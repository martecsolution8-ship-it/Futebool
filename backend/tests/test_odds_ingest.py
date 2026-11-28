from datetime import datetime, timezone

from sqlalchemy import func, select

from app.intelligence.odds_lookup import quote_for
from app.models.odds import FixtureOdds
from app.models.sports import Competition, Fixture, FixtureStatus, Season, Team
from app.services.odds_ingest import OddsIngestService
from app.services.odds_providers import THE_ODDS_API, parse_the_odds_api_event
from tests.test_odds_providers import THE_ODDS_EVENT


def _seed(db):
    competition = Competition(name="Premier League", country="England", code="PL", external_ids={})
    db.add(competition)
    arsenal, chelsea = Team(name="Arsenal FC", external_ids={}), Team(name="Chelsea FC", external_ids={})
    db.add_all([arsenal, chelsea])
    db.flush()
    season = Season(competition_id=competition.id, label="2026", active=True, external_ids={})
    db.add(season)
    db.flush()
    fixture = Fixture(
        competition_id=competition.id, season_id=season.id, home_team_id=arsenal.id, away_team_id=chelsea.id,
        provider="football-data", provider_fixture_id="1", kickoff_at=datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc),
        status=FixtureStatus.SCHEDULED, statistics={}, raw_hash="x",
    )
    db.add(fixture)
    db.commit()
    return fixture


def test_ingest_attaches_odds_and_never_creates_fixtures(db):
    fixture = _seed(db)
    event = parse_the_odds_api_event(THE_ODDS_EVENT)
    other = parse_the_odds_api_event({**THE_ODDS_EVENT, "home_team": "Equipa X", "away_team": "Equipa Y",
                                      "bookmakers": []})

    result = OddsIngestService(db, THE_ODDS_API).ingest([event, other])

    assert result.matched == 1 and len(result.unmatched_events) == 1
    assert db.scalar(select(func.count()).select_from(Fixture)) == 1
    assert db.scalar(select(func.count()).select_from(Team)) == 2
    assert db.scalar(select(func.count()).select_from(FixtureOdds).where(FixtureOdds.fixture_id == fixture.id)) > 0

    again = OddsIngestService(db, THE_ODDS_API).ingest([event])
    assert again.odds_changed == 0  # idempotente


def test_engine_quote_uses_fresh_real_odds(db):
    fixture = _seed(db)
    OddsIngestService(db, THE_ODDS_API).ingest([parse_the_odds_api_event(THE_ODDS_EVENT)])

    quote = quote_for(db, {}, fixture.id, "1X2", "HOME")
    assert quote is not None and quote["bookmaker"] == "pinnacle" and quote["price"] == 2.1  # sem bet365 -> pinnacle
    assert quote_for(db, {}, fixture.id, "BTTS", "YES") is None
