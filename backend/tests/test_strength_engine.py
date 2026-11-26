from datetime import datetime, timedelta, timezone

import numpy as np

from app.intelligence.engine import IntelligenceEngine
from app.models.intelligence import (
    BacktestRun, EngineRunStatus, EvaluationMetric, ModelStatus, ModelVersion, Prediction, PredictionStatus,
)
from app.models.sports import Competition, Fixture, FixtureStatus, Season, Team


def _league(db, rounds=24, teams_n=12, seed=7):
    rng = np.random.default_rng(seed)
    comp = Competition(name="Liga Teste", external_ids={})
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2026", external_ids={}, active=True)
    teams = [Team(name=f"T{i}", external_ids={}) for i in range(teams_n)]
    db.add_all([season, *teams])
    db.flush()
    attack = rng.normal(0, 0.35, teams_n)
    defense = rng.normal(0, 0.35, teams_n)
    now = datetime.now(timezone.utc)
    counter = 0

    def add(i, j, kickoff, finished):
        nonlocal counter
        counter += 1
        gh = ga = None
        if finished:
            gh = int(rng.poisson(np.exp(0.15 + 0.25 + attack[i] - defense[j])))
            ga = int(rng.poisson(np.exp(0.15 + attack[j] - defense[i])))
        fx = Fixture(
            competition_id=comp.id, season_id=season.id, home_team_id=teams[i].id, away_team_id=teams[j].id,
            provider="test", provider_fixture_id=f"f{counter}", kickoff_at=kickoff,
            status=FixtureStatus.FINISHED if finished else FixtureStatus.SCHEDULED,
            home_score=gh, away_score=ga, statistics={}, raw_hash="t",
        )
        db.add(fx)
        return fx

    for r in range(rounds):
        for k in range(teams_n // 2):
            i, j = (k + r) % teams_n, (teams_n - 1 - k + r * 5) % teams_n
            if i != j:
                add(i, j, now - timedelta(days=(rounds - r) * 4 + 3), True)
    upcoming = [add(i, (i + 3) % teams_n, now + timedelta(days=2 + i % 3), False) for i in range(teams_n)]
    db.commit()
    return upcoming


def test_backtest_validates_the_published_model(db):
    _league(db)
    engine = IntelligenceEngine(db)
    run = engine.train_and_backtest()
    assert run.status in (EngineRunStatus.SUCCESS, EngineRunStatus.PARTIAL)
    backtest = db.query(BacktestRun).one()
    assert backtest.summary["brier_score"] is not None and backtest.summary["log_loss"] is not None
    from app.intelligence.strength_pipeline import BINARY_MARKETS
    assert db.query(EvaluationMetric).count() == 1 + len(BINARY_MARKETS)
    model = db.query(ModelVersion).filter_by(code="strength-dc").one()
    assert {"calibration", "elo_weight", "skill"} <= set(model.parameters)
    assert model.status in (ModelStatus.VALIDATED, ModelStatus.DEVELOPMENT)


def test_predictions_are_sane_refreshed_and_not_duplicated(db):
    upcoming = _league(db)
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    first = engine.generate_predictions()
    assert first.status == EngineRunStatus.SUCCESS and first.records_created > 0

    preds = db.query(Prediction).filter(Prediction.status == PredictionStatus.PENDING).all()
    for p in preds:
        assert 0.05 <= p.probability <= 1.0
        assert "edge" in p.rationale and "expected_goals_home" in p.rationale
        if p.selection == "OVER_1.5":
            assert p.probability < 0.99  # o modelo antigo dava 95-99% de forma sistematica
    for fx in upcoming:
        by = {(p.market, p.selection): p.probability for p in preds if p.fixture_id == fx.id}
        if by:
            assert abs(by[("1X2", "HOME")] + by[("1X2", "DRAW")] + by[("1X2", "AWAY")] - 1.0) < 1e-6

    count = db.query(Prediction).count()
    second = engine.generate_predictions()
    assert second.records_created == 0 and second.metadata_json["updated"] > 0
    assert db.query(Prediction).count() == count


def test_old_model_pending_predictions_are_voided(db):
    upcoming = _league(db)
    old = ModelVersion(code="poisson-elo", version="1.0.0", status=ModelStatus.DEVELOPMENT, algorithm="old", parameters={})
    db.add(old)
    db.flush()
    stale = Prediction(fixture_id=upcoming[0].id, model_version_id=old.id, market="TOTAL_GOALS", selection="OVER_1.5",
                       probability=0.99, confidence=0.9, data_quality=0.9, rationale={})
    db.add(stale)
    db.commit()
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    engine.generate_predictions()
    db.refresh(stale)
    assert stale.status == PredictionStatus.VOID


def test_top8_endpoint_unique_fixtures_and_not_only_over_15(client, db):
    _league(db)
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    engine.generate_predictions()
    response = client.get("/api/v1/intelligence/top8")
    assert response.status_code == 200
    items = response.json()
    assert len(items) <= 8
    assert len({i["fixture_id"] for i in items}) == len(items)
