"""Importa CSV do football-data.co.uk (jogos + odds) uma vez e termina.

Exemplos:
  python -m app.run_import_footballdata                          # época actual, ligas principais
  python -m app.run_import_footballdata --seasons last:10 --leagues all
  python -m app.run_import_footballdata --seasons 2425,2526 --leagues E0,SP1 --force
  python -m app.run_import_footballdata --local-dir ./csv        # <dir>/<época>/<Div>.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from app.db.session import SessionLocal
from app.services.football_data_uk_importer import FootballDataUkImporter
from app.services.football_data_uk_parser import (
    LEAGUES,
    TOP_LEAGUES,
    League,
    csv_url,
    current_season_start_year,
    season_code_from_year,
    season_label_from_code,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("marbet.import_footballdata")


def resolve_seasons(spec: str, today: date | None = None) -> list[str]:
    today = today or date.today()
    current = current_season_start_year(today)
    spec = spec.strip().lower()
    if spec in ("", "current"):
        return [season_code_from_year(current)]
    if spec.startswith("last:"):
        count = max(1, int(spec.split(":", 1)[1]))
        return [season_code_from_year(current - i) for i in range(count - 1, -1, -1)]
    return [code.strip() for code in spec.split(",") if code.strip()]


def resolve_leagues(spec: str) -> list[League]:
    spec = spec.strip().lower()
    if spec in ("", "top"):
        divs = list(TOP_LEAGUES)
    elif spec == "all":
        divs = list(LEAGUES)
    else:
        divs = [item.strip().upper() for item in spec.split(",") if item.strip()]
    unknown = [d for d in divs if d not in LEAGUES]
    if unknown:
        raise SystemExit(f"Ligas desconhecidas: {', '.join(unknown)}. Disponíveis: {', '.join(LEAGUES)}")
    return [LEAGUES[d] for d in divs]


def fetch(client: httpx.Client, url: str, attempts: int = 3) -> bytes | None:
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("Falha de rede em %s (tentativa %s/%s): %s", url, attempt, attempts, exc)
            time.sleep(2 * attempt)
            continue
        if response.status_code == 404:
            logger.info("Sem ficheiro em %s (404); ignorado.", url)
            return None
        if response.status_code >= 500:
            time.sleep(2 * attempt)
            continue
        response.raise_for_status()
        return response.content
    raise RuntimeError(f"Não foi possível descarregar {url}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", default="current", help="current | last:N | 2425,2526")
    parser.add_argument("--leagues", default="top", help="top | all | E0,SP1,...")
    parser.add_argument("--force", action="store_true", help="reimporta mesmo que o ficheiro não tenha mudado")
    parser.add_argument("--local-dir", default=None, help="lê CSV locais em vez de descarregar")
    parser.add_argument("--sleep", type=float, default=1.0, help="pausa entre descarregamentos (s)")
    args = parser.parse_args(argv)

    seasons = resolve_seasons(args.seasons)
    leagues = resolve_leagues(args.leagues)
    current_code = season_code_from_year(current_season_start_year(date.today()))
    failures = 0
    all_new_teams: list[str] = []

    db = SessionLocal()
    try:
        importer = FootballDataUkImporter(db)
        with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "MarBet-importer/1.0"}) as client:
            for code in seasons:
                for league in leagues:
                    if args.local_dir:
                        path = Path(args.local_dir) / code / f"{league.div}.csv"
                        content = path.read_bytes() if path.exists() else None
                    else:
                        content = fetch(client, csv_url(league.div, code))
                        time.sleep(args.sleep)
                    if content is None:
                        continue
                    result = importer.import_file(
                        league, season_label_from_code(code), content,
                        force=args.force, active=(code == current_code),
                    )
                    all_new_teams.extend(result.new_teams)
                    logger.info(
                        "%s %s: %s | linhas=%s criados=%s actualizados=%s anexados=%s odds=%s%s",
                        league.div, code, result.status, result.rows, result.fixtures_created,
                        result.fixtures_updated, result.fixtures_attached, result.odds_written,
                        f" | erro={result.error}" if result.error else "",
                    )
                    if result.status == "FAILED":
                        failures += 1
        if all_new_teams:
            logger.info("Equipas novas criadas (rever aliases): %s", ", ".join(sorted(set(all_new_teams))))
        return 1 if failures else 0
    except Exception:
        logger.exception("Falhou")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
