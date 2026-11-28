from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.odds import FixtureOdds, TeamAlias
from app.models.sports import Competition, Fixture, FixtureStatus, Season, Team
from app.services.football_data_uk_importer import FootballDataUkImporter
from app.services.football_data_uk_parser import LEAGUES, SOURCE

CSV = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,B365H,B365D,B365A,B365CH,B365CD,B365CA\n"
    "E0,16/08/2025,20:00,Man United,Liverpool,1,2,0,1,2.5,3.4,2.7,2.4,3.5,2.8\n"
    "E0,23/08/2025,15:00,Liverpool,Man United,,,,,1.8,3.8,4.5,,,\n"
).encode()


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_import_creates_everything_and_is_idempotent(db):
    importer = FootballDataUkImporter(db)
    first = importer.import_file(LEAGUES["E0"], "2025", CSV, force=True)
    assert first.status == "SUCCESS" and first.fixtures_created == 2 and first.odds_written == 9
    assert _count(db, Competition) == 1 and _count(db, Season) == 1 and _count(db, Team) == 2
    finished = db.scalar(select(Fixture).where(Fixture.home_score.is_not(None)))
    assert finished.status == FixtureStatus.FINISHED and finished.home_score_halftime == 0

    second = FootballDataUkImporter(db).import_file(LEAGUES["E0"], "2025", CSV, force=True)
    assert second.fixtures_created == 0 and second.odds_written == 0
    assert _count(db, Fixture) == 2 and _count(db, FixtureOdds) == 9


def test_unchanged_file_is_skipped(db):
    FootballDataUkImporter(db).import_file(LEAGUES["E0"], "2025", CSV)
    again = FootballDataUkImporter(db).import_file(LEAGUES["E0"], "2025", CSV)
    assert again.status == "SKIPPED"


def test_price_update_is_applied(db):
    FootballDataUkImporter(db).import_file(LEAGUES["E0"], "2025", CSV)
    changed = CSV.replace(b",2.5,3.4,2.7,", b",2.6,3.4,2.7,")
    result = FootballDataUkImporter(db).import_file(LEAGUES["E0"], "2025", changed)
    assert result.odds_written == 1 and result.fixtures_created == 0


def test_existing_teams_and_foreign_fixture_are_reused(db):
    competition = Competition(name="Premier League", country="England", code="PL", external_ids={"football-data": "2021"})
    db.add(competition)
    united = Team(name="Manchester United FC", external_ids={"football-data": "66"})
    liverpool = Team(name="Liverpool FC", external_ids={"football-data": "64"})
    db.add_all([united, liverpool])
    db.flush()
    season = Season(competition_id=competition.id, label="2025", active=True, external_ids={})
    db.add(season)
    db.flush()
    foreign = Fixture(
        competition_id=competition.id, season_id=season.id, home_team_id=united.id, away_team_id=liverpool.id,
        provider="football-data", provider_fixture_id="999", kickoff_at=datetime(2025, 8, 16, 19, 0, tzinfo=timezone.utc),
        status=FixtureStatus.FINISHED, home_score=1, away_score=2, statistics={}, raw_hash="x",
    )
    db.add(foreign)
    db.commit()

    result = FootballDataUkImporter(db).import_file(LEAGUES["E0"], "2025", CSV, force=True)

    assert result.new_teams == []
    assert _count(db, Team) == 2 and _count(db, Competition) == 1
    assert result.fixtures_attached == 1 and result.fixtures_created == 1  # o 2.º jogo ainda não existia
    assert _count(db, Fixture) == 2
    assert db.scalar(select(func.count()).select_from(FixtureOdds).where(FixtureOdds.fixture_id == foreign.id)) == 6
    assert db.get(Fixture, foreign.id).provider == "football-data"  # não foi alterado
    assert _count(db, TeamAlias) == 2
    assert db.scalar(select(TeamAlias.source).limit(1)) == SOURCE
