import asyncio

import httpx
import pytest

from app.models.sports import ApiCache, ApiUsage, Competition, Fixture, FixtureStatus, Season, Team
from app.services.sports_adapters import ApiFootballAdapter, FootballDataAdapter
from app.services.sync_service import CacheService, SportsSyncService


def test_api_football_adapter_normalizes_competitions():
    async def handler(request: httpx.Request):
        assert request.headers["x-apisports-key"] == "test-key"
        return httpx.Response(200, json={"response": [{
            "league": {"id": 39, "name": "Premier League", "code": "PL", "type": "League"},
            "country": {"name": "England"},
            "seasons": [{"year": 2026, "start": "2026-08-01", "end": "2027-05-31", "current": True}],
        }]})

    async def run_test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = ApiFootballAdapter(client, "test-key")
            return await adapter.get_competitions()

    result = asyncio.run(run_test())

    assert result[0]["provider_id"] == "39"
    assert result[0]["name"] == "Premier League"
    assert result[0]["seasons"][0]["active"] is True


def test_api_football_adapter_normalizes_fixture():
    async def handler(request: httpx.Request):
        return httpx.Response(200, json={"response": [{
            "fixture": {"id": 1001, "date": "2026-11-21T20:00:00Z", "status": {"short": "FT"}},
            "league": {"id": 39, "season": 2026, "round": "Regular Season - 12"},
            "teams": {"home": {"id": 10, "name": "Team A"}, "away": {"id": 20, "name": "Team B"}},
            "goals": {"home": 2, "away": 1},
            "score": {"halftime": {"home": 1, "away": 0}},
        }]})

    async def run_test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ApiFootballAdapter(client, "test-key").get_fixtures("39", "2026")

    result = asyncio.run(run_test())

    assert result[0]["provider_id"] == "1001"
    assert result[0]["status"] == "FINISHED"
    assert result[0]["home_score"] == 2


def test_api_football_adapter_normalizes_interval_suspended_and_unknown():
    def normalize(status):
        item = {
            "fixture": {"id": 1002, "date": "2026-11-22T20:00:00Z", "status": {"short": status}},
            "league": {"id": 135, "season": 2026},
            "teams": {"home": {"id": 10, "name": "Home"}, "away": {"id": 20, "name": "Away"}},
            "goals": {"home": None, "away": None},
            "score": {"halftime": {"home": None, "away": None}},
        }
        return ApiFootballAdapter._normalize_fixture(item)

    assert normalize("HT")["status"] == "INTERVAL"
    assert normalize("SUSP")["status"] == "SUSPENDED"
    assert normalize("NEW_STATUS")["status"] == "UNKNOWN"


def test_football_data_adapter_normalizes_fixture():
    async def handler(request: httpx.Request):
        assert request.url.params["season"] == "2026"
        return httpx.Response(200, json={
            "competition": {"id": 2021, "name": "Test League"},
            "matches": [{
                "id": 55,
                "utcDate": "2026-11-22T14:00:00Z",
                "status": "TIMED",
                "matchday": 8,
                "season": {"startDate": "2026-08-01", "endDate": "2027-05-31"},
                "homeTeam": {"id": 30, "name": "Team C"},
                "awayTeam": {"id": 40, "name": "Team D"},
                "score": {"fullTime": {"home": None, "away": None}, "halfTime": {"home": None, "away": None}},
            }],
        })

    async def run_test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FootballDataAdapter(client, "test-key").get_fixtures("2021", "2026")

    result = asyncio.run(run_test())

    assert result[0]["provider_id"] == "55"
    assert result[0]["status"] == "SCHEDULED"
    assert result[0]["round"] == "Matchday 8"


def test_football_data_adapter_returns_current_season():
    async def handler(request: httpx.Request):
        assert request.headers["x-auth-token"] == "test-key"
        return httpx.Response(200, json={"competitions": [{
            "id": 2021,
            "name": "Premier League",
            "code": "PL",
            "area": {"name": "England"},
            "currentSeason": {"startDate": "2026-08-01", "endDate": "2027-05-31"},
        }]})

    async def run_test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FootballDataAdapter(client, "test-key").get_competitions()

    result = asyncio.run(run_test())
    assert result[0]["seasons"] == [{
        "label": "2026",
        "start": "2026-08-01",
        "end": "2027-05-31",
        "active": True,
    }]


def test_api_football_rejects_provider_errors_even_with_http_200():
    async def handler(request: httpx.Request):
        return httpx.Response(200, json={"errors": {"token": "Invalid API key"}, "response": []})

    async def run_test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ApiFootballAdapter(client, "test-key").get_competitions()

    with pytest.raises(RuntimeError, match="rejected"):
        asyncio.run(run_test())


def test_api_football_adapter_normalizes_players():
    async def handler(request: httpx.Request):
        assert request.url.params["team"] == "10"
        return httpx.Response(200, json={"response": [{"players": [{
            "id": 501,
            "name": "Player One",
            "position": "Midfielder",
            "nationality": "Angola",
        }]}]})

    async def run_test():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ApiFootballAdapter(client, "test-key").get_players("10")

    result = asyncio.run(run_test())
    assert result[0]["provider_id"] == "501"
    assert result[0]["name"] == "Player One"


def test_cache_stores_and_expires_entries(db):
    cache = CacheService(db)
    cache.set("competition-list", {"records": [{"id": "1"}]}, ttl_seconds=60)

    assert cache.get("competition-list") == {"records": [{"id": "1"}]}

    entry = db.query(ApiCache).filter(ApiCache.cache_key == "competition-list").one()
    entry.expires_at = entry.expires_at.replace(year=2000)
    db.commit()

    assert cache.get("competition-list") is None


def test_daily_usage_limit_is_enforced(db):
    from app.core.config import Settings

    service = SportsSyncService(db)
    service.settings = Settings(api_football_daily_limit=1)
    db.add(ApiUsage(provider="api-football", endpoint="test", http_status=200, success=True, response_bytes=10))
    db.commit()

    with pytest.raises(RuntimeError, match="Daily API limit reached"):
        service._assert_daily_limit("api-football")


def test_football_data_minute_limit_is_enforced(db):
    from datetime import datetime, timezone

    from app.core.config import Settings

    service = SportsSyncService(db)
    service.settings = Settings(football_data_requests_per_minute=1)
    db.add(ApiUsage(
        provider="football-data",
        endpoint="test",
        http_status=200,
        success=True,
        response_bytes=10,
        request_at=datetime.now(timezone.utc),
    ))
    db.commit()

    with pytest.raises(RuntimeError, match="per minute"):
        service._assert_request_limit("football-data")


def test_football_data_token_fallback_is_supported(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("FOOTBALL_DATA_TOKEN", "test-token")
    monkeypatch.setenv("FOOTBALL_DATA_KEY", "")
    settings = Settings()
    assert bool(settings.football_data_token) is True
    assert (settings.football_data_token or settings.football_data_key) == "test-token"


def test_sync_service_uses_configured_provider_base_urls(db):
    import httpx

    from app.core.config import Settings

    service = SportsSyncService(db)
    service.settings = Settings(
        api_football_key="api-key",
        api_football_base_url="https://api-football.example/v3",
        football_data_token="data-token",
        football_data_base_url="https://football-data.example/v4",
    )

    async def run_test():
        async with httpx.AsyncClient() as client:
            football = service._adapter("api-football", client)
            data = service._adapter("football-data", client)
            return football.base_url, data.base_url

    assert asyncio.run(run_test()) == (
        "https://api-football.example/v3",
        "https://football-data.example/v4",
    )


def test_fixture_endpoint_returns_persisted_data(client, db):
    response = client.get("/api/v1/data/fixtures")
    assert response.status_code == 200
    assert response.json() == []


def test_fixture_endpoint_lists_upcoming_games_before_results(client, db):
    from datetime import datetime, timedelta, timezone

    competition = Competition(name="Liga de teste", external_ids={})
    db.add(competition)
    db.flush()
    season = Season(competition_id=competition.id, label="2026", external_ids={}, active=True)
    home = Team(name="Casa", external_ids={})
    away = Team(name="Fora", external_ids={})
    db.add_all([season, home, away])
    db.flush()
    common = {
        "competition_id": competition.id,
        "season_id": season.id,
        "home_team_id": home.id,
        "away_team_id": away.id,
        "provider": "test",
        "statistics": {},
        "raw_hash": "test",
    }
    upcoming = Fixture(
        **common,
        provider_fixture_id="upcoming",
        kickoff_at=datetime.now(timezone.utc) + timedelta(days=1),
        status=FixtureStatus.SCHEDULED,
    )
    finished = Fixture(
        **common,
        provider_fixture_id="finished",
        kickoff_at=datetime.now(timezone.utc) - timedelta(days=1),
        status=FixtureStatus.FINISHED,
        home_score=1,
        away_score=0,
    )
    db.add_all([finished, upcoming])
    db.commit()

    response = client.get("/api/v1/data/fixtures")
    assert response.status_code == 200
    assert [item["provider_fixture_id"] for item in response.json()] == ["upcoming", "finished"]


def test_sync_persists_real_provider_records_for_frontend(client, db, monkeypatch):
    from sqlalchemy import select

    from app.core.config import Settings

    class FakeApiFootballAdapter:
        provider_name = "api-football"
        last_http_status = 200
        last_response_bytes = 100

        def __init__(self, client, api_key, base_url):
            assert api_key == "test-key"
            assert base_url == "https://v3.football.api-sports.io"

        async def get_competitions(self):
            return [{
                "provider_id": "39",
                "name": "Premier League",
                "code": "PL",
                "country": "England",
                "type": "League",
                "seasons": [{"label": "2026", "start": "2026-08-01", "end": "2027-05-31", "active": True}],
            }]

        async def get_fixtures(self, competition_id, season):
            assert competition_id == "39"
            assert season == "2026"
            return [{
                "provider_id": "1001",
                "kickoff_at": "2026-11-25T18:00:00Z",
                "status": "SCHEDULED",
                "home_team": {"provider_id": "10", "name": "Team A", "crest_url": None},
                "away_team": {"provider_id": "20", "name": "Team B", "crest_url": None},
                "home_score": None,
                "away_score": None,
                "home_score_halftime": None,
                "away_score_halftime": None,
                "round": "Round 12",
                "statistics": {},
            }]

    monkeypatch.setattr("app.services.sync_service.ApiFootballAdapter", FakeApiFootballAdapter)
    service = SportsSyncService(db)
    service.settings = Settings(api_football_key="test-key")

    async def synchronize():
        competitions_run = await service.sync_competitions("api-football")
        assert competitions_run.status.value == "SUCCESS", competitions_run.error_message
        competition = db.scalars(select(Competition)).one()
        fixtures_run = await service.sync_fixtures("api-football", competition.id, "2026")
        return competitions_run, fixtures_run

    competitions_run, fixtures_run = asyncio.run(synchronize())
    assert competitions_run.status.value == "SUCCESS"
    assert fixtures_run.status.value == "SUCCESS"

    fixtures = client.get("/api/v1/data/fixtures").json()
    teams = client.get("/api/v1/data/teams").json()
    competitions = client.get("/api/v1/data/competitions").json()
    assert len(fixtures) == 1
    assert fixtures[0]["provider_fixture_id"] == "1001"
    assert len(teams) == 2
    assert competitions[0]["name"] == "Premier League"


def test_unsupported_provider_is_rejected(client, db):
    from app.core.security import create_access_token, hash_password
    from app.models.user import User, UserRole

    admin = User(
        email="phase2-admin@marbet.ao",
        display_name="Phase 2 Admin",
        password_hash=hash_password("UmaSenhaSegura123!"),
        role=UserRole.ADMIN,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    response = client.post(
        "/api/v1/data/sync/competitions?provider=unknown",
        headers={"Authorization": f"Bearer {create_access_token(str(admin.id))}"},
    )
    assert response.status_code == 422


def test_fixture_endpoint_filters_today_and_recent_results(client, db):
    from datetime import datetime, timedelta, timezone

    competition = Competition(name="Liga de filtros", external_ids={})
    db.add(competition)
    db.flush()
    season = Season(competition_id=competition.id, label="2026", external_ids={}, active=True)
    home = Team(name="Casa F", external_ids={})
    away = Team(name="Fora F", external_ids={})
    db.add_all([season, home, away])
    db.flush()
    common = {"competition_id": competition.id, "season_id": season.id, "home_team_id": home.id,
              "away_team_id": away.id, "provider": "test", "statistics": {}, "raw_hash": "test"}
    now = datetime.now(timezone.utc)
    # 5 jogos futuros agendados + 1 resultado: com limit=3 o resultado não cabia no pedido antigo
    for i in range(5):
        db.add(Fixture(**common, provider_fixture_id=f"future-{i}", kickoff_at=now + timedelta(days=2 + i), status=FixtureStatus.SCHEDULED))
    db.add(Fixture(**common, provider_fixture_id="today", kickoff_at=now + timedelta(hours=1), status=FixtureStatus.SCHEDULED))
    db.add(Fixture(**common, provider_fixture_id="done", kickoff_at=now - timedelta(days=1), status=FixtureStatus.FINISHED, home_score=2, away_score=1))
    db.commit()

    assert [f["provider_fixture_id"] for f in client.get("/api/v1/data/fixtures", params={"limit": 3}).json()] == ["today", "future-0", "future-1"]
    finished = client.get("/api/v1/data/fixtures", params={"status": "FINISHED", "order": "desc", "limit": 3}).json()
    assert [f["provider_fixture_id"] for f in finished] == ["done"]
    window = client.get("/api/v1/data/fixtures", params={
        "date_from": (now - timedelta(minutes=1)).isoformat(), "date_to": (now + timedelta(hours=2)).isoformat(), "order": "asc"}).json()
    assert [f["provider_fixture_id"] for f in window] == ["today"]
