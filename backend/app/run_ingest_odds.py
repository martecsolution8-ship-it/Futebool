"""Recolhe odds actuais (The Odds API e 5DollarFootballAPI) e junta-as aos jogos existentes.

Chaves por variável de ambiente: THE_ODDS_API_KEY, FIVE_DOLLAR_API_KEY.
Exemplos:
  python -m app.run_ingest_odds
  python -m app.run_ingest_odds --providers 5dollar --days-back 2 --days-ahead 3
  python -m app.run_ingest_odds --list-5dollar-leagues
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.db.session import SessionLocal
from app.services.odds_ingest import OddsIngestService, last_requests_remaining, record_run
from app.services.odds_providers import FIVE_DOLLAR, THE_ODDS_API, FiveDollarClient, ProviderError, TheOddsApiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("marbet.ingest_odds")

DEFAULT_SPORTS = "soccer_epl,soccer_spain_la_liga,soccer_germany_bundesliga,soccer_italy_serie_a,soccer_france_ligue_one"
DEFAULT_FIVE_DOLLAR_LEAGUES = "39,140,135,78,61"  # EPL, La Liga, Serie A, Bundesliga, Ligue 1 (confirmar com --list-5dollar-leagues)


def _log_result(result) -> None:
    logger.info("%s: eventos=%s ligados=%s odds_alteradas=%s sem_jogo=%s",
                result.provider, result.events, result.matched, result.odds_changed, len(result.unmatched_events))
    if result.unmatched_teams:
        logger.info("%s: equipas sem correspondência (acrescentar a MANUAL_ALIASES): %s",
                    result.provider, ", ".join(result.unmatched_teams))
    if result.linked_teams:
        logger.info("%s: ligações aproximadas (rever): %s", result.provider,
                    "; ".join(f"{raw} -> {team}" for raw, team in result.linked_teams))


def run_the_odds_api(db, client: httpx.Client, args) -> None:
    key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not key:
        logger.info("THE_ODDS_API_KEY não definida; The Odds API ignorada.")
        return
    reserve = args.odds_api_reserve
    remaining = last_requests_remaining(db, THE_ODDS_API)
    if remaining is not None and remaining <= reserve:
        logger.warning("The Odds API: restam %s créditos (reserva %s); a saltar esta execução.", remaining, reserve)
        return

    api = TheOddsApiClient(client, key, markets=os.getenv("THE_ODDS_API_MARKETS", "h2h"),
                           regions=os.getenv("THE_ODDS_API_REGIONS", "eu"))
    service = OddsIngestService(db, THE_ODDS_API)
    events = []
    meta: dict = {}
    for sport in [s.strip() for s in args.sports.split(",") if s.strip()]:
        fetched, meta_now = api.fetch_sport(sport)
        meta.update(meta_now)
        events.extend(fetched)
        remaining = meta.get("requests_remaining")
        logger.info("The Odds API %s: %s eventos (créditos restantes: %s)", sport, len(fetched), remaining)
        if remaining is not None and remaining <= reserve:
            logger.warning("The Odds API: reserva de créditos atingida; a parar.")
            break
    result = service.ingest(events)
    record_run(db, THE_ODDS_API, "odds", result, meta)
    _log_result(result)


def run_five_dollar(db, client: httpx.Client, args) -> None:
    key = os.getenv("FIVE_DOLLAR_API_KEY", "").strip()
    if not key:
        logger.info("FIVE_DOLLAR_API_KEY não definida; 5DollarFootballAPI ignorada.")
        return
    api = FiveDollarClient(client, key)
    league_ids = [int(x) for x in os.getenv("FIVE_DOLLAR_LEAGUE_IDS", args.league_ids).split(",") if x.strip()]
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    service = OddsIngestService(db, FIVE_DOLLAR)
    events = []
    meta: dict = {}
    stop = False
    for offset in range(-args.days_back, args.days_ahead + 1):
        start = today + timedelta(days=offset)
        for league_id in league_ids:
            fetched, meta_now = api.fetch_window(league_id, int(start.timestamp()), int((start + timedelta(days=1)).timestamp()))
            meta.update(meta_now)
            events.extend(fetched)
            time.sleep(1.2)
            if meta.get("rate_remaining") is not None and meta["rate_remaining"] <= 2:
                logger.warning("5Dollar: janela de pedidos quase esgotada; a parar.")
                stop = True
                break
        if stop:
            break
    result = service.ingest(events)
    record_run(db, FIVE_DOLLAR, "odds", result, meta)
    _log_result(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--providers", default="the-odds-api,5dollar")
    parser.add_argument("--sports", default=os.getenv("THE_ODDS_API_SPORTS", DEFAULT_SPORTS))
    parser.add_argument("--league-ids", default=DEFAULT_FIVE_DOLLAR_LEAGUES)
    parser.add_argument("--days-back", type=int, default=1)
    parser.add_argument("--days-ahead", type=int, default=3)
    parser.add_argument("--odds-api-reserve", type=int, default=25, help="créditos a não gastar na The Odds API")
    parser.add_argument("--list-5dollar-leagues", action="store_true")
    args = parser.parse_args(argv)

    providers = {p.strip().lower() for p in args.providers.split(",")}
    failures = 0
    db = SessionLocal()
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "MarBet-odds/1.0"}) as client:
            if args.list_5dollar_leagues:
                key = os.getenv("FIVE_DOLLAR_API_KEY", "").strip()
                for league in FiveDollarClient(client, key).list_leagues():
                    print(league.get("id"), league.get("name"), league.get("country", ""))
                return 0
            if "the-odds-api" in providers:
                try:
                    run_the_odds_api(db, client, args)
                except (ProviderError, httpx.HTTPError):
                    logger.exception("The Odds API falhou")
                    db.rollback()
                    failures += 1
            if "5dollar" in providers:
                try:
                    run_five_dollar(db, client, args)
                except (ProviderError, httpx.HTTPError):
                    logger.exception("5DollarFootballAPI falhou")
                    db.rollback()
                    failures += 1
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
