import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.intelligence.engine import IntelligenceEngine
from app.models.intelligence import BacktestRun, EngineRun, EngineRunStatus, EvaluationMetric, ModelVersion, Prediction, PredictionStatus
from app.models.sports import Competition, Fixture, FixtureStatus, SyncRun, SyncStatus
from app.models.user import UserRole
from intelligence.evaluation import SettledPick, evaluate
from intelligence.markets import NO_PICK_MESSAGE, REFUSAL_MESSAGES, analysis_eligibility, market_label, selection_label
from intelligence.picks import dedupe_matches, summarize_settled

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _prediction_dict(p: Prediction) -> dict:
    """Palpite/estimativa para o frontend, com rótulos já em português."""
    return {
        "id": p.id, "fixture_id": p.fixture_id, "model_version_id": p.model_version_id,
        "market": p.market, "selection": p.selection,
        "market_label": market_label(p.market), "selection_label": selection_label(p.market, p.selection),
        "probability": p.probability, "confidence": p.confidence, "data_quality": p.data_quality,
        "status": p.status, "actual_outcome": p.actual_outcome, "is_correct": p.is_correct,
        "generated_at": p.generated_at, "settled_at": p.settled_at,
        "is_official_pick": bool((p.rationale or {}).get("published_pick")),
        "rationale": p.rationale or {},
    }


def _run_dict(run: EngineRun | None) -> dict | None:
    if run is None:
        return None
    return {"id": run.id, "operation": run.operation, "status": run.status, "records_processed": run.records_processed,
            "records_created": run.records_created, "error_count": run.error_count, "error_message": run.error_message,
            "started_at": run.started_at, "completed_at": run.completed_at, "metadata": run.metadata_json or {}}


def _published_probability(prediction: Prediction) -> float:
    rationale = prediction.rationale or {}
    snapshot = rationale.get("published_snapshot") or {}
    if snapshot.get("probability") is not None:
        return float(snapshot["probability"])
    if rationale.get("first_probability") is not None:
        return float(rationale["first_probability"])
    return float(prediction.probability)


def _settled_picks(db: Session) -> list[SettledPick]:
    """Palpites PUBLICADOS e liquidados (acerto/erro), com a probabilidade ORIGINAL publicada."""
    query = (
        select(Prediction, Competition.name)
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .join(Competition, Competition.id == Fixture.competition_id)
        .where(Prediction.status == PredictionStatus.SETTLED)
    )
    result = []
    for prediction, competition_name in db.execute(query).all():
        rationale = prediction.rationale or {}
        if not rationale.get("published_pick") or prediction.is_correct is None:
            continue
        probability = _published_probability(prediction)
        result.append(SettledPick(prediction.market, competition_name or "Desconhecida", float(probability),
                                  bool(prediction.is_correct), _aware(prediction.settled_at), rationale.get("model")))
    return result


@router.get("/predictions")
def predictions(
    fixture_id: uuid.UUID | None = None,
    market: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = select(Prediction).order_by(Prediction.generated_at.desc()).limit(limit)
    if fixture_id:
        query = query.where(Prediction.fixture_id == fixture_id)
    if market:
        query = query.where(Prediction.market == market)
    return db.scalars(query).all()


def _is_pick(prediction: Prediction) -> bool:
    return bool((prediction.rationale or {}).get("published_pick"))


@router.get("/picks")
def picks(limit: int = Query(default=60, ge=1, le=200), db: Session = Depends(get_db)):
    """Prognósticos publicados: no máximo UM por jogo, só jogos futuros ainda por começar."""
    query = (
        select(Prediction, Fixture)
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .where(Prediction.status == PredictionStatus.PENDING)
        .where(Fixture.status == FixtureStatus.SCHEDULED, Fixture.kickoff_at > datetime.now(timezone.utc))
        .order_by(Fixture.kickoff_at.asc())
    )
    rows = [(prediction, fixture) for prediction, fixture in db.execute(query).all() if _is_pick(prediction)]
    # o mesmo jogo vindo de dois fornecedores aparece uma só vez
    rows = dedupe_matches(
        rows,
        home=lambda r: r[1].home_team_id,
        away=lambda r: r[1].away_team_id,
        kickoff=lambda r: r[1].kickoff_at if r[1].kickoff_at.tzinfo else r[1].kickoff_at.replace(tzinfo=timezone.utc),
    )
    return [prediction for prediction, _ in rows[:limit]]


@router.get("/performance")
def performance(db: Session = Depends(get_db)):
    """Desempenho real dos prognósticos PUBLICADOS e já resolvidos."""
    settled = db.scalars(select(Prediction).where(Prediction.status == PredictionStatus.SETTLED)).all()
    evaluated = [p for p in settled if _is_pick(p) and p.is_correct is not None]
    rows = [
        (p.market, _published_probability(p), bool(p.is_correct))
        for p in evaluated
    ]
    correct = sum(1 for p in evaluated if p.is_correct)
    total = len(evaluated)
    pending = db.scalars(select(Prediction).where(Prediction.status == PredictionStatus.PENDING)).all()
    pending_count = sum(1 for p in pending if _is_pick(p))
    return {
        "total_evaluated": total,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy_pct": round(correct / total * 100, 2) if total else None,
        "error_pct": round((total - correct) / total * 100, 2) if total else None,
        "pending": pending_count,
        "markets": summarize_settled(rows),
        "evaluation": evaluate(_settled_picks(db)),
        "note": "Só inclui prognósticos publicados e resolvidos. Jogos pendentes, adiados, cancelados e anulados não entram na taxa.",
    }


@router.get("/top8")
def top8(limit: int = Query(default=8, ge=1, le=8), db: Session = Depends(get_db)):
    query = (
        select(Prediction)
        .join(Fixture, Fixture.id == Prediction.fixture_id)
        .where(Prediction.status == PredictionStatus.PENDING)
        .where(Fixture.status == FixtureStatus.SCHEDULED, Fixture.kickoff_at > datetime.now(timezone.utc))
        .order_by(Prediction.confidence.desc(), Prediction.probability.desc())
    )
    # O TOP 8 é uma lista de recomendações: só entram palpites OFICIAIS (publicados após validação do mercado).
    predictions = [p for p in db.scalars(query).all() if _is_pick(p)]
    result = []
    used_fixtures = set()
    for prediction in predictions:
        if len(result) >= limit:
            break
        if prediction.fixture_id in used_fixtures:
            continue
        if prediction.data_quality < 0.40 or prediction.probability < 0.58:
            continue
        used_fixtures.add(prediction.fixture_id)
        result.append(prediction)
    return result


@router.get("/fixtures/{fixture_id}/probabilities")
def fixture_probabilities(fixture_id: uuid.UUID, db: Session = Depends(get_db)):
    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    predictions = db.scalars(
        select(Prediction).where(Prediction.fixture_id == fixture_id).order_by(Prediction.market, Prediction.selection)
    ).all()
    return {"fixture_id": fixture_id, "predictions": predictions}


@router.get("/fixtures/{fixture_id}/analysis")
def fixture_analysis(fixture_id: uuid.UUID, db: Session = Depends(get_db)):
    """Análise de um jogo: probabilidades gerais SEPARADAS da recomendação oficial do MarBet."""
    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    base = {"fixture_id": fixture.id, "fixture_status": fixture.status, "kickoff_at": fixture.kickoff_at}
    allowed, reason = analysis_eligibility(fixture.status, fixture.kickoff_at)
    if not allowed:
        # Jogos terminados, adiados, cancelados, suspensos ou já iniciados não são apresentados como previsão futura.
        return {**base, "analysable": False, "reason": reason, "message": REFUSAL_MESSAGES.get(reason),
                "probabilities": [], "official_pick": None}

    predictions = db.scalars(
        select(Prediction).where(Prediction.fixture_id == fixture_id, Prediction.status == PredictionStatus.PENDING)
        .order_by(Prediction.market, Prediction.selection)
    ).all()
    if not predictions:
        last = db.scalar(select(EngineRun).where(EngineRun.operation == "generate_predictions").order_by(EngineRun.started_at.desc()).limit(1))
        refusal = next((r for r in (last.metadata_json or {}).get("refused", []) if r.get("fixture_id") == str(fixture_id)), None) if last else None
        reason = (refusal or {}).get("reason", "DADOS_INSUFICIENTES")
        return {**base, "analysable": True, "reason": reason,
                "message": "Sem análise: " + REFUSAL_MESSAGES.get(reason, "dados insuficientes para uma análise fiável."),
                "probabilities": [], "official_pick": None}

    pick = next((p for p in predictions if _is_pick(p)), None)
    blocks: dict[str, int] = {}
    for p in predictions:
        reason = (p.rationale or {}).get("pick_block")
        if reason:
            blocks[reason] = blocks.get(reason, 0) + 1
    return {
        **base, "analysable": True, "reason": None,
        "official_pick": _prediction_dict(pick) if pick else None,
        "message": None if pick else NO_PICK_MESSAGE,
        "pick_blocks": blocks,
        "probabilities": [_prediction_dict(p) for p in predictions],
        "disclaimer": "Probabilidades são estimativas estatísticas, não garantias. Só o palpite oficial é uma recomendação do MarBet.",
    }


@router.get("/metrics")
def metrics(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    return db.scalars(select(EvaluationMetric).order_by(EvaluationMetric.created_at.desc()).limit(limit)).all()


@router.get("/models")
def models(db: Session = Depends(get_db)):
    return db.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc())).all()


@router.get("/backtests")
def backtests(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    return db.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)).all()


@router.get("/history")
def history(
    limit: int = Query(default=100, ge=1, le=500),
    picks_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    items = db.scalars(
        select(Prediction).where(Prediction.status == PredictionStatus.SETTLED).order_by(Prediction.settled_at.desc()).limit(limit * 8 if picks_only else limit)
    ).all()
    if picks_only:
        items = [item for item in items if _is_pick(item)]
        pending = db.scalars(
            select(Prediction)
            .where(Prediction.status == PredictionStatus.PENDING)
            .order_by(Prediction.generated_at.desc())
        ).all()
        items = items + [item for item in pending if _is_pick(item)]
    return items[:limit]


@router.post("/admin/train", status_code=status.HTTP_202_ACCEPTED)
def train(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN))):
    return IntelligenceEngine(db).train_and_backtest(force=True)


@router.post("/admin/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN))):
    return IntelligenceEngine(db).generate_predictions()


@router.post("/admin/settle", status_code=status.HTTP_202_ACCEPTED)
def settle(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN))):
    return IntelligenceEngine(db).settle_predictions()


@router.get("/runs")
def runs(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.ANALYST))):
    return db.scalars(select(EngineRun).order_by(EngineRun.started_at.desc()).limit(limit)).all()


@router.get("/admin/status")
def admin_status(db: Session = Depends(get_db), _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.ANALYST))):
    """Painel de controlo do motor: estado, última execução, jogos analisados/recusados, desempenho e erros."""
    model = db.scalar(select(ModelVersion).where(ModelVersion.code == IntelligenceEngine.model_code,
                                                 ModelVersion.version == IntelligenceEngine.model_label))
    last_runs = {}
    for operation in ("train_backtest", "generate_predictions", "settle_predictions"):
        last_runs[operation] = _run_dict(db.scalar(
            select(EngineRun).where(EngineRun.operation == operation).order_by(EngineRun.started_at.desc()).limit(1)))
    generation = (last_runs["generate_predictions"] or {}).get("metadata", {})

    pending = db.scalars(select(Prediction).where(Prediction.status == PredictionStatus.PENDING)).all()
    published_pending = sum(1 for p in pending if _is_pick(p))
    settled = _settled_picks(db)
    voided = sum(1 for p in db.scalars(select(Prediction).where(Prediction.status == PredictionStatus.VOID)).all() if _is_pick(p))

    errors = [_run_dict(r) for r in db.scalars(
        select(EngineRun).where(EngineRun.status.in_((EngineRunStatus.FAILED, EngineRunStatus.PARTIAL)))
        .order_by(EngineRun.started_at.desc()).limit(20)).all()]
    sync_errors = [{"provider": r.provider, "operation": r.operation, "error_message": r.error_message, "started_at": r.started_at}
                   for r in db.scalars(select(SyncRun).where(SyncRun.status == SyncStatus.FAILED)
                                       .order_by(SyncRun.started_at.desc()).limit(10)).all()]

    if model is None or model.trained_at is None:
        engine_state = "SEM_BACKTEST"
    elif (last_runs["generate_predictions"] or {}).get("status") == EngineRunStatus.FAILED:
        engine_state = "ERRO"
    else:
        engine_state = "OK"
    parameters = (model.parameters or {}) if model else {}
    return {
        "engine": {
            "state": engine_state,
            "model": None if model is None else {
                "code": model.code, "version": model.version, "status": model.status, "algorithm": model.algorithm,
                "trained_at": model.trained_at, "data_from": model.data_from, "data_to": model.data_to},
        },
        "last_runs": last_runs,
        "last_generation": {
            "analyzed_games": generation.get("analyzed"),
            "games_without_enough_data": generation.get("insufficient_or_refused"),
            "published_new": generation.get("published_new"),
            "published_kept": generation.get("published_kept"),
            "no_pick_games": generation.get("no_pick"),
            "pick_blocks": generation.get("pick_blocks", {}),
            "refused_sample": (generation.get("refused") or [])[:50],
            "data_sources": generation.get("data_sources"),
            "data_updated_at": generation.get("data_updated_at"),
        },
        "predictions": {"pending_estimates": len(pending), "published_pending": published_pending,
                        "published_settled": len(settled), "published_voided": voided},
        "market_validation": parameters.get("market_validation", {}),
        "performance": evaluate(settled),
        "errors": errors,
        "sync_errors": sync_errors,
    }
