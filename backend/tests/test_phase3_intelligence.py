import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.api.dependencies import require_roles
from app.intelligence.engine import IntelligenceEngine
from app.models.intelligence import EngineRunStatus, ModelStatus, ModelVersion, Prediction, PredictionStatus
from app.models.sports import Competition, Fixture, FixtureStatus, Season, Team
from app.models.user import User, UserRole
from intelligence.features.feature_builder import HistoricalIndex, head_to_head, missing_quality, team_history
from intelligence.models.statistical_models import EloTable, poisson_matrix
from intelligence.top8 import RankedPrediction, select_top8
from intelligence.validation.backtesting import assert_no_future_leakage, chronological_splits
from intelligence.validation.metrics import brier_multiclass, calibration_bins


def test_poisson_probabilities_are_coherent():
    _, probabilities = poisson_matrix(1.5, 1.0)
    result_total = probabilities.home_win + probabilities.draw + probabilities.away_win
    assert abs(result_total - 1.0) < 1e-6
    assert 0.0 <= probabilities.over_25 <= 1.0
    assert 0.0 <= probabilities.under_25 <= 1.0


def test_elo_update_moves_ratings():
    elo = EloTable(base_rating=1500, k_factor=20)
    before_home = elo.rating("A")
    elo.update("A", "B", 2, 0)
    assert elo.rating("A") > before_home
    assert elo.rating("B") < 1500


def test_feature_builder_uses_only_before_cutoff():
    cutoff = datetime(2026, 11, 21, tzinfo=timezone.utc)
    fixtures = [
        {"home_team_id": "A", "away_team_id": "B", "kickoff_at": cutoff - timedelta(days=7), "home_score": 2, "away_score": 1},
        {"home_team_id": "B", "away_team_id": "A", "kickoff_at": cutoff + timedelta(days=7), "home_score": 1, "away_score": 0},
    ]
    snapshot = team_history(fixtures, "A", cutoff)
    assert snapshot.matches == 1
    assert snapshot.wins == 1


def test_historical_index_matches_team_and_h2h_functions():
    cutoff = datetime(2026, 11, 21, tzinfo=timezone.utc)
    fixtures = [
        {"home_team_id": "A", "away_team_id": "B", "kickoff_at": cutoff - timedelta(days=3), "home_score": 2, "away_score": 1},
        {"home_team_id": "B", "away_team_id": "A", "kickoff_at": cutoff - timedelta(days=10), "home_score": 0, "away_score": 1},
        {"home_team_id": "A", "away_team_id": "C", "kickoff_at": cutoff + timedelta(days=3), "home_score": 9, "away_score": 9},
    ]
    index = HistoricalIndex(fixtures)
    old_home = team_history(fixtures, "A", cutoff, 10)
    new_home = index.team_history("A", cutoff, 10)
    assert new_home.matches == old_home.matches
    assert new_home.goals_for == old_home.goals_for
    assert new_home.goals_against == old_home.goals_against
    assert new_home.over_25_pct == old_home.over_25_pct
    assert new_home.btts_pct == old_home.btts_pct
    assert index.head_to_head("A", "B", cutoff) == head_to_head(fixtures, "A", "B", cutoff)


def test_future_window_rejects_game_beyond_window():
    from app.intelligence.engine import future_window_end

    cutoff = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    window_end = future_window_end(cutoff, 7)
    inside = cutoff + timedelta(days=7)
    outside = cutoff + timedelta(days=7, seconds=1)
    assert inside <= window_end
    assert outside > window_end


def test_fixture_priority_prefers_friday_saturday_sunday():
    from app.intelligence.engine import fixture_priority

    now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    dates = [now + timedelta(days=offset) for offset in range(7)]
    assert min(dates, key=lambda date: fixture_priority(date)[0]) in (
        datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 29, 12, 0, tzinfo=timezone.utc),
    )


def test_future_window_and_batch_settings_default_to_seven_and_fifty():
    from app.core.config import Settings

    settings = Settings()
    assert settings.intelligence_future_window_days == 7
    assert settings.intelligence_feature_batch_size == 50


def test_missing_quality_reflects_history_coverage():
    fixtures = [{"home_team_id": "A", "away_team_id": "B", "kickoff_at": datetime(2026, 11, 20, tzinfo=timezone.utc), "home_score": 1, "away_score": 0}]
    cutoff = datetime(2026, 11, 21, tzinfo=timezone.utc)
    home = team_history(fixtures, "A", cutoff)
    away = team_history(fixtures, "B", cutoff)
    assert 0.0 <= missing_quality(home, away) <= 1.0


def test_recent_features_and_head_to_head_exclude_future_matches():
    cutoff = datetime(2026, 11, 21, tzinfo=timezone.utc)
    fixtures = [
        {"home_team_id": "A", "away_team_id": "B", "kickoff_at": cutoff - timedelta(days=3), "home_score": 2, "away_score": 1},
        {"home_team_id": "B", "away_team_id": "A", "kickoff_at": cutoff - timedelta(days=10), "home_score": 0, "away_score": 1},
        {"home_team_id": "A", "away_team_id": "B", "kickoff_at": cutoff + timedelta(days=3), "home_score": 9, "away_score": 9},
    ]
    home = team_history(fixtures, "A", cutoff)
    assert home.matches == 2
    assert home.matches_5 == 2
    assert home.home_matches == 1
    assert home.away_matches == 1
    assert home.over_15_pct == 0.5
    assert len(head_to_head(fixtures, "A", "B", cutoff)) == 2


def test_calibration_and_brier_score():
    labels = np.array([0, 1, 0, 1])
    probabilities = np.array([[0.8, 0.2], [0.3, 0.7], [0.6, 0.4], [0.4, 0.6]])
    brier = brier_multiclass(labels, probabilities)
    bins = calibration_bins(probabilities[:, 1], probabilities[:, 1])
    assert brier >= 0.0
    assert bins


def test_chronological_splits_and_leakage_guard():
    splits = chronological_splits(total=100, initial_train=60, test_size=10)
    assert splits
    assert_no_future_leakage(splits[0].cutoff_index, splits[0].cutoff_index)
    with pytest.raises(AssertionError):
        assert_no_future_leakage(60, 59)


def test_top8_returns_only_eligible_and_unique_fixtures():
    predictions = [
        RankedPrediction("1", "1X2", "HOME", 0.75, 0.80, 0.90),
        RankedPrediction("1", "TOTAL_GOALS", "OVER_2.5", 0.80, 0.85, 0.90),
        RankedPrediction("2", "1X2", "HOME", 0.65, 0.70, 0.80),
        RankedPrediction("3", "BTTS", "YES", 0.55, 0.50, 0.90),
    ]
    selected = select_top8(predictions)
    assert len(selected) == 2
    assert len({item.fixture_id for item in selected}) == len(selected)


def test_admin_permission_dependency():
    dependency = require_roles(UserRole.ADMIN)
    with pytest.raises(Exception) as exc:
        dependency(current_user=User(role=UserRole.USER))
    assert getattr(exc.value, "status_code", None) == 403


def test_settlement_is_idempotent_and_preserves_probability(db):
    competition = Competition(name="Test League", external_ids={})
    db.add(competition)
    db.flush()
    season = Season(competition_id=competition.id, label="2026", external_ids={}, active=True)
    team_home = Team(name="Home", external_ids={})
    team_away = Team(name="Away", external_ids={})
    db.add_all([season, team_home, team_away])
    db.flush()
    fixture = Fixture(
        competition_id=competition.id,
        season_id=season.id,
        home_team_id=team_home.id,
        away_team_id=team_away.id,
        provider="test",
        provider_fixture_id="intelligence-1",
        kickoff_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
        status=FixtureStatus.FINISHED,
        home_score=2,
        away_score=0,
        statistics={},
        raw_hash="test",
    )
    db.add(fixture)
    db.flush()
    model = ModelVersion(code="poisson-elo", version="test", status=ModelStatus.ACTIVE, algorithm="Poisson + Elo", parameters={})
    db.add(model)
    db.flush()
    prediction = Prediction(
        fixture_id=fixture.id,
        model_version_id=model.id,
        market="1X2",
        selection="HOME",
        probability=0.73,
        confidence=0.80,
        data_quality=0.90,
        rationale={"test": True},
        status=PredictionStatus.PENDING,
    )
    db.add(prediction)
    db.commit()

    original_probability = prediction.probability
    engine = IntelligenceEngine(db)
    engine.settle_predictions()
    engine.settle_predictions()

    db.refresh(prediction)
    assert prediction.status == PredictionStatus.SETTLED
    assert prediction.is_correct is True
    assert prediction.actual_outcome == "HOME"
    assert prediction.probability == original_probability


def test_snapshot_commit_can_be_deferred_and_is_idempotent(db):
    from test_strength_engine import _league
    from app.models.intelligence import FeatureSnapshot

    upcoming = _league(db)
    engine = IntelligenceEngine(db)
    rows = [engine._fixture_dict(fixture) for fixture in engine._finished_fixtures()]

    snapshot = engine.build_feature_snapshot(upcoming[0], rows, commit=False)
    assert snapshot in db.new
    assert db.query(FeatureSnapshot).count() == 0

    db.commit()
    assert db.query(FeatureSnapshot).count() == 1

    engine.build_feature_snapshot(upcoming[0], rows, commit=True)
    assert db.query(FeatureSnapshot).count() == 1


def test_engine_operations_are_serialized_inside_process(db):
    import threading
    import time
    from app.intelligence.engine import serialized_engine_operation

    class Probe:
        def __init__(self):
            self.db = db
            self.active = 0
            self.max_active = 0

        @serialized_engine_operation
        def work(self):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            self.active -= 1
            return True

    probe = Probe()
    threads = [threading.Thread(target=probe.work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert probe.max_active == 1


def test_metrics_use_published_snapshot_probability():
    from app.api.routes.intelligence import _published_probability

    prediction = Prediction(
        market="1X2",
        selection="HOME",
        probability=0.50,
        confidence=0.80,
        data_quality=0.90,
        rationale={
            "first_probability": 0.60,
            "published_snapshot": {"probability": 0.58},
        },
    )
    assert _published_probability(prediction) == 0.58


def test_endpoint_models_is_accessible(client):
    response = client.get("/api/v1/intelligence/models")
    assert response.status_code == 200


def test_engine_does_not_publish_predictions_without_enough_history(db):
    from app.core.config import Settings

    engine = IntelligenceEngine(db)
    engine.settings = Settings(intelligence_min_history_matches=30)

    run = engine.generate_predictions()

    assert run.status == EngineRunStatus.PARTIAL
    assert run.records_created == 0
    assert "Insufficient historical matches" in run.error_message
    assert db.query(Prediction).count() == 0
