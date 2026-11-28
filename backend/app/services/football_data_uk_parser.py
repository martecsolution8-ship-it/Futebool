"""Leitura dos CSV do football-data.co.uk (módulo puro, só biblioteca padrão)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo

    _LONDON = ZoneInfo("Europe/London")
except Exception:  # sem base de fusos horários (ex.: Windows sem tzdata)
    _LONDON = None

SOURCE = "football-data-uk"
BASE_URL = "https://www.football-data.co.uk"


@dataclass(frozen=True)
class League:
    div: str
    name: str
    country: str
    code: str  # alinhado com os códigos do football-data.org quando existem


_LEAGUE_LIST = [
    League("E0", "Premier League", "England", "PL"),
    League("E1", "Championship", "England", "ELC"),
    League("E2", "League One", "England", "E2"),
    League("E3", "League Two", "England", "E3"),
    League("EC", "National League", "England", "EC"),
    League("SC0", "Scottish Premiership", "Scotland", "SC0"),
    League("SC1", "Scottish Championship", "Scotland", "SC1"),
    League("D1", "Bundesliga", "Germany", "BL1"),
    League("D2", "2. Bundesliga", "Germany", "D2"),
    League("I1", "Serie A", "Italy", "SA"),
    League("I2", "Serie B", "Italy", "I2"),
    League("SP1", "La Liga", "Spain", "PD"),
    League("SP2", "Segunda División", "Spain", "SP2"),
    League("F1", "Ligue 1", "France", "FL1"),
    League("F2", "Ligue 2", "France", "F2"),
    League("N1", "Eredivisie", "Netherlands", "DED"),
    League("B1", "Jupiler Pro League", "Belgium", "B1"),
    League("P1", "Primeira Liga", "Portugal", "PPL"),
    League("T1", "Süper Lig", "Turkey", "T1"),
    League("G1", "Super League", "Greece", "G1"),
]
LEAGUES: dict[str, League] = {league.div: league for league in _LEAGUE_LIST}
TOP_LEAGUES = ("E0", "E1", "D1", "I1", "SP1", "F1", "N1", "P1")


# ---------------------------------------------------------------- épocas e URLs

def season_code_from_year(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def season_label_from_code(code: str) -> str:
    """'2526' -> '2025' (o rótulo usado pelos outros fornecedores é o ano de início)."""
    start = int(code[:2])
    return str((2000 if start < 90 else 1900) + start)


def current_season_start_year(today: date) -> int:
    return today.year if today.month >= 7 else today.year - 1


def csv_url(div: str, season_code: str) -> str:
    return f"{BASE_URL}/mmz4281/{season_code}/{div}.csv"


# ---------------------------------------------------------------- mapeamento de colunas

STAT_COLUMNS = {
    "HS": "home_shots", "AS": "away_shots", "HST": "home_shots_on_target", "AST": "away_shots_on_target",
    "HF": "home_fouls", "AF": "away_fouls", "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellow", "AY": "away_yellow", "HR": "home_red", "AR": "away_red",
}

_H2H_BOOKS = {
    "B365": "bet365", "BW": "bwin", "IW": "interwetten", "PS": "pinnacle", "WH": "williamhill",
    "VC": "vcbet", "BF": "betfair", "BFE": "betfair_exchange", "1XB": "1xbet",
    "Max": "market_max", "Avg": "market_avg",
}
_OU_BOOKS = {"B365": "bet365", "P": "pinnacle", "Max": "market_max", "Avg": "market_avg"}
_AH_BOOKS = {"B365": "bet365", "P": "pinnacle", "Max": "market_max", "Avg": "market_avg"}


def _build_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for prefix, book in _H2H_BOOKS.items():
        for kind, infix in (("pre_match", ""), ("closing", "C")):
            for sel in ("H", "D", "A"):
                specs.append({"col": f"{prefix}{infix}{sel}", "bookmaker": book, "market": "1X2",
                              "selection": sel, "kind": kind, "line": 0.0, "line_col": None})
    for prefix, book in _OU_BOOKS.items():
        for kind, infix in (("pre_match", ""), ("closing", "C")):
            specs.append({"col": f"{prefix}{infix}>2.5", "bookmaker": book, "market": "OU25",
                          "selection": "OVER", "kind": kind, "line": 2.5, "line_col": None})
            specs.append({"col": f"{prefix}{infix}<2.5", "bookmaker": book, "market": "OU25",
                          "selection": "UNDER", "kind": kind, "line": 2.5, "line_col": None})
    for prefix, book in _AH_BOOKS.items():
        for kind, infix, line_col in (("pre_match", "", "AHh"), ("closing", "C", "AHCh")):
            specs.append({"col": f"{prefix}{infix}AHH", "bookmaker": book, "market": "AH",
                          "selection": "HOME", "kind": kind, "line": None, "line_col": line_col})
            specs.append({"col": f"{prefix}{infix}AHA", "bookmaker": book, "market": "AH",
                          "selection": "AWAY", "kind": kind, "line": None, "line_col": line_col})
    return specs


ODDS_SPECS = _build_specs()


# ---------------------------------------------------------------- leitura

def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _integer(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _parse_date(value: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _kickoff(day: date, clock: str) -> datetime:
    try:
        parsed = datetime.strptime(clock, "%H:%M").time()
    except ValueError:
        return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)
    naive = datetime.combine(day, parsed)
    if _LONDON is None:
        return naive.replace(tzinfo=timezone.utc)
    return naive.replace(tzinfo=_LONDON).astimezone(timezone.utc)


def parse_csv(content: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(decode_csv(content)))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        row = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
        day = _parse_date(row.get("Date", ""))
        home, away = row.get("HomeTeam", ""), row.get("AwayTeam", "")
        if day is None or not home or not away:
            continue

        home_score, away_score = _integer(row.get("FTHG")), _integer(row.get("FTAG"))
        finished = home_score is not None and away_score is not None

        statistics: dict[str, Any] = {}
        for column, key in STAT_COLUMNS.items():
            value = _integer(row.get(column))
            if value is not None:
                statistics[key] = value
        if row.get("Referee"):
            statistics["referee"] = row["Referee"]

        odds: list[dict[str, Any]] = []
        for spec in ODDS_SPECS:
            price = _number(row.get(spec["col"]))
            if price is None or price <= 1.0:
                continue
            line = spec["line"]
            if spec["line_col"]:
                line = _number(row.get(spec["line_col"]))
                if line is None:
                    continue
            odds.append({
                "bookmaker": spec["bookmaker"], "market": spec["market"], "selection": spec["selection"],
                "kind": spec["kind"], "line": round(float(line), 2), "price": price,
            })

        rows.append({
            "date": day,
            "kickoff": _kickoff(day, row.get("Time", "")),
            "home": home,
            "away": away,
            "home_score": home_score if finished else None,
            "away_score": away_score if finished else None,
            "home_score_halftime": _integer(row.get("HTHG")) if finished else None,
            "away_score_halftime": _integer(row.get("HTAG")) if finished else None,
            "status": "FINISHED" if finished else "SCHEDULED",
            "statistics": statistics,
            "odds": odds,
        })
    return rows
