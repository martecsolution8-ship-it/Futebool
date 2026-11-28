import hashlib
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

for _candidate in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[3]):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from app.core.config import get_settings
from app.intelligence import immutability  # noqa: F401  (regista a proteção dos palpites publicados)
from app.intelligence.odds_lookup import quote_for
from app.models.intelligence import (
    BacktestRun,
    EngineRun,
    EngineRunStatus,
    EvaluationMetric,
    FeatureSnapshot,
    ModelStatus,
    ModelVersion,
    Prediction,
    PredictionStatus,
    TrainingRun,
)
from app.models.sports import Fixture, FixtureStatus
from intelligence.confidence import agreement_score, classify_confidence
from intelligence.explain import build_explanation
from intelligence.features.feature_builder import HistoricalIndex, head_to_head, missing_quality, team_history
from intelligence.markets import (
    BINARY_EVENTS,
    PUSH,
    actual_outcome,
    analysis_eligibility,
    base_rate_for,
    build_market_estimates,
    settle_selection,
    settlement_state,
)
from intelligence.models.calibration import apply_platt
from intelligence.models.statistical_models import EloTable
from intelligence.models.team_strength import fit_strength_model, market_probabilities
from intelligence.picks import PickCandidate, choose_pick, dedupe_matches, pick_block_reason
from intelligence.top8 import RankedPrediction, select_top8
from intelligence.value import assess_value
from app.intelligence.strength_pipeline import MIN_MATCHES_PER_TEAM, competition_base_rates, run_backtest

# Calibração dos mercados binários que usam o complemento do lado "mais de".
_CALIBRATION_KEY = {"under_25": "over_25", "under_35": "over_35"}
_STRENGTH_CACHE: dict[str, object] = {}   # um único ajuste em memória: evita reajustar com os mesmos dados
_OPERATION_LOCK = threading.RLock()
_ADVISORY_LOCK_KEY = 734001
_MAX_REFUSED = 500


def serialized_engine_operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with _OPERATION_LOCK:
            advisory_acquired = False
            bind = getattr(self.db, "bind", None)
            dialect = getattr(bind, "dialect", None)
            if dialect is not None and dialect.name == "postgresql":
                advisory_acquired = bool(self.db.scalar(text(
                    "SELECT pg_try_advisory_lock(:key)"
                ), {"key": _ADVISORY_LOCK_KEY}))
                if not advisory_acquired:
                    raise RuntimeError("Outra execução do motor MarBet já está em curso.")
            try:
                return method(self, *args, **kwargs)
            finally:
                if advisory_acquired:
                    self.db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    return guarded


def future_window_end(now: datetime, days: int) -> datetime:
    return now + timedelta(days=max(1, int(days)))


def fixture_priority(kickoff_at: datetime, timezone_name: str = "Africa/Luanda") -> tuple[int, datetime]:
    kickoff = kickoff_at if kickoff_at.tzinfo is not None else kickoff_at.replace(tzinfo=timezone.utc)
    try:
        operational = kickoff.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError):
        operational = kickoff
    rank = 0 if operational.weekday() == 4 else 1 if operational.weekday() == 5 else 2 if operational.weekday() == 6 else 3
    return rank, kickoff


def rows_fingerprint(rows: list[dict]) -> str:
    """Impressão digital dos jogos terminados: se não mudou, não há motivo para refazer o backtest/ajuste."""
    digest = hashlib.sha1()
    for r in rows:
        digest.update(f"{r['kickoff_at'].isoformat()}|{r['home_team_id']}|{r['away_team_id']}|{r['home_score']}|{r['away_score']};".encode())
    return digest.hexdigest()


class IntelligenceEngine:
    feature_version = "features-v1"

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    model_code = "strength-dc"
    model_label = "2.1.0"

    def _model_version(self) -> ModelVersion:
        model = self.db.scalar(select(ModelVersion).where(ModelVersion.code == self.model_code, ModelVersion.version == self.model_label))
        if model is None:
            model = ModelVersion(
                code=self.model_code,
                version=self.model_label,
                status=ModelStatus.DEVELOPMENT,
                algorithm="Dixon-Coles regularizado (ataque/defesa/casa, rho estimado) + Elo + calibracao validada",
                parameters={"half_life_days": 240.0, "ridge": 6.0, "max_goals": 10},
            )
            self.db.add(model)
            for old in self.db.scalars(select(ModelVersion).where(ModelVersion.code == "poisson-elo")).all():
                old.status = ModelStatus.RETIRED
            for old in self.db.scalars(select(ModelVersion).where(ModelVersion.code == self.model_code, ModelVersion.version != self.model_label)).all():
                old.status = ModelStatus.RETIRED
            self.db.commit()
            self.db.refresh(model)
        return model

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _fixture_dict(self, fixture: Fixture) -> dict:
        return {
            "home_team_id": str(fixture.home_team_id),
            "away_team_id": str(fixture.away_team_id),
            "competition_id": str(fixture.competition_id),
            "kickoff_at": self._aware(fixture.kickoff_at),
            "home_score": fixture.home_score,
            "away_score": fixture.away_score,
        }

    def _dedupe(self, fixtures: list[Fixture]) -> list[Fixture]:
        """O mesmo jogo vindo de dois fornecedores conta uma só vez (evita contar resultados a dobrar)."""
        # ordem estável: com empate, fica sempre o mesmo registo entre ciclos (não troca o jogo "escolhido")
        fixtures = sorted(fixtures, key=lambda f: (str(f.provider), str(f.provider_fixture_id)))
        return dedupe_matches(
            fixtures,
            home=lambda f: f.home_team_id,
            away=lambda f: f.away_team_id,
            kickoff=lambda f: self._aware(f.kickoff_at),
            rank=lambda f: 1.0 if f.home_score is not None and f.away_score is not None else 0.0,
        )

    def _finished_fixtures(self) -> list[Fixture]:
        return self._dedupe(list(self.db.scalars(
            select(Fixture).where(
                Fixture.status == FixtureStatus.FINISHED,
                Fixture.home_score.is_not(None),
                Fixture.away_score.is_not(None),
            ).order_by(Fixture.kickoff_at.asc())
        ).all()))

    def build_feature_snapshot(self, fixture: Fixture, all_fixtures: list[dict] | None = None, commit: bool = True, existing_snapshots: dict | None = None) -> FeatureSnapshot:
        existing = existing_snapshots.get(fixture.id) if existing_snapshots is not None else self.db.scalar(select(FeatureSnapshot).where(FeatureSnapshot.fixture_id == fixture.id))
        if all_fixtures is None:
            all_fixtures = [self._fixture_dict(item) for item in self._finished_fixtures()]
        cutoff = self._aware(fixture.kickoff_at)
        if isinstance(all_fixtures, HistoricalIndex):
            historical = all_fixtures
            home = historical.team_history(str(fixture.home_team_id), cutoff, 10)
            away = historical.team_history(str(fixture.away_team_id), cutoff, 10)
            h2h = historical.head_to_head(str(fixture.home_team_id), str(fixture.away_team_id), cutoff)
        else:
            home = team_history(all_fixtures, str(fixture.home_team_id), cutoff, 10)
            away = team_history(all_fixtures, str(fixture.away_team_id), cutoff, 10)
            h2h = head_to_head(all_fixtures, str(fixture.home_team_id), str(fixture.away_team_id), cutoff)
        quality = missing_quality(home, away)
        values = {
            "home_matches": home.matches,
            "home_matches_5": home.matches_5,
            "home_matches_10": home.matches_10,
            "home_wins": home.wins,
            "home_draws": home.draws,
            "home_losses": home.losses,
            "home_goals_for_avg": home.goals_for / home.matches if home.matches else 0.0,
            "home_goals_against_avg": home.goals_against / home.matches if home.matches else 0.0,
            "home_over_15_pct": home.over_15_pct,
            "home_over_25_pct": home.over_25_pct,
            "home_btts_pct": home.btts_pct,
            "home_clean_sheet_pct": home.clean_sheet_pct,
            "home_venue_matches": home.home_matches,
            "home_venue_goals_for_avg": home.home_goals_for_avg,
            "home_venue_goals_against_avg": home.home_goals_against_avg,
            "away_matches": away.matches,
            "away_matches_5": away.matches_5,
            "away_matches_10": away.matches_10,
            "away_wins": away.wins,
            "away_draws": away.draws,
            "away_losses": away.losses,
            "away_goals_for_avg": away.goals_for / away.matches if away.matches else 0.0,
            "away_goals_against_avg": away.goals_against / away.matches if away.matches else 0.0,
            "away_over_15_pct": away.over_15_pct,
            "away_over_25_pct": away.over_25_pct,
            "away_btts_pct": away.btts_pct,
            "away_clean_sheet_pct": away.clean_sheet_pct,
            "away_venue_matches": away.away_matches,
            "away_venue_goals_for_avg": away.away_goals_for_avg,
            "away_venue_goals_against_avg": away.away_goals_against_avg,
            "home_rest_days": home.rest_days,
            "away_rest_days": away.rest_days,
            "h2h_matches": len(h2h),
            "h2h_home_wins": sum(
                (f["home_score"] > f["away_score"] if str(f["home_team_id"]) == str(fixture.home_team_id)
                 else f["away_score"] > f["home_score"])
                for f in h2h
            ),
            "h2h_draws": sum(f["home_score"] == f["away_score"] for f in h2h),
            "h2h_away_wins": sum(
                (f["away_score"] > f["home_score"] if str(f["home_team_id"]) == str(fixture.home_team_id)
                 else f["home_score"] > f["away_score"])
                for f in h2h
            ),
            "xg_available": bool(fixture.statistics.get("xg") or fixture.statistics.get("xG") or fixture.statistics.get("expected_goals")) if isinstance(fixture.statistics, dict) else False,
        }
        missing = {key: True for key, value in values.items() if value is None}
        if existing:
            existing.values = values
            existing.data_quality = quality
            existing.missing_features = missing
            snapshot = existing
        else:
            snapshot = FeatureSnapshot(
                fixture_id=fixture.id,
                feature_version=self.feature_version,
                values=values,
                data_quality=quality,
                missing_features=missing,
            )
            self.db.add(snapshot)
        if commit:
            self.db.commit()
        return snapshot

    # ------------------------------------------------------------------ utilitários

    def _strength(self, rows: list[dict], now: datetime):
        """Ajuste Dixon-Coles com cache em memória (os mesmos jogos terminados dão sempre o mesmo ajuste)."""
        fingerprint = rows_fingerprint(rows)
        if _STRENGTH_CACHE.get("fingerprint") == fingerprint:
            return _STRENGTH_CACHE.get("model")
        model = fit_strength_model(rows, now)
        _STRENGTH_CACHE.clear()
        _STRENGTH_CACHE.update({"fingerprint": fingerprint, "model": model})
        return model

    @staticmethod
    def _calibration_state(validation_key: str, calibration: dict, market_validation: dict) -> str:
        """VALIDATED: calibração confirmada em dados novos | FITTED: ajustada mas não confirmada | NONE."""
        if validation_key == "1X2":
            v = market_validation.get("1X2") or {}
            ece = v.get("ece")
            if v.get("validated") and v.get("samples", 0) >= 100 and ece is not None and ece <= 0.05:
                return "VALIDATED"
            return "FITTED" if v else "NONE"
        cal = calibration.get(_CALIBRATION_KEY.get(validation_key, validation_key))
        if not cal:
            return "NONE"
        return "VALIDATED" if cal.get("validated") else "FITTED"

    @staticmethod
    def _agreement(estimate, dc: list[float], elo: list[float], calibrated_applied: bool) -> float:
        """Concordância entre modelos: Dixon-Coles vs Elo (1X2 e derivados); bruto vs calibrado nos restantes."""
        if estimate.market == "1X2":
            i = {"HOME": 0, "DRAW": 1, "AWAY": 2}[estimate.selection]
            return agreement_score(dc[i], elo[i])
        if estimate.market == "DOUBLE_CHANCE":
            idx = {"1X": (0, 1), "X2": (1, 2), "12": (0, 2)}[estimate.selection]
            return agreement_score(sum(dc[i] for i in idx), sum(elo[i] for i in idx))
        if estimate.market == "DRAW_NO_BET":
            i = 0 if estimate.selection == "HOME" else 2
            dc_den, elo_den = dc[0] + dc[2], elo[0] + elo[2]
            return agreement_score(dc[i] / dc_den if dc_den else None, elo[i] / elo_den if elo_den else None)
        if calibrated_applied:
            return agreement_score(estimate.raw_probability, estimate.probability, tolerance=0.10)
        return 0.5

    # ------------------------------------------------------------------ treino e backtest

    @serialized_engine_operation
    def train_and_backtest(self, force: bool = False) -> EngineRun:
        run = EngineRun(operation="train_backtest", status=EngineRunStatus.RUNNING)
        self.db.add(run)
        model = self._model_version()
        run.model_version_id = model.id
        self.db.commit()

        rows = [self._fixture_dict(item) for item in self._finished_fixtures()]
        min_history = self.settings.intelligence_min_history_matches
        model_inputs = {
            key: value for key, value in (model.parameters or {}).items()
            if key not in {"backtest_fingerprint", "backtested_at", "trained_at"}
        }
        fingerprint_payload = (
            f"{rows_fingerprint(rows)}:{self.model_code}:{self.model_label}:{self.feature_version}:"
            f"{self.settings.intelligence_min_market_gain}:{self.settings.intelligence_min_market_samples}:"
            f"{min_history}:{self.settings.intelligence_future_window_days}:"
            f"{json.dumps(model_inputs, sort_keys=True, default=str)}"
        )
        fingerprint = hashlib.sha1(fingerprint_payload.encode()).hexdigest()

        # Mesmos jogos e mesmos critérios desde o último backtest: não há nada novo para aprender.
        if not force and (model.parameters or {}).get("backtest_fingerprint") == fingerprint:
            run.status = EngineRunStatus.SUCCESS
            run.records_processed = len(rows)
            run.metadata_json = {"skipped": True, "reason": "Sem jogos novos desde o último backtest."}
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        training = TrainingRun(model_version_id=model.id, status="RUNNING")
        self.db.add(training)

        def _insufficient(message: str) -> EngineRun:
            training.status = "INSUFFICIENT_DATA"
            training.error_message = message
            training.completed_at = datetime.now(timezone.utc)
            run.status = EngineRunStatus.PARTIAL
            run.records_processed = len(rows)
            run.error_count = 1
            run.error_message = message
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        if len(rows) < min_history:
            return _insufficient(f"{len(rows)} finished matches available; minimum is {min_history}")

        try:
            result = run_backtest(
                rows, min_history,
                min_gain=self.settings.intelligence_min_market_gain,
                min_market_samples=self.settings.intelligence_min_market_samples,
            )
            if result is None:
                return _insufficient(f"{len(rows)} finished matches are not enough for a reliable chronological backtest")

            now = datetime.now(timezone.utc)
            params = dict(model.parameters or {})
            params.update({
                "calibration": result["calibration"],
                "elo_weight": result["elo_weight"],
                "skill": result["skill"],
                "market_validation": result["market_validation"],
                "backtest_matches": result["test_matches"],
                "backtested_at": now.isoformat(),
                "backtest_fingerprint": fingerprint,
                "min_market_gain": self.settings.intelligence_min_market_gain,
                "min_market_samples": self.settings.intelligence_min_market_samples,
            })
            model.parameters = params
            model.trained_at = now
            model.data_from = rows[0]["kickoff_at"]
            model.data_to = rows[-1]["kickoff_at"]
            model.status = ModelStatus.VALIDATED if result["skill"] >= 0.6 else ModelStatus.DEVELOPMENT

            self.db.execute(delete(EvaluationMetric).where(EvaluationMetric.model_version_id == model.id))
            for market, m in result["metrics"].items():
                self.db.add(EvaluationMetric(
                    model_version_id=model.id,
                    market=market,
                    sample_count=m["sample_count"],
                    brier_score=m["brier_score"],
                    log_loss=m["log_loss"],
                    accuracy=m["accuracy"],
                    calibration={
                        "bins": m["calibration"],
                        "baseline_log_loss": m["baseline_log_loss"],
                        "beats_baseline": m["beats_baseline"],
                        "log_loss_gain": m.get("log_loss_gain"),
                        "ece": m.get("ece"),
                        "validated": result["market_validation"].get(market, {}).get("validated"),
                        "platt": result["calibration"].get(market),
                    },
                    period_from=rows[0]["kickoff_at"],
                    period_to=rows[-1]["kickoff_at"],
                ))

            one_x_two = result["metrics"]["1X2"]
            self.db.add(BacktestRun(
                model_version_id=model.id,
                initial_train_matches=result["initial_train"],
                test_matches=result["test_size"],
                folds=result["folds"],
                summary={
                    "finished_matches": len(rows),
                    "tested_matches": result["test_matches"],
                    "elo_weight": result["elo_weight"],
                    "skill": result["skill"],
                    "brier_score": one_x_two["brier_score"],
                    "log_loss": one_x_two["log_loss"],
                    "accuracy_1x2": one_x_two["accuracy"],
                    "baseline_log_loss_1x2": one_x_two["baseline_log_loss"],
                    "ece_1x2": one_x_two.get("ece"),
                    "markets": {k: {"log_loss": v["log_loss"], "baseline_log_loss": v["baseline_log_loss"],
                                     "accuracy": v["accuracy"], "beats_baseline": v["beats_baseline"],
                                     "log_loss_gain": v.get("log_loss_gain")}
                                for k, v in result["metrics"].items()},
                    "market_validation": result["market_validation"],
                    "note": "Out-of-sample chronological backtest of the published model itself (no future data leakage).",
                },
            ))

            training.status = "SUCCESS"
            training.train_samples = result["initial_train"]
            training.validation_samples = 0
            training.test_samples = result["test_matches"]
            training.completed_at = now

            run.records_processed = len(rows)
            run.completed_at = now
            weak = [k for k, v in result["market_validation"].items() if not v["validated"]]
            run.metadata_json = {"validated_markets": [k for k, v in result["market_validation"].items() if v["validated"]],
                                 "unvalidated_markets": weak}
            if weak:
                run.status = EngineRunStatus.PARTIAL
                run.error_count = len(weak)
                run.error_message = "Mercados sem vantagem validada sobre a média histórica (não originam palpites): " + ", ".join(weak)
            else:
                run.status = EngineRunStatus.SUCCESS
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            training.status = "FAILED"
            training.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            training.completed_at = datetime.now(timezone.utc)
            run.status = EngineRunStatus.FAILED
            run.error_count = 1
            run.error_message = training.error_message
            run.completed_at = datetime.now(timezone.utc)
            self.db.add_all([training, run])
            self.db.commit()
        return run

    # ------------------------------------------------------------------ geração

    @serialized_engine_operation
    def _assess_with_market_odds(self, fixture_id, market: str, selection: str, probability: float) -> dict:
        """Valor esperado com odds REAIS guardadas (fixture_odds); sem odds recentes não afirma valor."""
        cache = self.__dict__.setdefault("_odds_cache", {})
        quote = quote_for(self.db, cache, fixture_id, market, selection)
        assessment = assess_value(probability, quote["price"] if quote else None, self.settings.intelligence_min_ev)
        if quote:
            assessment["quote"] = quote
        return assessment

    def generate_predictions(self) -> EngineRun:
        run = EngineRun(operation="generate_predictions", status=EngineRunStatus.RUNNING)
        model = self._model_version()
        run.model_version_id = model.id
        self.db.add(run)
        self.db.commit()

        finished = self._finished_fixtures()
        rows = [self._fixture_dict(item) for item in finished]
        finished_count = len(rows)
        now = datetime.now(timezone.utc)
        strength = self._strength(rows, now) if finished_count >= self.settings.intelligence_min_history_matches else None
        if strength is None:
            run.status = EngineRunStatus.PARTIAL
            run.records_processed = finished_count
            run.error_count = 1
            run.error_message = (
                f"Insufficient historical matches: {finished_count} available; "
                f"{self.settings.intelligence_min_history_matches} required"
            )
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        params = model.parameters or {}
        elo_weight = float(params.get("elo_weight", 0.15))
        calibration = params.get("calibration") or {}
        market_validation = params.get("market_validation") or {}
        skill = float(params.get("skill", 0.0))
        sources = sorted({str(item.provider) for item in finished})
        synced = [self._aware(item.last_synced_at) for item in finished if item.last_synced_at is not None]
        data_updated_at = max(synced).isoformat() if synced else None
        global_base, comp_base = competition_base_rates(rows)

        historical_index = HistoricalIndex(rows)

        elo = EloTable()
        for r in rows:
            elo.update(r["home_team_id"], r["away_team_id"], r["home_score"], r["away_score"])

        window_end = future_window_end(now, self.settings.intelligence_future_window_days)
        scheduled = self._dedupe(list(self.db.scalars(
            select(Fixture).where(
                Fixture.status == FixtureStatus.SCHEDULED,
                Fixture.kickoff_at > now,
                Fixture.kickoff_at <= window_end,
            ).order_by(Fixture.kickoff_at.asc())
        ).all()))
        scheduled.sort(key=lambda fixture: fixture_priority(fixture.kickoff_at, self.settings.timezone))

        created = updated = 0
        published_new = published_kept = no_pick = 0
        block_counts: dict[str, int] = {}
        refused: list[dict] = []
        candidates: list[RankedPrediction] = []
        try:
            # Previsões pendentes de modelos antigos deixam de valer — EXCETO as já publicadas,
            # que nunca são anuladas por mudança de versão: são liquidadas com o resultado real.
            for stale in self.db.scalars(
                select(Prediction).join(Fixture, Fixture.id == Prediction.fixture_id).where(
                    Prediction.status == PredictionStatus.PENDING,
                    Prediction.model_version_id != model.id,
                    Fixture.status == FixtureStatus.SCHEDULED,
                )
            ).all():
                if (stale.rationale or {}).get("published_pick"):
                    continue
                stale.status = PredictionStatus.VOID

            existing: dict = {}
            existing_snapshots: dict = {}
            if scheduled:
                for snapshot in self.db.scalars(select(FeatureSnapshot).where(
                    FeatureSnapshot.fixture_id.in_([fixture.id for fixture in scheduled])
                )).all():
                    existing_snapshots[snapshot.fixture_id] = snapshot
            published_anywhere: set = set()   # jogos com palpite publicado por QUALQUER versão do modelo
            if scheduled:
                for item in self.db.scalars(select(Prediction).where(
                    Prediction.fixture_id.in_([f.id for f in scheduled]),
                    Prediction.status == PredictionStatus.PENDING,
                )).all():
                    if item.model_version_id == model.id:
                        existing[(item.fixture_id, item.market, item.selection)] = item
                    if (item.rationale or {}).get("published_pick"):
                        published_anywhere.add(item.fixture_id)

            model_name = f"{self.model_code} {self.model_label}"
            snapshot_count = 0
            batch_size = max(1, int(self.settings.intelligence_feature_batch_size))
            for fixture in scheduled:
                allowed, reason = analysis_eligibility(fixture.status, fixture.kickoff_at, now)
                if not allowed:
                    refused.append({"fixture_id": str(fixture.id), "reason": reason})
                    continue
                home_id, away_id, comp = str(fixture.home_team_id), str(fixture.away_team_id), str(fixture.competition_id)
                home_n = strength.matches_per_team.get(home_id, 0)
                away_n = strength.matches_per_team.get(away_id, 0)
                quality = min(min(home_n, away_n) / 12.0, 1.0)  # a equipa com menos jogos limita a qualidade
                if min(home_n, away_n) < MIN_MATCHES_PER_TEAM or quality < self.settings.intelligence_top8_min_data_quality:
                    refused.append({"fixture_id": str(fixture.id), "reason": "DADOS_INSUFICIENTES",
                                    "home_matches": home_n, "away_matches": away_n})
                    continue
                base = comp_base.get(comp, global_base)
                if not base:
                    refused.append({"fixture_id": str(fixture.id), "reason": "DADOS_INSUFICIENTES"})
                    continue

                snapshot = self.build_feature_snapshot(fixture, historical_index, commit=False, existing_snapshots=existing_snapshots)
                snapshot_count += 1
                if snapshot_count % batch_size == 0:
                    self.db.commit()
                form = {k: v for k, v in (snapshot.values or {}).items() if k != "xg_available"}
                matrix = strength.score_matrix(home_id, away_id, comp)
                mp = market_probabilities(matrix)
                lam_home, lam_away = strength.lambdas(home_id, away_id, comp)
                elo_p = list(elo.probability(home_id, away_id))
                dc_p = [mp["home_win"], mp["draw"], mp["away_win"]]
                blend = [(1 - elo_weight) * d + elo_weight * e for d, e in zip(dc_p, elo_p)]
                total = sum(blend)
                blend = [value / total for value in blend]
                best_score = divmod(int(matrix.argmax()), matrix.shape[1])

                # Só se aplica calibração confirmada em dados novos; senão fica a probabilidade do modelo.
                calibrated_probs = {}
                for key in BINARY_EVENTS:
                    cal = calibration.get(_CALIBRATION_KEY.get(key, key))
                    if key in mp and cal and cal.get("validated") and key not in _CALIBRATION_KEY:
                        calibrated_probs[key] = apply_platt(mp[key], cal)
                estimates = build_market_estimates(mp, tuple(blend), calibrated_probs)

                locked = fixture.id in published_anywhere
                fixture_rows: dict[tuple[str, str], Prediction] = {}
                pick_candidates: list[tuple[PickCandidate, object, dict]] = []
                for est in estimates:
                    probability = float(max(0.0, min(1.0, est.probability)))
                    base_rate = base_rate_for(est.market, est.selection, base)
                    if probability < 0.05 or base_rate is None:
                        continue
                    validation = market_validation.get(est.validation_key) or {}
                    validated = bool(validation.get("validated"))
                    cal_state = self._calibration_state(est.validation_key, calibration, market_validation)
                    calibrated_applied = est.validation_key in calibrated_probs or (
                        est.validation_key in ("under_25", "under_35") and _CALIBRATION_KEY[est.validation_key] in calibrated_probs)
                    edge = probability - base_rate
                    conf = classify_confidence(
                        data_quality=quality,
                        market_validated=validated,
                        validation_gain=validation.get("gain"),
                        validation_samples=int(validation.get("samples", 0)),
                        calibration_state=cal_state,
                        model_agreement=self._agreement(est, dc_p, elo_p, calibrated_applied),
                        edge=edge,
                    )
                    explanation = build_explanation(
                        market=est.market, selection=est.selection, probability=probability, base_rate=base_rate,
                        expected_goals=(lam_home, lam_away), home_matches=home_n, away_matches=away_n, form=form,
                        validation={"validated": validated, "samples": int(validation.get("samples", 0)),
                                    "gain": float(validation.get("gain") or 0.0)} if validation else None,
                        calibration_state=cal_state,
                        model_agreement=self._agreement(est, dc_p, elo_p, calibrated_applied),
                    )
                    rationale = {
                        "method": "Dixon-Coles regularizado + Elo + calibracao validada",
                        "model": model_name,
                        "model_version": self.model_label,
                        "analysed_at": now.isoformat(),
                        "data_sources": sources,
                        "data_updated_at": data_updated_at,
                        "finished_matches": finished_count,
                        "home_matches": home_n,
                        "away_matches": away_n,
                        "expected_goals_home": round(lam_home, 2),
                        "expected_goals_away": round(lam_away, 2),
                        "most_likely_score": f"{best_score[0]}-{best_score[1]}",
                        "dixon_coles_rho": round(float(strength.rho), 4),
                        "elo_weight": elo_weight,
                        "raw_probability": round(float(est.raw_probability), 4),
                        "base_rate": round(float(base_rate), 4),
                        "edge": round(edge, 4),
                        "calibrated": cal_state == "VALIDATED",
                        "calibration_state": cal_state,
                        "market_validated": validated,
                        "market_validation": {k: validation.get(k) for k in ("samples", "gain", "log_loss", "baseline_log_loss")},
                        "confidence_level": conf.level,
                        "confidence_label": conf.label,
                        "confidence_reasons": conf.reasons,
                        "summary": explanation["summary"],
                        "factors_for": explanation["factors_for"],
                        "risks": explanation["risks"],
                        "odds": self._assess_with_market_odds(fixture.id, est.market, est.selection, probability),
                        "feature_version": self.feature_version,
                        "form": form,
                    }
                    key = (fixture.id, est.market, est.selection)
                    prediction = existing.get(key)
                    if prediction is not None:
                        old = prediction.rationale or {}
                        if old.get("published_pick"):
                            # Prognóstico já publicado: não é alterado retroativamente.
                            fixture_rows[(est.market, est.selection)] = prediction
                            continue
                        rationale["first_probability"] = old.get("first_probability", round(prediction.probability, 4))
                        rationale["first_published_at"] = old.get("first_published_at", prediction.generated_at.isoformat() if prediction.generated_at else now.isoformat())
                        prediction.probability = probability
                        prediction.confidence = conf.score
                        prediction.data_quality = quality
                        prediction.rationale = rationale
                        prediction.generated_at = now
                        updated += 1
                    else:
                        rationale["first_probability"] = round(probability, 4)
                        rationale["first_published_at"] = now.isoformat()
                        prediction = Prediction(
                            fixture_id=fixture.id,
                            model_version_id=model.id,
                            market=est.market,
                            selection=est.selection,
                            probability=probability,
                            confidence=conf.score,
                            data_quality=quality,
                            rationale=rationale,
                        )
                        self.db.add(prediction)
                        created += 1
                    fixture_rows[(est.market, est.selection)] = prediction
                    candidate = PickCandidate(est.market, est.selection, probability, float(base_rate), conf.score, quality)
                    pick_candidates.append((candidate, est, {"validated": validated, "conf": conf, "explanation": explanation,
                                                              "base_rate": base_rate, "edge": edge, "raw": est.raw_probability}))

                thresholds = dict(
                    min_probability=self.settings.intelligence_pick_min_probability,
                    min_edge=self.settings.intelligence_pick_min_edge,
                    min_quality=self.settings.intelligence_top8_min_data_quality,
                )
                reasons: dict[tuple[str, str], str | None] = {}
                eligible: list[PickCandidate] = []
                for candidate, est, info in pick_candidates:
                    reason = pick_block_reason(candidate, market_validated=info["validated"], **thresholds)
                    reasons[(candidate.market, candidate.selection)] = reason
                    if reason is None:
                        eligible.append(candidate)
                        candidates.append(RankedPrediction(
                            str(fixture.id), candidate.market, candidate.selection, candidate.probability,
                            candidate.confidence, candidate.data_quality, candidate.edge,
                        ))

                if locked:
                    published_kept += 1
                    continue

                # Um único prognóstico publicado por jogo (nunca mais de um, nunca contraditório).
                chosen = choose_pick(eligible, **thresholds) if eligible else None
                for candidate, est, info in pick_candidates:
                    row = fixture_rows[(candidate.market, candidate.selection)]
                    is_chosen = chosen is not None and (candidate.market, candidate.selection) == (chosen.market, chosen.selection)
                    if is_chosen:
                        published_at = now.isoformat()
                        frozen = {
                            "market": candidate.market, "selection": candidate.selection,
                            "probability": round(candidate.probability, 4), "raw_probability": round(float(info["raw"]), 4),
                            "confidence": info["conf"].score, "confidence_level": info["conf"].level,
                            "data_quality": round(quality, 4), "base_rate": round(float(info["base_rate"]), 4),
                            "edge": round(float(info["edge"]), 4), "summary": info["explanation"]["summary"],
                            "factors_for": info["explanation"]["factors_for"], "risks": info["explanation"]["risks"],
                            "model": model_name, "published_at": published_at, "kickoff_at": self._aware(fixture.kickoff_at).isoformat(),
                            "competition_id": comp, "home_team_id": home_id, "away_team_id": away_id,
                            "data_sources": sources, "data_updated_at": data_updated_at,
                        }
                        row.rationale = {**(row.rationale or {}), "published_pick": True, "published_at": published_at,
                                         "published_snapshot": frozen}
                        published_new += 1
                    else:
                        reason = reasons[(candidate.market, candidate.selection)] or "OUTRO_MERCADO_MELHOR"
                        row.rationale = {**(row.rationale or {}), "pick_block": reason}
                        block_counts[reason] = block_counts.get(reason, 0) + 1
                if chosen is None:
                    no_pick += 1

            self.db.commit()
            top8 = select_top8(candidates, self.settings.intelligence_top8_min_probability, self.settings.intelligence_top8_min_data_quality)
            run.status = EngineRunStatus.SUCCESS
            run.records_processed = len(scheduled)
            run.records_created = created
            run.metadata_json = {
                "updated": updated,
                "model_version": self.model_label,
                "analyzed": len(scheduled) - len(refused),
                "insufficient_or_refused": len(refused),
                "published_new": published_new,
                "published_kept": published_kept,
                "no_pick": no_pick,
                "pick_blocks": block_counts,
                "refused": refused[:_MAX_REFUSED],
                "validated_markets": sorted(k for k, v in market_validation.items() if v.get("validated")),
                "top8_fixture_count": len(set(item.fixture_id for item in top8)),
                "top8_predictions": len(top8),
                "calibrated": any(c and c.get("validated") for c in calibration.values()),
                "elo_weight": elo_weight,
                "data_sources": sources,
                "data_updated_at": data_updated_at,
                "future_window_days": int(self.settings.intelligence_future_window_days),
                "feature_batch_size": batch_size,
                "finished_matches": finished_count,
                "eligible_fixtures": len(scheduled),
                "feature_snapshots_processed": snapshot_count,
                "note": None if market_validation else "Sem backtest: nenhum mercado validado, por isso não são publicados palpites.",
            }
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run.status = EngineRunStatus.FAILED
            run.error_count = 1
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            run.completed_at = datetime.now(timezone.utc)
            self.db.add(run)
            self.db.commit()
        return run

    # ------------------------------------------------------------------ liquidação

    @serialized_engine_operation
    def settle_predictions(self) -> EngineRun:
        run = EngineRun(operation="settle_predictions", status=EngineRunStatus.RUNNING)
        self.db.add(run)
        self.db.commit()
        settled = voided = unrecognized = 0
        try:
            # Só palpites PUBLICADOS e ainda PENDENTES: o que já foi liquidado nunca é liquidado outra vez.
            predictions = [p for p in self.db.scalars(select(Prediction).where(Prediction.status == PredictionStatus.PENDING)).all()
                           if (p.rationale or {}).get("published_pick")]
            for prediction in predictions:
                fixture = self.db.get(Fixture, prediction.fixture_id)
                if fixture is None:
                    continue
                state = settlement_state(fixture.status, fixture.home_score, fixture.away_score)
                if state == "VOID":
                    # Adiado/cancelado/suspenso: nunca é contado como acerto nem erro.
                    prediction.status = PredictionStatus.VOID
                    prediction.actual_outcome = "VOID"
                    prediction.is_correct = None
                    prediction.settled_at = None
                    voided += 1
                    continue
                if state != "READY":
                    continue
                verdict = settle_selection(prediction.market, prediction.selection, fixture.home_score, fixture.away_score)
                if verdict is None:
                    unrecognized += 1   # mercado desconhecido: fica pendente em vez de ser classificado à sorte
                    continue
                prediction.actual_outcome = actual_outcome(prediction.market, prediction.selection, fixture.home_score, fixture.away_score)
                prediction.settled_at = datetime.now(timezone.utc)
                if verdict == PUSH:
                    # Empate anula aposta com empate: reembolso, não é acerto nem erro.
                    prediction.status = PredictionStatus.VOID
                    prediction.is_correct = None
                else:
                    prediction.status = PredictionStatus.SETTLED
                    prediction.is_correct = verdict == "WIN"
                    settled += 1
            self.db.commit()
            run.status = EngineRunStatus.SUCCESS
            run.records_processed = len(predictions)
            run.records_created = settled
            run.metadata_json = {"settled": settled, "voided": voided, "unrecognized": unrecognized}
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run.status = EngineRunStatus.FAILED
            run.error_count = 1
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        return run
