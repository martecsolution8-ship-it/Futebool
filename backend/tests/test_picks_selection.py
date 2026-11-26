"""Testes da seleção de prognósticos e da matemática do modelo.

Só usam funções puras (sem base de dados). Os dados sintéticos abaixo existem apenas
para verificar cálculos; nunca chegam ao produto.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np

from app.intelligence.strength_pipeline import MIN_MATCHES_PER_TEAM, run_backtest
from intelligence.models.team_strength import fit_strength_model, market_probabilities, score_matrix
from intelligence.picks import PickCandidate, choose_pick, dedupe_matches, summarize_settled

T0 = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ deduplicação

def _m(home, away, minutes, scored=False, provider="a"):
    return {"home": home, "away": away, "t": T0 + timedelta(minutes=minutes), "scored": scored, "provider": provider}


def _dedupe(items):
    return dedupe_matches(items, lambda x: x["home"], lambda x: x["away"], lambda x: x["t"],
                          rank=lambda x: 1.0 if x["scored"] else 0.0)


def test_same_match_from_two_providers_is_kept_once_and_prefers_the_one_with_result():
    kept = _dedupe([_m("A", "B", 0, False, "api-football"), _m("A", "B", 3, True, "football-data")])
    assert len(kept) == 1 and kept[0]["scored"] is True


def test_rematches_and_reversed_fixtures_are_not_merged():
    kept = _dedupe([_m("A", "B", 0), _m("A", "B", 60 * 24 * 14), _m("B", "A", 0)])
    assert len(kept) == 3


# ------------------------------------------------------------------ escolha do prognóstico

def _c(market, selection, p, base, conf=0.7, quality=0.9):
    return PickCandidate(market, selection, p, base, conf, quality)


def test_only_one_pick_per_fixture_never_contradictory():
    cands = [
        _c("TOTAL_GOALS", "OVER_2.5", 0.60, 0.50),
        _c("TOTAL_GOALS", "UNDER_2.5", 0.40, 0.50),   # complementar: nunca pode sair também
        _c("BTTS", "YES", 0.59, 0.50),
        _c("1X2", "DRAW", 0.14, 0.26),
    ]
    pick = choose_pick(cands)
    assert pick is not None and pick.selection == "OVER_2.5"


def test_low_probability_markets_are_never_recommended():
    assert choose_pick([_c("1X2", "DRAW", 0.14, 0.10)]) is None  # tem "vantagem", mas 14% não é prognóstico


def test_no_edge_over_historical_average_means_no_pick():
    assert choose_pick([_c("TOTAL_GOALS", "OVER_1.5", 0.76, 0.75)]) is None


def test_insufficient_data_means_no_pick():
    assert choose_pick([_c("1X2", "HOME", 0.70, 0.45, quality=0.2)]) is None
    assert choose_pick([]) is None


def test_confidence_is_not_probability():
    # prob alta com confiança baixa perde para prob menor com muita confiança e mais vantagem
    low_conf = _c("TOTAL_GOALS", "OVER_1.5", 0.86, 0.75, conf=0.1)
    solid = _c("1X2", "HOME", 0.66, 0.45, conf=0.9)
    assert choose_pick([low_conf, solid]).selection == "HOME"


# ------------------------------------------------------------------ métricas

def test_summarize_settled_matches_hand_calculation():
    s = summarize_settled([("1X2", 0.8, True), ("1X2", 0.6, False)])["1X2"]
    assert s["samples"] == 2 and s["hit_rate"] == 0.5
    assert math.isclose(s["brier_score"], ((0.2) ** 2 + (0.6) ** 2) / 2)
    assert math.isclose(s["log_loss"], -(math.log(0.8) + math.log(0.4)) / 2)


# ------------------------------------------------------------------ matemática do modelo

def test_score_matrix_and_markets_are_consistent_probabilities():
    m = score_matrix(1.6, 1.1, rho=-0.05)
    assert math.isclose(m.sum(), 1.0)
    mp = market_probabilities(m)
    assert math.isclose(mp["home_win"] + mp["draw"] + mp["away_win"], 1.0, abs_tol=1e-9)
    assert math.isclose(mp["over_25"] + mp["under_25"], 1.0, abs_tol=1e-9)
    assert mp["over_25"] <= mp["over_15"] <= 1.0


def test_independent_poisson_closed_forms_match():
    lh, la = 1.4, 0.9
    mp = market_probabilities(score_matrix(lh, la, rho=0.0, max_goals=30))
    assert math.isclose(mp["btts"], (1 - math.exp(-lh)) * (1 - math.exp(-la)), abs_tol=1e-6)
    lam = lh + la
    assert math.isclose(mp["over_15"], 1 - math.exp(-lam) * (1 + lam), abs_tol=1e-6)


def _synthetic_league(seed=11, teams=12, rounds=26):
    rng = np.random.default_rng(seed)
    attack, defense = rng.normal(0, 0.35, teams), rng.normal(0, 0.35, teams)
    rows, now = [], datetime(2026, 11, 1, tzinfo=timezone.utc)
    for r in range(rounds):
        for k in range(teams // 2):
            i, j = (k + r) % teams, (teams - 1 - k + r * 5) % teams
            if i == j:
                continue
            rows.append({
                "home_team_id": f"T{i}", "away_team_id": f"T{j}", "competition_id": "L",
                "kickoff_at": now - timedelta(days=(rounds - r) * 4),
                "home_score": int(rng.poisson(np.exp(0.15 + 0.25 + attack[i] - defense[j]))),
                "away_score": int(rng.poisson(np.exp(0.15 + attack[j] - defense[i]))),
            })
    rows.sort(key=lambda x: x["kickoff_at"])
    return rows, attack, defense


def test_model_recovers_true_team_strengths_on_synthetic_data():
    """Com poucos jogos por equipa a estimativa é ruidosa (varia por semente), por isso
    verificamos a média de várias sementes e a força líquida (ataque + defesa)."""
    as_of = datetime(2026, 11, 2, tzinfo=timezone.utc)
    net, adv = [], []
    for seed in (3, 5, 7, 11, 13):
        rows, attack, defense = _synthetic_league(seed=seed, rounds=60)
        model = fit_strength_model(rows, as_of)
        idx = [model.teams[f"T{i}"] for i in range(len(attack))]
        fitted_net = model.attack[idx] + model.defense[idx]
        net.append(np.corrcoef(fitted_net, attack + defense)[0, 1])
        adv.append(model.home_adv)
    assert float(np.mean(net)) > 0.75
    assert 0.0 < float(np.mean(adv)) < 0.5  # vantagem de casa verdadeira = 0.25


def test_model_never_sees_the_future():
    rows, *_ = _synthetic_league()
    as_of = rows[len(rows) // 2]["kickoff_at"]
    base = fit_strength_model(rows, as_of)
    future = dict(rows[-1], kickoff_at=as_of + timedelta(days=30), home_score=9, away_score=0)
    again = fit_strength_model(rows + [future], as_of)
    assert np.allclose(base.attack, again.attack) and base.home_adv == again.home_adv


def test_too_little_history_gives_no_model():
    rows, *_ = _synthetic_league()
    assert fit_strength_model(rows[:10], datetime(2027, 1, 1, tzinfo=timezone.utc)) is None
    assert MIN_MATCHES_PER_TEAM >= 5


def test_backtest_is_chronological_and_reports_real_metrics():
    rows, *_ = _synthetic_league(rounds=40)
    result = run_backtest(rows, min_history=30)
    assert result is not None
    one_x_two = result["metrics"]["1X2"]
    assert one_x_two["sample_count"] >= 30
    assert 0.0 < one_x_two["brier_score"] < 1.0 and one_x_two["log_loss"] > 0
    assert one_x_two["log_loss"] < one_x_two["baseline_log_loss"]  # há sinal real nos dados sintéticos
    assert run_backtest(rows[:20], min_history=30) is None  # dados insuficientes: não valida nada
