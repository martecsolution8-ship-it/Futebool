"""Testes do motor MarBet que NÃO precisam de base de dados (unittest; também corre com pytest).

Os dados sintéticos servem apenas para verificar cálculos; nunca chegam ao produto.
"""
import math
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from app.intelligence.strength_pipeline import MIN_MATCHES_PER_TEAM, base_rates, run_backtest
from intelligence.confidence import HIGH, LOW, classify_confidence
from intelligence.evaluation import SettledPick, detect_degradation, evaluate
from intelligence.features.feature_builder import team_history
from intelligence.markets import (
    NO_PICK_MESSAGE, PUSH, WIN, LOSS, analysis_eligibility, build_market_estimates, settle_selection,
    settlement_state, validate_probabilities,
)
from intelligence.models.team_strength import fit_strength_model, market_probabilities, score_matrix
from intelligence.picks import PickCandidate, choose_pick, pick_block_reason
from intelligence.published_rules import check_published_update
from intelligence.validation.backtesting import assert_no_future_leakage
from intelligence.value import assess_value, expected_value

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def league(seed=11, teams=12, rounds=40):
    rng = np.random.default_rng(seed)
    att, de = rng.normal(0, .35, teams), rng.normal(0, .35, teams)
    rows = []
    for r in range(rounds):
        for k in range(teams // 2):
            i, j = (k + r) % teams, (teams - 1 - k + r * 5) % teams
            if i == j:
                continue
            rows.append({"home_team_id": f"T{i}", "away_team_id": f"T{j}", "competition_id": "L",
                         "kickoff_at": NOW - timedelta(days=(rounds - r) * 4),
                         "home_score": int(rng.poisson(np.exp(.4 + att[i] - de[j]))),
                         "away_score": int(rng.poisson(np.exp(.15 + att[j] - de[i])))})
    rows.sort(key=lambda x: x["kickoff_at"])
    return rows


class TestAnalysis(unittest.TestCase):
    def test_01_complete_data_gives_coherent_probabilities(self):
        rows = league()
        model = fit_strength_model(rows, NOW)
        self.assertIsNotNone(model)
        mp = market_probabilities(model.score_matrix("T1", "T2"))
        est = build_market_estimates(mp, (mp["home_win"], mp["draw"], mp["away_win"]))
        d = {(e.market, e.selection): e.probability for e in est}
        self.assertEqual(len(d), 19)
        self.assertTrue(all(0 <= p <= 1 for p in d.values()))
        self.assertAlmostEqual(d[("1X2", "HOME")] + d[("1X2", "DRAW")] + d[("1X2", "AWAY")], 1.0)
        self.assertAlmostEqual(d[("DOUBLE_CHANCE", "1X")] + d[("1X2", "AWAY")], 1.0)
        self.assertAlmostEqual(d[("TOTAL_GOALS", "OVER_2.5")] + d[("TOTAL_GOALS", "UNDER_2.5")], 1.0)
        self.assertLessEqual(d[("TOTAL_GOALS", "OVER_3.5")], d[("TOTAL_GOALS", "OVER_2.5")])
        self.assertLessEqual(d[("TOTAL_GOALS", "OVER_2.5")], d[("TOTAL_GOALS", "OVER_1.5")])

    def test_02_incomplete_data_lowers_quality(self):
        rows = league()
        few = team_history(rows, "T1", rows[3]["kickoff_at"], 10)
        full = team_history(rows, "T1", NOW, 10)
        self.assertLess(few.matches, full.matches)
        self.assertEqual(team_history(rows, "ninguem", NOW).matches, 0)

    def test_03_no_history_means_no_model_and_no_invented_base(self):
        self.assertIsNone(fit_strength_model(league()[:10], NOW))
        self.assertEqual(base_rates([]), {})
        self.assertGreaterEqual(MIN_MATCHES_PER_TEAM, 5)

    def test_04_probabilities_match_closed_forms(self):
        lh, la = 1.4, 0.9
        mp = market_probabilities(score_matrix(lh, la, rho=0.0, max_goals=30))
        self.assertAlmostEqual(mp["btts"], (1 - math.exp(-lh)) * (1 - math.exp(-la)), places=6)
        self.assertAlmostEqual(mp["home_over_05"], 1 - math.exp(-lh), places=6)
        self.assertAlmostEqual(mp["away_over_15"], 1 - math.exp(-la) * (1 + la), places=6)

    def test_05_selection_one_market_per_fixture_and_validation_gate(self):
        c = [PickCandidate("TOTAL_GOALS", "OVER_2.5", .60, .50, .7, .9), PickCandidate("TOTAL_GOALS", "UNDER_2.5", .40, .50, .7, .9)]
        self.assertEqual(choose_pick(c).selection, "OVER_2.5")
        self.assertEqual(pick_block_reason(c[0], market_validated=False), "MERCADO_NAO_VALIDADO")
        self.assertIsNone(pick_block_reason(c[0], market_validated=True))

    def test_06_no_opportunity(self):
        self.assertIsNone(choose_pick([PickCandidate("TOTAL_GOALS", "OVER_1.5", .76, .75, .9, .9)]))
        self.assertTrue(NO_PICK_MESSAGE.startswith("Sem palpite recomendado"))

    def test_07_invalid_probabilities_are_rejected(self):
        for bad in (float("nan"), 1.5, -0.2, float("inf"), "x"):
            with self.assertRaises(ValueError):
                validate_probabilities({"a": bad})
        with self.assertRaises(ValueError):
            build_market_estimates({"home_win": 2.0}, (0.4, 0.3, 0.3))

    def test_08_finished_game_is_not_analysed(self):
        ok, reason = analysis_eligibility("FINISHED", NOW - timedelta(days=1), NOW)
        self.assertFalse(ok); self.assertEqual(reason, "JOGO_TERMINADO")
        self.assertEqual(analysis_eligibility("SCHEDULED", NOW - timedelta(minutes=1), NOW)[1], "JOGO_JA_INICIADO")
        self.assertTrue(analysis_eligibility("SCHEDULED", NOW + timedelta(hours=3), NOW)[0])

    def test_09_postponed_or_cancelled_is_never_hit_or_miss(self):
        for status in ("POSTPONED", "CANCELLED", "SUSPENDED"):
            self.assertFalse(analysis_eligibility(status, NOW + timedelta(days=1), NOW)[0])
            self.assertEqual(settlement_state(status, 2, 1), "VOID")
        self.assertEqual(settlement_state("LIVE", 1, 0), "PENDING")
        self.assertEqual(settlement_state("FINISHED", None, None), "PENDING")
        self.assertEqual(settlement_state("FINISHED", 2, 1), "READY")


class TestPublicationAndSettlement(unittest.TestCase):
    def test_10_11_published_pick_is_immutable_except_settlement(self):
        rat = {"published_pick": True, "published_snapshot": {"probability": .62}, "first_probability": .62, "published_at": "t"}
        old = {"probability": .62, "market": "1X2"}
        self.assertEqual(check_published_update(old, {"probability": .70}, rat, rat), ["probability"])
        self.assertIn("rationale.published_snapshot", check_published_update({}, {}, rat, {**rat, "published_snapshot": {}}))
        self.assertEqual(check_published_update({}, {}, rat, rat), [])          # liquidar (status/is_correct) é permitido
        self.assertEqual(check_published_update(old, {"probability": .9}, {}, {}), [])  # não publicado: pode mudar

    def test_12_settlement_hits_and_misses(self):
        cases = [("1X2", "HOME", 2, 0, WIN), ("1X2", "DRAW", 2, 0, LOSS), ("DOUBLE_CHANCE", "1X", 1, 1, WIN),
                 ("DOUBLE_CHANCE", "12", 1, 1, LOSS), ("DRAW_NO_BET", "HOME", 1, 1, PUSH), ("DRAW_NO_BET", "AWAY", 0, 2, WIN),
                 ("BTTS", "YES", 1, 1, WIN), ("BTTS", "NO", 1, 1, LOSS), ("TOTAL_GOALS", "OVER_2.5", 2, 1, WIN),
                 ("TOTAL_GOALS", "UNDER_2.5", 2, 1, LOSS), ("TOTAL_GOALS", "OVER_3.5", 2, 2, WIN),
                 ("OVER_2.5", "OVER_2.5", 2, 1, WIN),  # rótulo de mercado antigo continua a liquidar
                 ("TEAM_GOALS_HOME", "OVER_1.5", 2, 0, WIN), ("TEAM_GOALS_AWAY", "OVER_0.5", 3, 0, LOSS)]
        for market, sel, h, a, expected in cases:
            self.assertEqual(settle_selection(market, sel, h, a), expected, (market, sel, h, a))
        self.assertIsNone(settle_selection("1X2", "XYZ", 1, 0))
        self.assertIsNone(settle_selection("MERCADO_NOVO", "YES", 1, 0))
        self.assertIsNone(settle_selection("1X2", "HOME", None, 0))


class TestEvaluationAndLeakage(unittest.TestCase):
    def test_14_evaluation_metrics_by_hand(self):
        t0 = NOW
        picks = [SettledPick("1X2", "A", .8, True, t0), SettledPick("1X2", "A", .6, False, t0 + timedelta(1))]
        s = evaluate(picks)["overall"]
        self.assertEqual(s["hit_rate"], .5)
        self.assertAlmostEqual(s["brier_score"], (.2 ** 2 + .6 ** 2) / 2)
        self.assertAlmostEqual(s["log_loss"], -(math.log(.8) + math.log(.4)) / 2)
        self.assertFalse(detect_degradation(picks)["evaluated"])  # amostra pequena: não conclui nada
        good = [SettledPick("1X2", "A", .6, i % 5 != 0, t0 + timedelta(i)) for i in range(60)]
        bad = [SettledPick("1X2", "A", .6, False, t0 + timedelta(100 + i)) for i in range(30)]
        self.assertTrue(detect_degradation(good + bad)["degraded"])

    def test_15_no_future_leakage(self):
        rows = league()
        asof = rows[len(rows) // 2]["kickoff_at"]
        base = fit_strength_model(rows, asof)
        future = dict(rows[-1], kickoff_at=asof + timedelta(days=30), home_score=9, away_score=0)
        again = fit_strength_model(rows + [future], asof)
        self.assertTrue(np.allclose(base.attack, again.attack) and base.rho == again.rho)
        self.assertEqual(team_history(rows + [future], future["home_team_id"], asof, 50).matches,
                         team_history(rows, future["home_team_id"], asof, 50).matches)
        with self.assertRaises(AssertionError):
            assert_no_future_leakage(100, 99)
        assert_no_future_leakage(100, 100)

    def test_backtest_validates_markets_and_reports_real_metrics(self):
        result = run_backtest(league(rounds=60), min_history=30)
        self.assertIsNotNone(result)
        self.assertIn("1X2", result["market_validation"])
        self.assertTrue(result["market_validation"]["1X2"]["validated"])      # há sinal real nos dados sintéticos
        self.assertIsNone(run_backtest(league()[:20], min_history=30))         # sem histórico: não valida nada


class TestConfidenceAndValue(unittest.TestCase):
    def test_high_probability_alone_is_never_high_confidence(self):
        weak = classify_confidence(data_quality=.4, market_validated=False, validation_gain=None, validation_samples=0,
                                   calibration_state="NONE", model_agreement=.5, edge=.3)
        self.assertEqual(weak.level, LOW)
        strong = classify_confidence(data_quality=1, market_validated=True, validation_gain=.04, validation_samples=300,
                                     calibration_state="VALIDATED", model_agreement=.9, edge=.08)
        self.assertEqual(strong.level, HIGH)

    def test_no_odds_no_value_claim(self):
        self.assertFalse(assess_value(.6, None)["assessed"])
        self.assertIsNone(expected_value(.6, .9))
        self.assertAlmostEqual(expected_value(.55, 2.0), .10)


if __name__ == "__main__":
    unittest.main()
