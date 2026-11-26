"""Testes do motor MarBet COM base de dados (precisam de pytest + SQLAlchemy + FastAPI instalados)."""
import pytest

from app.intelligence.engine import IntelligenceEngine
from app.intelligence.immutability import ImmutablePredictionError
from app.models.intelligence import ModelVersion, Prediction, PredictionStatus
from app.models.sports import Fixture, FixtureStatus
from test_strength_engine import _league


def _published(db, status=PredictionStatus.PENDING):
    return [p for p in db.query(Prediction).filter(Prediction.status == status).all() if (p.rationale or {}).get("published_pick")]


def _run(db):
    _league(db)
    engine = IntelligenceEngine(db)
    engine.train_and_backtest()
    engine.generate_predictions()
    return engine


def test_published_pick_only_in_validated_markets_and_keeps_original_snapshot(db):
    engine = _run(db)
    validation = engine._model_version().parameters["market_validation"]
    for p in _published(db):
        r = p.rationale
        assert r["market_validated"] is True and r["confidence_level"] in ("LOW", "MODERATE", "HIGH")
        snap = r["published_snapshot"]
        assert snap["probability"] == pytest.approx(p.probability, abs=1e-4) and snap["market"] == p.market
        assert r["factors_for"] is not None and r["risks"] and r["odds"]["assessed"] is False
    assert isinstance(validation, dict) and "1X2" in validation


def test_published_pick_cannot_be_modified_but_can_be_settled(db):
    _run(db)
    picks = _published(db)
    if not picks:
        pytest.skip("os dados de teste não originaram palpites publicados")
    pick = picks[0]
    pick.probability = 0.99
    with pytest.raises(ImmutablePredictionError):
        db.commit()
    db.rollback()
    pick = db.get(Prediction, pick.id)
    pick.is_correct = True      # liquidação é permitida
    db.commit()


def test_settlement_is_idempotent(db):
    engine = _run(db)
    picks = _published(db)
    for fixture in {db.get(Fixture, p.fixture_id) for p in picks}:
        fixture.status, fixture.home_score, fixture.away_score = FixtureStatus.FINISHED, 2, 1
    db.commit()
    first = engine.settle_predictions()
    snapshot = {p.id: (p.status, p.is_correct, p.settled_at) for p in db.query(Prediction).all()}
    second = engine.settle_predictions()
    assert second.metadata_json["settled"] == 0 and first.metadata_json["settled"] >= 0
    assert snapshot == {p.id: (p.status, p.is_correct, p.settled_at) for p in db.query(Prediction).all()}


def test_new_model_version_never_creates_second_pick_nor_voids_published(db):
    engine = _run(db)
    before = {p.fixture_id: p.id for p in _published(db)}
    engine.model_label = "9.9.9"           # simula nova versão do modelo
    engine.train_and_backtest(force=True)
    engine.generate_predictions()
    after = _published(db)
    assert len({p.fixture_id for p in after}) == len(after)          # no máximo um por jogo
    assert set(before.values()) <= {p.id for p in after}              # os antigos continuam publicados e PENDING


def test_analysis_endpoint_refuses_finished_games_and_separates_pick(client, db):
    _run(db)
    finished = db.query(Fixture).filter(Fixture.status == FixtureStatus.FINISHED).first()
    body = client.get(f"/api/v1/intelligence/fixtures/{finished.id}/analysis").json()
    assert body["analysable"] is False and body["probabilities"] == [] and body["official_pick"] is None
    scheduled = db.query(Fixture).filter(Fixture.status == FixtureStatus.SCHEDULED).first()
    body = client.get(f"/api/v1/intelligence/fixtures/{scheduled.id}/analysis").json()
    assert (body["official_pick"] is None) == (body["message"] is not None)
