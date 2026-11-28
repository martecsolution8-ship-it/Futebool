from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from app.intelligence.odds_quote import pick_quote
from app.services.odds_providers import FiveDollarClient, parse_five_dollar_fixture, parse_five_dollar_odds, parse_the_odds_api_event

THE_ODDS_EVENT = {
    "id": "abc", "commence_time": "2026-11-28T15:00:00Z", "home_team": "Arsenal", "away_team": "Chelsea",
    "bookmakers": [
        {"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.1}, {"name": "Chelsea", "price": 3.5}, {"name": "Draw", "price": 3.4}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5}, {"name": "Under", "price": 1.95, "point": 2.5},
                                           {"name": "Over", "price": 1.5, "point": 1.5}]}]},
        {"key": "williamhill", "markets": [
            {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.0}, {"name": "Chelsea", "price": 3.6}, {"name": "Draw", "price": 3.3}]}]},
        {"key": "unibet", "markets": [
            {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.2}, {"name": "Chelsea", "price": 3.4}, {"name": "Draw", "price": 3.5}]}]},
    ],
}


def test_the_odds_api_event_is_normalised():
    event = parse_the_odds_api_event(THE_ODDS_EVENT)
    assert (event.home, event.away) == ("Arsenal", "Chelsea")
    assert event.kickoff == datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)
    prices = {(o["bookmaker"], o["market"], o["selection"], o["line"]): o["price"] for o in event.odds}
    assert prices[("pinnacle", "1X2", "H", 0.0)] == 2.1
    assert prices[("pinnacle", "1X2", "D", 0.0)] == 3.4
    assert prices[("pinnacle", "OU25", "OVER", 2.5)] == 1.9
    assert not any(o["line"] == 1.5 for o in event.odds)          # só a linha 2.5 interessa
    assert prices[("market_max", "1X2", "H", 0.0)] == 2.2          # 3 casas -> média/máximo sintéticos
    assert prices[("market_avg", "1X2", "H", 0.0)] == round((2.1 + 2.0 + 2.2) / 3, 3)
    assert ("market_avg", "OU25", "OVER", 2.5) not in prices       # só 1 casa -> sem agregado


FIVE_DOLLAR_ROW = {
    "id": 1, "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
    "kickoff_utc": "2026-11-28T15:00:00+00:00", "status": "scheduled",
    "odds": {
        "1x2": {"opening": {"home": 2.1, "draw": 3.4, "away": 3.5}, "closing": {"home": 2.0, "draw": 3.5, "away": 3.7}, "inplay": None},
        "asian_handicap": {"opening": -0.25, "closing": -0.5, "inplay": None},
        "goal_line": {"opening": {"line": 2.5, "over": 1.9, "under": 1.9}, "closing": 2.75},
    },
}


def test_five_dollar_fixture_is_normalised():
    event = parse_five_dollar_fixture(FIVE_DOLLAR_ROW)
    assert (event.home, event.away) == ("Arsenal", "Chelsea")
    prices = {(o["market"], o["selection"], o["kind"]): o["price"] for o in event.odds}
    assert prices[("1X2", "H", "opening")] == 2.1 and prices[("1X2", "A", "closing")] == 3.7
    assert prices[("OU25", "OVER", "opening")] == 1.9
    assert all(o["bookmaker"] == "bet365" for o in event.odds)
    assert not any(o["market"] == "AH" for o in event.odds)        # linhas sem preços são ignoradas


def _row(bookmaker, selection, price, kind="pre_match", market="1X2", line=0.0, age_hours=1, source="x"):
    return SimpleNamespace(bookmaker=bookmaker, market=market, selection=selection, line=line, kind=kind,
                           price=price, source=source,
                           updated_at=datetime.now(timezone.utc) - timedelta(hours=age_hours))


def test_pick_quote_prefers_bookmaker_then_kind_and_ignores_stale():
    rows = [
        _row("pinnacle", "H", 2.1),
        _row("bet365", "H", 2.0, kind="opening"),
        _row("bet365", "H", 1.95, kind="closing"),
        _row("bet365", "D", 3.3, age_hours=100),   # demasiado antiga
    ]
    quote = pick_quote(rows, "1X2", "HOME")
    assert quote["bookmaker"] == "bet365" and quote["price"] == 1.95 and quote["kind"] == "closing"
    assert pick_quote(rows, "1X2", "DRAW") is None                      # só existe antiga
    assert pick_quote(rows, "BTTS", "YES") is None                      # mercado sem correspondência
    assert pick_quote([_row("pinnacle", "OVER", 1.9, market="OU25", line=2.5)], "TOTAL_GOALS", "OVER_2.5")["price"] == 1.9
    assert pick_quote([_row("pinnacle", "OVER", 1.9, market="OU25", line=3.5)], "TOTAL_GOALS", "OVER_2.5") is None


def test_five_dollar_current_odds_endpoint_envelope_is_normalised():
    payload = {
        "fixture_id": 123,
        "bookmakers": [{"name": "Bet 365", "slug": "bet365", "odds": {
            "1x2": {"opening": {"home": 2.1, "draw": 3.4, "away": 3.6},
                    "closing": {"home": 1.95, "draw": 3.5, "away": 3.8}},
            "goal_line": {"opening": {"line": 2.5, "over": 1.91, "under": 1.91},
                          "closing": {"line": 2.5, "over": 1.87, "under": 1.95}},
        }}],
    }
    prices = parse_five_dollar_odds(payload, "bet365")
    by_key = {(o["bookmaker"], o["market"], o["selection"], o["kind"]): o["price"] for o in prices}
    assert by_key[("bet365", "1X2", "H", "opening")] == 2.1
    assert by_key[("bet365", "1X2", "A", "closing")] == 3.8
    assert by_key[("bet365", "OU25", "OVER", "closing")] == 1.87


def test_five_dollar_free_plan_fetches_odds_per_fixture():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/fixtures"):
            return httpx.Response(200, json={"data": [{
                "id": 456,
                "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
                "kickoff_utc": "2026-11-28T15:00:00+00:00",
            }], "pagination": {"has_more": False}})
        if request.url.path.endswith("/fixtures/456/odds"):
            return httpx.Response(200, json={"data": {"bookmakers": [{"slug": "bet365", "odds": {
                "1x2": {"opening": {"home": 2.0, "draw": 3.4, "away": 3.8}}
            }}]}})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = FiveDollarClient(http, "test-key", min_request_interval=0)
        events, meta = api.fetch_window(39, 1795824000, 1795910400)

    assert calls == ["/v1/fixtures", "/v1/fixtures/456/odds"]
    assert len(events) == 1
    assert any(o["market"] == "1X2" and o["selection"] == "H" and o["price"] == 2.0 for o in events[0].odds)
