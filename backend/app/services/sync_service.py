import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.sports import (
    ApiCache,
    ApiUsage,
    Competition,
    Fixture,
    FixtureStatus,
    Player,
    Season,
    SyncRun,
    SyncStatus,
    Team,
)
from app.services.sports_adapters import ApiFootballAdapter, FootballDataAdapter


class SportsSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def _daily_limit(self, provider: str) -> int:
        if provider == "api-football":
            return self.settings.api_football_daily_limit
        if provider == "football-data":
            return self.settings.football_data_daily_limit
        return 0

    def _assert_daily_limit(self, provider: str) -> None:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count = self.db.scalar(
            select(func.count(ApiUsage.id)).where(ApiUsage.provider == provider, ApiUsage.request_at >= start)
        ) or 0
        if count >= self._daily_limit(provider):
            raise RuntimeError(f"Daily API limit reached for {provider}")

    def _assert_request_limit(self, provider: str) -> None:
        self._assert_daily_limit(provider)
        if provider == "football-data":
            minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
            count = self.db.scalar(
                select(func.count(ApiUsage.id)).where(
                    ApiUsage.provider == provider,
                    ApiUsage.request_at >= minute_ago,
                )
            ) or 0
            if count >= self.settings.football_data_requests_per_minute:
                raise RuntimeError(f"Request limit reached for {provider} (per minute)")

    def _record_usage(
        self,
        provider: str,
        endpoint: str,
        success: bool,
        response_bytes: int = 0,
        error_message: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.db.add(ApiUsage(
            provider=provider,
            endpoint=endpoint,
            http_status=http_status if http_status is not None else (200 if success else None),
            success=success,
            response_bytes=response_bytes,
            error_message=error_message,
        ))
        self.db.commit()

    def _adapter(self, provider: str, client: httpx.AsyncClient):
        if provider == "api-football":
            if not self.settings.api_football_key:
                raise RuntimeError("API_FOOTBALL_KEY is not configured")
            return ApiFootballAdapter(
                client,
                self.settings.api_football_key,
                base_url=self.settings.api_football_base_url,
            )
        if provider == "football-data":
            token = self.settings.football_data_token or self.settings.football_data_key
            if not token:
                raise RuntimeError("FOOTBALL_DATA_TOKEN is not configured")
            return FootballDataAdapter(
                client,
                token,
                base_url=self.settings.football_data_base_url,
            )
        raise ValueError("Unsupported provider")

    def _record_provider_error(self, provider: str, endpoint: str, error: Exception) -> None:
        if "limit reached" in str(error).lower():
            return
        response = error.response if isinstance(error, httpx.HTTPStatusError) else None
        http_status = response.status_code if response is not None else (
            200 if isinstance(error, RuntimeError) and "rejected the" in str(error) else None
        )
        self._record_usage(
            provider,
            endpoint,
            False,
            response_bytes=len(response.content) if response is not None else 0,
            error_message=f"{type(error).__name__}: {error}"[:4000],
            http_status=http_status,
        )

    def _normalize_datetime(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _get_or_create_competition(self, item: dict[str, Any]) -> Competition:
        provider = item["_provider"]
        provider_id = item["provider_id"]
        competition = self.db.scalars(
            select(Competition).where(Competition.external_ids[provider].as_string() == provider_id)
        ).first()
        if competition is None:
            for candidate in self.db.scalars(select(Competition)).all():
                same_code = bool(item.get("code") and candidate.code and item["code"].upper() == candidate.code.upper())
                same_name = candidate.name.strip().casefold() == item["name"].strip().casefold()
                same_country = not item.get("country") or not candidate.country or item["country"] == candidate.country
                if (same_code or same_name) and same_country:
                    competition = candidate
                    break
        if competition is None:
            competition = Competition(
                name=item["name"],
                country=item.get("country"),
                code=item.get("code"),
                type=item.get("type", "league"),
                external_ids={provider: provider_id},
            )
            self.db.add(competition)
            self.db.flush()
        else:
            competition.name = item["name"]
            competition.external_ids = {**competition.external_ids, provider: provider_id}
        return competition

    def _get_or_create_team(self, item: dict[str, Any], provider: str) -> Team:
        provider_id = item["provider_id"]
        team = self.db.scalars(
            select(Team).where(Team.external_ids[provider].as_string() == provider_id)
        ).first()
        if team is None:
            for candidate in self.db.scalars(select(Team)).all():
                if candidate.name.strip().casefold() == item["name"].strip().casefold():
                    team = candidate
                    break
        if team is None:
            team = Team(
                name=item["name"],
                crest_url=item.get("crest_url"),
                external_ids={provider: provider_id},
            )
            self.db.add(team)
            self.db.flush()
        else:
            team.name = item["name"]
            team.crest_url = item.get("crest_url") or team.crest_url
            team.external_ids = {**team.external_ids, provider: provider_id}
        return team

    async def sync_competitions(self, provider: str) -> SyncRun:
        run = SyncRun(provider=provider, operation="competitions", status=SyncStatus.RUNNING)
        self.db.add(run)
        self.db.commit()

        try:
            cache_key = f"{provider}:competitions"
            async with httpx.AsyncClient(timeout=30.0) as client:
                adapter = self._adapter(provider, client)
                cached = CacheService(self.db).get(cache_key)
                if cached:
                    records = cached["records"]
                else:
                    self._assert_request_limit(provider)
                    records = await adapter.get_competitions()
                    self._record_usage(
                        provider,
                        f"{provider}/competitions",
                        True,
                        response_bytes=adapter.last_response_bytes,
                        http_status=adapter.last_http_status,
                    )
                    CacheService(self.db).set(cache_key, {"records": records}, self.settings.data_cache_ttl_seconds)
            adapter_name = adapter.provider_name
            run.records_received = len(records)
            written = 0
            for item in records:
                item = {**item, "_provider": adapter_name}
                competition = self._get_or_create_competition(item)
                for season_item in item.get("seasons", []):
                    label = season_item["label"]
                    season = self.db.scalar(
                        select(Season).where(Season.competition_id == competition.id, Season.label == label)
                    )
                    if season is None:
                        season = Season(
                            competition_id=competition.id,
                            label=label,
                            start_date=self._parse_optional_date(season_item.get("start")),
                            end_date=self._parse_optional_date(season_item.get("end")),
                            active=season_item.get("active", False),
                            external_ids={adapter_name: item["provider_id"]},
                        )
                        self.db.add(season)
                    else:
                        season.start_date = self._parse_optional_date(season_item.get("start"))
                        season.end_date = self._parse_optional_date(season_item.get("end"))
                        season.active = season_item.get("active", False)
                        season.external_ids = {**season.external_ids, adapter_name: item["provider_id"]}
                written += 1
            self.db.commit()
            run.records_written = written
            run.status = SyncStatus.SUCCESS
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run.status = SyncStatus.FAILED
            run.error_count = 1
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            run.completed_at = datetime.now(timezone.utc)
            self.db.add(run)
            self.db.commit()
            self._record_provider_error(provider, f"{provider}/competitions", exc)
        return run

    def _parse_optional_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        return self._normalize_datetime(value)

    async def sync_players(self, provider: str, team_id: uuid.UUID) -> SyncRun:
        run = SyncRun(provider=provider, operation="players", status=SyncStatus.RUNNING, metadata_json={"team_id": str(team_id)})
        self.db.add(run)
        self.db.commit()
        team = self.db.get(Team, team_id)
        if team is None:
            run.status = SyncStatus.FAILED
            run.error_message = "Team not found"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run
        external_id = team.external_ids.get(provider)
        if not external_id:
            run.status = SyncStatus.FAILED
            run.error_message = f"Team is not mapped to provider {provider}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        try:
            self._assert_request_limit(provider)
            async with httpx.AsyncClient(timeout=30.0) as client:
                adapter = self._adapter(provider, client)
                records = await adapter.get_players(str(external_id))
            self._record_usage(
                provider,
                f"{provider}/players/{external_id}",
                True,
                response_bytes=adapter.last_response_bytes,
                http_status=adapter.last_http_status,
            )

            written = 0
            for item in records:
                player = self.db.scalar(
                    select(Player).where(Player.external_ids[provider].as_string() == item["provider_id"])
                )
                if player is None:
                    player = Player(
                        team_id=team.id,
                        name=item["name"],
                        position=item.get("position"),
                        nationality=item.get("nationality"),
                        external_ids={provider: item["provider_id"]},
                    )
                    self.db.add(player)
                    written += 1
                else:
                    player.team_id = team.id
                    player.name = item["name"]
                    player.position = item.get("position")
                    player.nationality = item.get("nationality")
                    player.external_ids = {**player.external_ids, provider: item["provider_id"]}
                    written += 1
            self.db.commit()
            run.records_received = len(records)
            run.records_written = written
            run.status = SyncStatus.SUCCESS
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run.status = SyncStatus.FAILED
            run.error_count = 1
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            run.completed_at = datetime.now(timezone.utc)
            self.db.add(run)
            self.db.commit()
            self._record_provider_error(provider, f"{provider}/players/{external_id}", exc)
        return run

    async def sync_fixtures(self, provider: str, competition_id: uuid.UUID, season_label: str) -> SyncRun:
        return await self._sync_fixtures_for_external(provider, competition_id, season_label)

    async def _sync_fixtures_for_external(self, provider: str, competition_id: uuid.UUID, season_label: str) -> SyncRun:
        run = SyncRun(provider=provider, operation="fixtures", status=SyncStatus.RUNNING, metadata_json={"competition_id": str(competition_id), "season": season_label})
        self.db.add(run)
        self.db.commit()
        competition = self.db.get(Competition, competition_id)
        if competition is None:
            run.status = SyncStatus.FAILED
            run.error_message = "Competition not found"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        season = self.db.scalar(select(Season).where(Season.competition_id == competition.id, Season.label == season_label))
        if season is None:
            run.status = SyncStatus.FAILED
            run.error_message = "Season not found"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        external_id = competition.external_ids.get(provider)
        if not external_id:
            run.status = SyncStatus.FAILED
            run.error_message = f"Competition is not mapped to provider {provider}"
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return run

        try:
            # Fixtures are stateful: never return a cached FT/LIVE score for existing games.
            self._assert_request_limit(provider)
            async with httpx.AsyncClient(timeout=30.0) as client:
                adapter = self._adapter(provider, client)
                records = await adapter.get_fixtures(str(external_id), season_label)
            self._record_usage(
                provider,
                f"{provider}/fixtures/{external_id}/{season_label}",
                True,
                response_bytes=adapter.last_response_bytes,
                http_status=adapter.last_http_status,
            )

            run.records_received = len(records)
            written = 0
            for item in records:
                home_team = self._get_or_create_team(item["home_team"], provider)
                away_team = self._get_or_create_team(item["away_team"], provider)
                raw_hash = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
                fixture = self.db.scalar(
                    select(Fixture).where(Fixture.provider == provider, Fixture.provider_fixture_id == item["provider_id"])
                )
                if fixture is None:
                    fixture = Fixture(
                        competition_id=competition.id,
                        season_id=season.id,
                        home_team_id=home_team.id,
                        away_team_id=away_team.id,
                        provider=provider,
                        provider_fixture_id=item["provider_id"],
                        round=item.get("round"),
                        kickoff_at=self._normalize_datetime(item["kickoff_at"]),
                        status=FixtureStatus(item["status"]),
                        home_score=item.get("home_score"),
                        away_score=item.get("away_score"),
                        home_score_halftime=item.get("home_score_halftime"),
                        away_score_halftime=item.get("away_score_halftime"),
                        statistics=item.get("statistics", {}),
                        raw_hash=raw_hash,
                    )
                    self.db.add(fixture)
                    written += 1
                elif fixture.raw_hash != raw_hash or (
                    fixture.competition_id != competition.id
                    or fixture.season_id != season.id
                    or fixture.home_team_id != home_team.id
                    or fixture.away_team_id != away_team.id
                ):
                    fixture.competition_id = competition.id
                    fixture.season_id = season.id
                    fixture.home_team_id = home_team.id
                    fixture.away_team_id = away_team.id
                    fixture.round = item.get("round")
                    fixture.kickoff_at = self._normalize_datetime(item["kickoff_at"])
                    fixture.status = FixtureStatus(item["status"])
                    fixture.home_score = item.get("home_score")
                    fixture.away_score = item.get("away_score")
                    fixture.home_score_halftime = item.get("home_score_halftime")
                    fixture.away_score_halftime = item.get("away_score_halftime")
                    fixture.statistics = item.get("statistics", {})
                    fixture.raw_hash = raw_hash
                    written += 1
            self.db.commit()
            run.records_written = written
            run.status = SyncStatus.SUCCESS
            run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            run.status = SyncStatus.FAILED
            run.error_count = 1
            run.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            run.completed_at = datetime.now(timezone.utc)
            self.db.add(run)
            self.db.commit()
            self._record_provider_error(provider, f"{provider}/fixtures/{external_id}/{season_label}", exc)
        return run


class CacheService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self.db.scalar(select(ApiCache).where(ApiCache.cache_key == key, ApiCache.expires_at > datetime.now(timezone.utc)))
        return entry.payload if entry else None

    def set(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        entry = self.db.scalar(select(ApiCache).where(ApiCache.cache_key == key))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        if entry:
            entry.payload = payload
            entry.expires_at = expires_at
        else:
            self.db.add(ApiCache(cache_key=key, payload=payload, expires_at=expires_at))
        self.db.commit()
