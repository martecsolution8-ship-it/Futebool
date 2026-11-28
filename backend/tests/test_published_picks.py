"""Testes com base de dados (precisam do ambiente completo do backend: pytest + SQLAlchemy)."""
from datetime import datetime, timedelta, timezone

from app.intelligence.engine import IntelligenceEngine
from app.models.intelligence import ModelVersion, Prediction, PredictionStatus
from app.models.sports import Fixture, FixtureStatus
from test_strength_engine import _league


def _picks(db):
    return [p for p in db.query(Prediction).filter(Prediction.status == PredictionStatus.PENDING).all()
            if (p.rationale or {}).get("published_pick")]


def test_at_most_one_published_pick_per_fixture_with_real_edge(db):
    _league(db)
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    engine.generate_predictions()
    picks = _picks(db)
    assert len({p.fixture_id for p in picks}) == len(picks)
    for p in picks:
        assert p.probability >= engine.settings.intelligence_pick_min_probability
        assert p.rationale["edge"] >= engine.settings.intelligence_pick_min_edge - 1e-9
        assert "first_probability" in p.rationale and "form" in p.rationale


def test_published_pick_is_not_changed_retroactively(db):
    _league(db)
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    engine.generate_predictions()
    before = {p.id: (p.market, p.selection, p.probability) for p in _picks(db)}
    engine.generate_predictions()
    after = {p.id: (p.market, p.selection, p.probability) for p in _picks(db)}
    assert before == after


def test_same_match_from_two_providers_counts_once(db):
    upcoming = _league(db)
    engine = IntelligenceEngine(db)
    finished = engine._finished_fixtures()
    original = finished[0]
    db.add(Fixture(
        competition_id=original.competition_id, season_id=original.season_id,
        home_team_id=original.home_team_id, away_team_id=original.away_team_id,
        provider="other", provider_fixture_id="dup-1", kickoff_at=original.kickoff_at + timedelta(minutes=5),
        status=FixtureStatus.FINISHED, home_score=original.home_score, away_score=original.away_score,
        statistics={}, raw_hash="dup",
    ))
    db.commit()
    assert len(engine._finished_fixtures()) == len(finished)


def test_settlement_does_not_score_cancelled_suspended_or_live(db):
    _league(db)
    engine = IntelligenceEngine(db)
    model = engine._model_version()
    fixture = db.query(Fixture).filter(Fixture.status == FixtureStatus.SCHEDULED).first()
    fixture.status = FixtureStatus.CANCELLED
    prediction = Prediction(
        fixture_id=fixture.id,
        model_version_id=model.id,
        market="1X2",
        selection="HOME",
        probability=0.70,
        confidence=0.70,
        data_quality=0.70,
        rationale={"published_pick": True},
    )
    db.add(prediction)
    db.commit()

    engine.settle_predictions()

    db.refresh(prediction)
    assert prediction.status == PredictionStatus.VOID
    assert prediction.actual_outcome == "VOID"
    assert prediction.is_correct is None


def test_settlement_scores_hit_error_and_is_idempotent(db):
    _league(db)
    engine = IntelligenceEngine(db)
    model = engine._model_version()
    fixtures = db.query(Fixture).filter(Fixture.status == FixtureStatus.SCHEDULED).limit(2).all()

    fixtures[0].status = FixtureStatus.FINISHED
    fixtures[0].home_score = 2
    fixtures[0].away_score = 0
    fixtures[1].status = FixtureStatus.FINISHED
    fixtures[1].home_score = 0
    fixtures[1].away_score = 1

    hit = Prediction(fixture_id=fixtures[0].id, model_version_id=model.id, market="1X2", selection="HOME", probability=0.70, confidence=0.70, data_quality=0.70, rationale={"published_pick": True})
    miss = Prediction(fixture_id=fixtures[1].id, model_version_id=model.id, market="OVER_2.5", selection="OVER_2.5", probability=0.65, confidence=0.65, data_quality=0.65, rationale={"published_pick": True})
    db.add_all([hit, miss])
    db.commit()

    engine.settle_predictions()
    engine.settle_predictions()

    db.refresh(hit)
    db.refresh(miss)
    assert hit.status == PredictionStatus.SETTLED and hit.is_correct is True
    assert miss.status == PredictionStatus.SETTLED and miss.is_correct is False


def test_picks_endpoint_only_future_and_unique(client, db):
    _league(db)
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    engine.generate_predictions()
    items = client.get("/api/v1/intelligence/picks").json()
    assert len({i["fixture_id"] for i in items}) == len(items)
    performance = client.get("/api/v1/intelligence/performance").json()
    assert "markets" in performance
