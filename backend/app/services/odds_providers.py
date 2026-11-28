"""Conectores de odds (The Odds API e 5DollarFootballAPI): HTTP + normalização para ProviderEvent.

As funções `parse_*` são puras e testáveis sem rede.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Any

import httpx
import time

THE_ODDS_API = "the-odds-api"
FIVE_DOLLAR = "5dollar-football-api"


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderEvent:
    provider: str
    external_id: str
    kickoff: datetime
    home: str
    away: str
    odds: list[dict[str, Any]] = field(default_factory=list)


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 1.0 else None


def _iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- The Odds API

def parse_the_odds_api_event(item: dict[str, Any]) -> ProviderEvent | None:
    home, away = item.get("home_team"), item.get("away_team")
    if not home or not away or not item.get("commence_time"):
        return None

    odds: list[dict[str, Any]] = []
    pooled: dict[tuple[str, str, float], list[float]] = {}

    def add(bookmaker: str, market: str, selection: str, line: float, price: float | None) -> None:
        if price is None:
            return
        odds.append({"bookmaker": bookmaker, "market": market, "selection": selection,
                     "line": line, "kind": "pre_match", "price": price})
        pooled.setdefault((market, selection, line), []).append(price)

    for bookmaker in item.get("bookmakers", []):
        key = str(bookmaker.get("key", "")).lower()
        if not key:
            continue
        for market in bookmaker.get("markets", []):
            for outcome in market.get("outcomes", []):
                price = _price(outcome.get("price"))
                name = outcome.get("name")
                if market.get("key") == "h2h":
                    if name == home:
                        add(key, "1X2", "H", 0.0, price)
                    elif name == away:
                        add(key, "1X2", "A", 0.0, price)
                    elif str(name).lower() == "draw":
                        add(key, "1X2", "D", 0.0, price)
                elif market.get("key") == "totals" and outcome.get("point") == 2.5:
                    if str(name).lower() == "over":
                        add(key, "OU25", "OVER", 2.5, price)
                    elif str(name).lower() == "under":
                        add(key, "OU25", "UNDER", 2.5, price)

    for (market, selection, line), prices in pooled.items():
        if len(prices) >= 3:
            odds.append({"bookmaker": "market_avg", "market": market, "selection": selection,
                         "line": line, "kind": "pre_match", "price": round(mean(prices), 3)})
            odds.append({"bookmaker": "market_max", "market": market, "selection": selection,
                         "line": line, "kind": "pre_match", "price": max(prices)})

    return ProviderEvent(THE_ODDS_API, str(item.get("id", "")), _iso(item["commence_time"]), home, away, odds)


class TheOddsApiClient:
    def __init__(self, client: httpx.Client, api_key: str, base_url: str = "https://api.the-odds-api.com/v4",
                 regions: str = "eu", markets: str = "h2h"):
        self.client, self.api_key = client, api_key
        self.base_url = base_url.rstrip("/")
        self.regions, self.markets = regions, markets

    def fetch_sport(self, sport_key: str) -> tuple[list[ProviderEvent], dict[str, Any]]:
        response = self.client.get(
            f"{self.base_url}/sports/{sport_key}/odds",
            params={"apiKey": self.api_key, "regions": self.regions, "markets": self.markets,
                    "oddsFormat": "decimal", "dateFormat": "iso"},
        )
        meta: dict[str, Any] = {}
        for header, key in (("x-requests-remaining", "requests_remaining"), ("x-requests-used", "requests_used")):
            try:
                meta[key] = int(response.headers[header])
            except (KeyError, ValueError):
                pass
        if response.status_code in (404, 422):  # liga sem jogos/odds de momento
            return [], meta
        if response.status_code in (401, 429):
            raise ProviderError(f"The Odds API recusou o pedido ({response.status_code}): {response.text[:200]}")
        response.raise_for_status()
        events = [e for e in (parse_the_odds_api_event(item) for item in response.json()) if e is not None]
        return events, meta


# ---------------------------------------------------------------- 5DollarFootballAPI

_FD_KINDS = (("opening", "opening"), ("closing", "closing"))


def parse_five_dollar_odds(odds: dict[str, Any], bookmaker: str) -> list[dict[str, Any]]:
    """Normaliza o bloco de odds Bet365 de /fixtures/{id}/odds.

    Também aceita o formato simplificado usado por versões antigas da API e pelos
    testes locais. O endpoint actual devolve `data.bookmakers[].odds`.
    """
    if isinstance(odds.get("bookmakers"), list):
        normalized: list[dict[str, Any]] = []
        for book in odds["bookmakers"]:
            book_odds = book.get("odds") or {}
            slug = str(book.get("slug") or book.get("name") or bookmaker).lower().replace(" ", "_")
            normalized.extend(parse_five_dollar_odds(book_odds, slug))
        return normalized

    out: list[dict[str, Any]] = []

    def add(market: str, selection: str, line: float, kind: str, price: float | None) -> None:
        if price is not None:
            out.append({"bookmaker": bookmaker, "market": market, "selection": selection,
                        "line": round(line, 2), "kind": kind, "price": price})

    for source_kind, kind in _FD_KINDS:
        block = (odds.get("1x2") or {}).get(source_kind)
        if isinstance(block, dict):
            for key, selection in (("home", "H"), ("draw", "D"), ("away", "A")):
                add("1X2", selection, 0.0, kind, _price(block.get(key)))

        block = (odds.get("goal_line") or {}).get(source_kind)
        if isinstance(block, dict) and block.get("line") == 2.5:
            add("OU25", "OVER", 2.5, kind, _price(block.get("over")))
            add("OU25", "UNDER", 2.5, kind, _price(block.get("under")))

        block = (odds.get("asian_handicap") or {}).get(source_kind)
        if isinstance(block, dict) and isinstance(block.get("line"), (int, float)):
            add("AH", "HOME", float(block["line"]), kind, _price(block.get("home")))
            add("AH", "AWAY", float(block["line"]), kind, _price(block.get("away")))
    return out


def parse_five_dollar_fixture(row: dict[str, Any]) -> ProviderEvent | None:
    teams = row.get("teams") or {}
    home = (teams.get("home") or {}).get("name")
    away = (teams.get("away") or {}).get("name")
    if not home or not away:
        return None
    if row.get("kickoff_utc"):
        kickoff = _iso(row["kickoff_utc"])
    elif row.get("kickoff_ts"):
        kickoff = datetime.fromtimestamp(int(row["kickoff_ts"]), tz=timezone.utc)
    else:
        return None
    odds = parse_five_dollar_odds(row.get("odds") or {}, "bet365")
    return ProviderEvent(FIVE_DOLLAR, str(row.get("id", "")), kickoff, home, away, odds)


class FiveDollarClient:
    def __init__(self, client: httpx.Client, api_key: str, base_url: str = "https://api.5dollarfootballapi.com/v1",
                 min_request_interval: float = 3.1, max_odds_requests: int = 30):
        self.client, self.base_url = client, base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.min_request_interval = max(0.0, min_request_interval)
        self.max_odds_requests = max(0, max_odds_requests)
        self.odds_requests = 0
        self._last_request_at = 0.0

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        response = self.client.get(f"{self.base_url}{path}", params=params, headers=self.headers)
        self._last_request_at = time.monotonic()
        if response.status_code == 429:
            time.sleep(min(int(response.headers.get("Retry-After", "5")), 30))
            response = self.client.get(f"{self.base_url}{path}", params=params, headers=self.headers)
        return response

    def list_leagues(self) -> list[dict[str, Any]]:
        response = self._get("/leagues", {"popular": 1})
        if response.status_code != 200:
            raise ProviderError(f"5Dollar /leagues devolveu {response.status_code}: {response.text[:200]}")
        return response.json().get("data", [])

    def fetch_window(self, league_id: int, start_ts: int, end_ts: int) -> tuple[list[ProviderEvent], dict[str, Any]]:
        events: list[ProviderEvent] = []
        meta: dict[str, Any] = {}
        page = 1
        while True:
            response = self._get("/fixtures", {"league": league_id, "start_time": start_ts, "end_time": end_ts,
                                               "per_page": 100, "page": page})
            try:
                meta["rate_remaining"] = int(response.headers["X-RateLimit-Remaining"])
            except (KeyError, ValueError):
                pass
            if response.status_code == 403:  # fora do plano (ex.: mais de 3 meses atrás no plano grátis)
                meta["forbidden"] = True
                return events, meta
            if response.status_code == 401:
                raise ProviderError("5Dollar: chave inválida ou em falta (401)")
            if response.status_code == 429:
                raise ProviderError("5Dollar: limite de pedidos excedido (429)")
            response.raise_for_status()
            payload = response.json()
            for row in payload.get("data", []):
                event = parse_five_dollar_fixture(row)
                if event is None:
                    continue
                # No plano Free, odds não vêm nas listas: é necessário consultar
                # /fixtures/{id}/odds individualmente (include=odds é Pro+).
                if not event.odds and event.external_id:
                    if self.odds_requests >= self.max_odds_requests:
                        meta["odds_budget_exhausted"] = True
                    else:
                        odds_response = self._get(f"/fixtures/{event.external_id}/odds", {})
                        self.odds_requests += 1
                        try:
                            meta["rate_remaining"] = int(odds_response.headers["X-RateLimit-Remaining"])
                        except (KeyError, ValueError):
                            pass
                        if odds_response.status_code == 401:
                            raise ProviderError("5Dollar: chave inválida ou em falta (401)")
                        if odds_response.status_code == 429:
                            raise ProviderError("5Dollar: limite de pedidos excedido ao consultar odds (429)")
                        if odds_response.status_code == 403:
                            meta["odds_forbidden"] = meta.get("odds_forbidden", 0) + 1
                        else:
                            odds_response.raise_for_status()
                            odds_payload = odds_response.json().get("data") or {}
                            event.odds = parse_five_dollar_odds(odds_payload, "bet365")
                events.append(event)
            if not (payload.get("pagination") or {}).get("has_more"):
                break
            page += 1
        return events, meta
