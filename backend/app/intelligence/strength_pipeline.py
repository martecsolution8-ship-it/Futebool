"""Pipeline do modelo de força (Dixon-Coles regularizado + Elo + calibração).

Funções puras (sem base de dados) usadas por IntelligenceEngine:
  - base_rates / competition_base_rates: frequências históricas por mercado
  - run_backtest: validação cronológica (rolling-origin) do modelo REAL publicado, com
      * baseline de frequência histórica para cada mercado;
      * calibração (Platt) confirmada num bloco final de dados que o ajuste NÃO viu;
      * validação por mercado (só mercados que batem a baseline podem originar palpites).
"""
from __future__ import annotations

import math

import numpy as np

from intelligence.evaluation import expected_calibration_error
from intelligence.markets import BASE_EVENTS, BINARY_EVENTS
from intelligence.models.calibration import apply_platt, fit_platt
from intelligence.models.statistical_models import EloTable
from intelligence.models.team_strength import fit_strength_model, market_probabilities
from intelligence.validation.backtesting import chronological_splits
from intelligence.validation.metrics import brier_multiclass, calibration_bins, log_loss_safe

BINARY_MARKETS = BINARY_EVENTS
ELO_WEIGHTS = (0.0, 0.15, 0.30, 0.50)
MIN_MATCHES_PER_TEAM = 5
MIN_GAIN = 0.005           # ganho mínimo de log loss sobre a baseline para validar um mercado (0,5%)
MIN_MARKET_SAMPLES = 30    # jogos fora da amostra mínimos para validar um mercado
CALIBRATION_MIN_SAMPLES = 60
CALIBRATION_FIT_FRACTION = 0.6


def base_rates(rows: list[dict]) -> dict[str, float]:
    """Frequência histórica de cada resultado. Sem jogos não há base: devolve {} (nunca valores inventados)."""
    if not rows:
        return {}
    n = len(rows)
    return {key: sum(1 for r in rows if fn(r["home_score"], r["away_score"])) / n for key, fn in BASE_EVENTS.items()}


def competition_base_rates(rows: list[dict], shrink: int = 20) -> tuple[dict, dict[str, dict]]:
    """Taxas globais + por competição (encolhidas para a global quando há poucos jogos)."""
    glob = base_rates(rows)
    if not glob:
        return {}, {}
    by_comp: dict[str, list[dict]] = {}
    for r in rows:
        by_comp.setdefault(r.get("competition_id"), []).append(r)
    result = {}
    for comp, items in by_comp.items():
        local = base_rates(items)
        n = len(items)
        result[comp] = {k: (n * local[k] + shrink * glob[k]) / (n + shrink) for k in glob}
    return glob, result


def _binary_metrics(p: np.ndarray, y: np.ndarray) -> dict:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {
        "brier_score": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "accuracy": float(np.mean((p >= 0.5) == (y >= 0.5))),
    }


def validate_calibration(p: np.ndarray, y: np.ndarray) -> dict:
    """A calibração só conta se melhorar o Log Loss num bloco FINAL que o ajuste não viu.

    p/y estão por ordem cronológica. Ajusta no 1.º bloco (60%), avalia no 2.º (40%).
    """
    n = len(y)
    if n < CALIBRATION_MIN_SAMPLES:
        return {"validated": False, "holdout_samples": 0, "reason": "amostra insuficiente"}
    cut = int(n * CALIBRATION_FIT_FRACTION)
    params = fit_platt(p[:cut], y[:cut])
    if params is None:
        return {"validated": False, "holdout_samples": n - cut, "reason": "ajuste impossível"}
    holdout_p, holdout_y = p[cut:], y[cut:]
    calibrated = np.array([apply_platt(float(v), params) for v in holdout_p])
    raw_ll = _binary_metrics(holdout_p, holdout_y)["log_loss"]
    cal_ll = _binary_metrics(calibrated, holdout_y)["log_loss"]
    return {"validated": bool(cal_ll < raw_ll), "holdout_samples": int(n - cut),
            "raw_log_loss": raw_ll, "calibrated_log_loss": cal_ll}


def run_backtest(rows: list[dict], min_history: int, max_folds: int = 5,
                 min_gain: float = MIN_GAIN, min_market_samples: int = MIN_MARKET_SAMPLES) -> dict | None:
    """Validação cronológica do modelo real. rows ordenadas por data (mais antigo primeiro)."""
    n = len(rows)
    initial = max(min_history, int(n * 0.5))
    if n - initial < 10:
        return None
    test_size = max(5, math.ceil((n - initial) / max_folds))
    splits = chronological_splits(n, initial, test_size)

    elo = EloTable()
    elo_pre = []
    for r in rows:
        elo_pre.append(elo.probability(r["home_team_id"], r["away_team_id"]))
        elo.update(r["home_team_id"], r["away_team_id"], r["home_score"], r["away_score"])

    labels, dc_1x2, elo_1x2, base_1x2 = [], [], [], []
    bin_p = {k: [] for k in BINARY_MARKETS}
    bin_y = {k: [] for k in BINARY_MARKETS}
    bin_base = {k: [] for k in BINARY_MARKETS}
    used_folds = 0

    for split in splits:
        train = rows[:split.cutoff_index]
        as_of = rows[split.cutoff_index]["kickoff_at"]
        model = fit_strength_model(train, as_of)
        if model is None:
            continue
        used_folds += 1
        base = base_rates(train)
        for i in range(split.cutoff_index, split.cutoff_index + split.test_matches):
            r = rows[i]
            h, a = r["home_team_id"], r["away_team_id"]
            if model.matches_per_team.get(h, 0) < MIN_MATCHES_PER_TEAM or model.matches_per_team.get(a, 0) < MIN_MATCHES_PER_TEAM:
                continue
            mp = market_probabilities(model.score_matrix(h, a, r.get("competition_id")))
            hg, ag = r["home_score"], r["away_score"]
            labels.append(0 if hg > ag else 1 if hg == ag else 2)
            dc_1x2.append([mp["home_win"], mp["draw"], mp["away_win"]])
            elo_1x2.append(list(elo_pre[i]))
            base_1x2.append([base["home_win"], base["draw"], base["away_win"]])
            for key, fn in BINARY_MARKETS.items():
                bin_p[key].append(mp[key])
                bin_y[key].append(1.0 if fn(hg, ag) else 0.0)
                bin_base[key].append(base[key])

    if len(labels) < 30:
        return None

    y = np.asarray(labels, dtype=int)
    dc, el, bs = (np.asarray(x, dtype=float) for x in (dc_1x2, elo_1x2, base_1x2))

    best_w, best_ll = 0.0, float("inf")
    for w in ELO_WEIGHTS:
        blend = (1 - w) * dc + w * el
        blend /= blend.sum(axis=1, keepdims=True)
        ll = log_loss_safe(y, blend, [0, 1, 2])
        if ll < best_ll:
            best_w, best_ll = w, ll
    final = (1 - best_w) * dc + best_w * el
    final /= final.sum(axis=1, keepdims=True)
    base_ll = log_loss_safe(y, bs, [0, 1, 2])

    metrics = {"1X2": {
        "sample_count": int(len(y)),
        "brier_score": brier_multiclass(y, final),
        "log_loss": float(best_ll),
        "accuracy": float(np.mean(np.argmax(final, axis=1) == y)),
        "baseline_log_loss": float(base_ll),
        "baseline_accuracy": float(np.mean(np.argmax(bs, axis=1) == y)),
        "beats_baseline": bool(best_ll < base_ll),
        "log_loss_gain": float(1.0 - best_ll / base_ll) if base_ll > 0 else 0.0,
        "ece": float(expected_calibration_error(final.ravel(), np.eye(3)[y].ravel())),
        "calibration": calibration_bins((y == 0).astype(float), final[:, 0], 5),
    }}
    calibration = {}
    market_validation = {"1X2": {
        "validated": bool(best_ll < base_ll and (1.0 - best_ll / base_ll) >= min_gain and len(y) >= min_market_samples),
        "samples": int(len(y)), "gain": float(1.0 - best_ll / base_ll) if base_ll > 0 else 0.0,
        "log_loss": float(best_ll), "baseline_log_loss": float(base_ll),
        "ece": float(expected_calibration_error(final.ravel(), np.eye(3)[y].ravel())),
    }}
    for key in BINARY_MARKETS:
        p = np.asarray(bin_p[key], dtype=float)
        yy = np.asarray(bin_y[key], dtype=float)
        m = _binary_metrics(p, yy)
        b = _binary_metrics(np.asarray(bin_base[key], dtype=float), yy)
        gain = float(1.0 - m["log_loss"] / b["log_loss"]) if b["log_loss"] > 0 else 0.0
        m.update({
            "sample_count": int(len(yy)), "baseline_log_loss": b["log_loss"],
            "beats_baseline": bool(m["log_loss"] < b["log_loss"]),
            "log_loss_gain": gain,
            "calibration": calibration_bins(yy, p, 5),
        })
        metrics[key] = m
        fitted = fit_platt(p, yy)
        holdout = validate_calibration(p, yy)
        # Só se aplica calibração confirmada em dados novos; caso contrário a probabilidade fica como o modelo a calculou.
        calibration[key] = None if fitted is None else {**fitted, "validated": holdout["validated"], "holdout": holdout}
        market_validation[key] = {
            "validated": bool(m["beats_baseline"] and gain >= min_gain and len(yy) >= min_market_samples),
            "samples": int(len(yy)), "gain": gain,
            "log_loss": m["log_loss"], "baseline_log_loss": b["log_loss"],
        }

    skill = float(np.mean([1.0 if m["beats_baseline"] else 0.0 for m in metrics.values()]))
    return {
        "folds": used_folds, "initial_train": initial, "test_size": test_size,
        "test_matches": int(len(y)), "elo_weight": best_w,
        "metrics": metrics, "calibration": calibration, "skill": skill,
        "market_validation": market_validation,
    }
