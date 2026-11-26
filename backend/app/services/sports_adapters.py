from abc import ABC, abstractmethod
from typing import Any


class SportsDataAdapter(ABC):
    provider_name: str

    def _track_response(self, response) -> None:
        self.last_http_status = response.status_code
        self.last_response_bytes = len(response.content)

    @abstractmethod
    async def get_competitions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_fixtures(self, competition_external_id: str, season: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_players(self, team_external_id: str) -> list[dict[str, Any]]:
        return []


class ApiFootballAdapter(SportsDataAdapter):
    provider_name = "api-football"

    def __init__(self, client, api_key: str, base_url: str = "https://v3.football.api-sports.io"):
        self.client = client
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def get_competitions(self) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("API_FOOTBALL_KEY is not configured")
        response = await self.client.get(
            f"{self.base_url}/leagues",
            headers={"x-apisports-key": self.api_key},
        )
        self._track_response(response)
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"API-Football rejected the competitions request: {payload['errors']}")
        return [
            {
                "provider_id": str(item["league"]["id"]),
                "name": item["league"]["name"],
                "code": item["league"].get("code"),
                "country": item["country"].get("name") if item.get("country") else None,
                "type": item["league"].get("type", "league"),
                "seasons": [
                    {
                        "label": str(season["year"]),
                        "start": season.get("start"),
                        "end": season.get("end"),
                        "active": season.get("current", False),
                    }
                    for season in item.get("seasons", [])
                ],
            }
            for item in payload.get("response", [])
        ]

    async def get_fixtures(self, competition_external_id: str, season: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("API_FOOTBALL_KEY is not configured")
        response = await self.client.get(
            f"{self.base_url}/fixtures",
            params={"league": competition_external_id, "season": season},
            headers={"x-apisports-key": self.api_key},
        )
        self._track_response(response)
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"API-Football rejected the fixtures request: {payload['errors']}")
        return [self._normalize_fixture(item) for item in payload.get("response", [])]

    async def get_players(self, team_external_id: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("API_FOOTBALL_KEY is not configured")
        response = await self.client.get(
            f"{self.base_url}/players/squads",
            params={"team": team_external_id},
            headers={"x-apisports-key": self.api_key},
        )
        self._track_response(response)
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"API-Football rejected the players request: {payload['errors']}")
        records = []
        for item in payload.get("response", []):
            for player in item.get("players", []):
                records.append({
                    "provider_id": str(player["id"]),
                    "name": player["name"],
                    "position": player.get("position"),
                    "nationality": player.get("nationality"),
                })
        return records

    @staticmethod
    def _normalize_fixture(item: dict[str, Any]) -> dict[str, Any]:
        fixture = item["fixture"]
        league = item["league"]
        teams = item["teams"]
        goals = item["goals"]
        score = item["score"]
        status = fixture["status"]["short"]
        status_map = {
            "NS": "SCHEDULED", "TBD": "SCHEDULED",
            "1H": "LIVE", "2H": "LIVE", "ET": "LIVE", "P": "LIVE", "BT": "LIVE", "LIVE": "LIVE",
            "HT": "INTERVAL", "INT": "INTERVAL",
            "FT": "FINISHED", "AET": "FINISHED", "PEN": "FINISHED",
            "PST": "POSTPONED",
            "CANC": "CANCELLED", "ABD": "CANCELLED",
            "SUSP": "SUSPENDED",
        }
        return {
            "provider_id": str(fixture["id"]),
            "competition_provider_id": str(league["id"]),
            "season": str(league["season"]),
            "round": league.get("round"),
            "kickoff_at": fixture["date"],
            "status": status_map.get(status, "UNKNOWN"),
            "home_team": {
                "provider_id": str(teams["home"]["id"]),
                "name": teams["home"]["name"],
                "crest_url": teams["home"].get("logo"),
            },
            "away_team": {
                "provider_id": str(teams["away"]["id"]),
                "name": teams["away"]["name"],
                "crest_url": teams["away"].get("logo"),
            },
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "home_score_halftime": score.get("halftime", {}).get("home"),
            "away_score_halftime": score.get("halftime", {}).get("away"),
            "statistics": item,
        }


class FootballDataAdapter(SportsDataAdapter):
    provider_name = "football-data"

    def __init__(self, client, api_key: str, base_url: str = "https://api.football-data.org/v4"):
        self.client = client
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def get_competitions(self) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("FOOTBALL_DATA_TOKEN is not configured")
        response = await self.client.get(
            f"{self.base_url}/competitions",
            headers={"X-Auth-Token": self.api_key},
        )
        self._track_response(response)
        response.raise_for_status()
        payload = response.json()
        records = []
        for item in payload.get("competitions", []):
            current_season = item.get("currentSeason")
            seasons = []
            if current_season and current_season.get("startDate"):
                seasons.append({
                    "label": str(current_season["startDate"][:4]),
                    "start": current_season.get("startDate"),
                    "end": current_season.get("endDate"),
                    "active": True,
                })
            records.append({
                "provider_id": str(item["id"]),
                "name": item["name"],
                "code": item.get("code"),
                "country": item.get("area", {}).get("name"),
                "type": item.get("type", "league"),
                "seasons": seasons,
            })
        return records

    async def get_fixtures(self, competition_external_id: str, season: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("FOOTBALL_DATA_TOKEN is not configured")
        response = await self.client.get(
            f"{self.base_url}/competitions/{competition_external_id}/matches",
            params={"season": season},
            headers={"X-Auth-Token": self.api_key},
        )
        self._track_response(response)
        response.raise_for_status()
        payload = response.json()
        competition = payload.get("competition", {})
        return [self._normalize_fixture(item, competition) for item in payload.get("matches", [])]

    async def get_players(self, team_external_id: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("FOOTBALL_DATA_TOKEN is not configured")
        response = await self.client.get(
            f"{self.base_url}/teams/{team_external_id}",
            headers={"X-Auth-Token": self.api_key},
        )
        self._track_response(response)
        response.raise_for_status()
        payload = response.json()
        return [{
            "provider_id": str(player["id"]),
            "name": player["name"],
            "position": player.get("position"),
            "nationality": player.get("nationality"),
        } for player in payload.get("squad", [])]

    @staticmethod
    def _normalize_fixture(item: dict[str, Any], competition: dict[str, Any]) -> dict[str, Any]:
        status_map = {
            "TIMED": "SCHEDULED", "SCHEDULED": "SCHEDULED", "IN_PLAY": "LIVE",
            "PAUSED": "INTERVAL", "FINISHED": "FINISHED", "POSTPONED": "POSTPONED",
            "CANCELLED": "CANCELLED", "SUSPENDED": "SUSPENDED",
        }
        score = item.get("score", {})
        full_time = score.get("fullTime", {})
        half_time = score.get("halfTime", {})
        season = item.get("season", {})
        return {
            "provider_id": str(item["id"]),
            "competition_provider_id": str(competition.get("id", "")),
            "season": str(season.get("startDate", "")[:4]) if season else "",
            "round": item.get("matchday") and f"Matchday {item['matchday']}" or None,
            "kickoff_at": item["utcDate"],
            "status": status_map.get(item.get("status"), "UNKNOWN"),
            "home_team": {
                "provider_id": str(item["homeTeam"]["id"]),
                "name": item["homeTeam"]["name"],
                "crest_url": item["homeTeam"].get("crest"),
            },
            "away_team": {
                "provider_id": str(item["awayTeam"]["id"]),
                "name": item["awayTeam"]["name"],
                "crest_url": item["awayTeam"].get("crest"),
            },
            "home_score": full_time.get("home"),
            "away_score": full_time.get("away"),
            "home_score_halftime": half_time.get("home"),
            "away_score_halftime": half_time.get("away"),
            "statistics": item,
        }
