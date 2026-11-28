"""Importa CSV do football-data.co.uk para competições, épocas, equipas, jogos e odds."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.sports import Competition, Fixture, FixtureStatus, Season, SyncRun, SyncStatus, Team
from app.services.football_data_uk_parser import SOURCE, League, parse_csv
from app.services.odds_store import upsert_odds
from app.services.team_resolver import TeamResolver

MATCH_TOLERANCE = timedelta(hours=36)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class ImportResult:
    div: str
    season: str
    status: str = "SUCCESS"  # SUCCESS | SKIPPED | FAILED
    rows: int = 0
    fixtures_created: int = 0
    fixtures_updated: int = 0
    fixtures_attached: int = 0  # jogos de outros fornecedores a que só se juntaram odds
    odds_written: int = 0
    new_teams: list[str] = field(default_factory=list)
    error: str | None = None


class FootballDataUkImporter:
    def __init__(self, db: Session):
        self.db = db
        self.teams = TeamResolver(db, SOURCE, create_missing=True)

    # ------------------------------------------------------------ equipas

    def resolve_team(self, raw_name: str, country: str | None) -> Team:
        team = self.teams.resolve(raw_name, country)
        assert team is not None
        return team

    # ------------------------------------------------------------ competição e época

    def get_competition(self, league: League) -> Competition:
        competition = self.db.scalars(
            select(Competition).where(Competition.external_ids[SOURCE].as_string() == league.div)
        ).first()
        if competition is None:
            for candidate in self.db.scalars(select(Competition)).all():
                same_code = bool(candidate.code) and candidate.code.upper() == league.code.upper()
                same_name = candidate.name.strip().casefold() == league.name.strip().casefold()
                same_country = not candidate.country or candidate.country == league.country
                if (same_code or same_name) and same_country:
                    competition = candidate
                    break
        if competition is None:
            competition = Competition(
                name=league.name, country=league.country, code=league.code, type="league",
                external_ids={SOURCE: league.div},
            )
            self.db.add(competition)
        else:
            competition.external_ids = {**(competition.external_ids or {}), SOURCE: league.div}
        self.db.flush()
        return competition

    def get_season(self, competition: Competition, label: str, active: bool) -> Season:
        season = self.db.scalar(
            select(Season).where(Season.competition_id == competition.id, Season.label == label)
        )
        if season is None:
            season = Season(competition_id=competition.id, label=label, active=active, external_ids={})
            self.db.add(season)
            self.db.flush()
        return season

    # ------------------------------------------------------------ importação

    def import_file(
        self, league: League, season_label: str, content: bytes, *, force: bool = False, active: bool = False
    ) -> ImportResult:
        result = ImportResult(div=league.div, season=season_label)
        operation = f"csv:{league.div}:{season_label}"
        file_hash = hashlib.sha256(content).hexdigest()

        if not force:
            last = self.db.scalar(
                select(SyncRun)
                .where(SyncRun.provider == SOURCE, SyncRun.operation == operation, SyncRun.status == SyncStatus.SUCCESS)
                .order_by(SyncRun.started_at.desc())
                .limit(1)
            )
            if last is not None and (last.metadata_json or {}).get("file_hash") == file_hash:
                result.status = "SKIPPED"
                return result

        run = SyncRun(provider=SOURCE, operation=operation, status=SyncStatus.RUNNING,
                      metadata_json={"file_hash": file_hash, "season": season_label, "div": league.div})
        self.db.add(run)
        self.db.commit()

        try:
            self.teams.created = []
            self._import_rows(league, season_label, content, active, result)
            result.new_teams = list(self.teams.created)
            run.records_received = result.rows
            run.records_written = result.fixtures_created + result.fixtures_updated + result.odds_written
            run.status = SyncStatus.SUCCESS
            run.completed_at = datetime.now(timezone.utc)
            run.metadata_json = {**run.metadata_json, "new_teams": result.new_teams,
                                 "fixtures_attached": result.fixtures_attached}
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            self.teams.reset()  # o índice pode referir equipas revertidas
            result.status = "FAILED"
            result.error = f"{type(exc).__name__}: {exc}"[:2000]
            run.status = SyncStatus.FAILED
            run.error_count = 1
            run.error_message = result.error
            run.completed_at = datetime.now(timezone.utc)
            self.db.add(run)
            self.db.commit()
        return result

    def _import_rows(self, league: League, season_label: str, content: bytes, active: bool, result: ImportResult) -> None:
        rows = parse_csv(content)
        result.rows = len(rows)
        if not rows:
            return

        competition = self.get_competition(league)
        season = self.get_season(competition, season_label, active)

        for row in rows:
            row["home_team"] = self.resolve_team(row["home"], league.country)
            row["away_team"] = self.resolve_team(row["away"], league.country)

        # Jogos já existentes (de qualquer fornecedor) para não duplicar.
        team_ids = {row["home_team"].id for row in rows}
        start = min(row["kickoff"] for row in rows) - MATCH_TOLERANCE
        end = max(row["kickoff"] for row in rows) + MATCH_TOLERANCE
        existing = self.db.scalars(
            select(Fixture).where(Fixture.kickoff_at >= start, Fixture.kickoff_at <= end,
                                  Fixture.home_team_id.in_(list(team_ids)))
        ).all()
        by_provider_id = {f.provider_fixture_id: f for f in existing if f.provider == SOURCE}
        by_pair: dict[tuple[uuid.UUID, uuid.UUID], list[Fixture]] = {}
        for fixture in existing:
            by_pair.setdefault((fixture.home_team_id, fixture.away_team_id), []).append(fixture)

        touched: list[tuple[Fixture, dict]] = []
        for row in rows:
            home, away = row["home_team"], row["away_team"]
            if home.id == away.id:
                continue
            pair_key = hashlib.sha1(f"{home.id}|{away.id}".encode()).hexdigest()[:12]
            provider_id = f"{league.div}:{season_label}:{row['date'].isoformat()}:{pair_key}"

            fixture = by_provider_id.get(provider_id)
            if fixture is None:
                candidates = [
                    f for f in by_pair.get((home.id, away.id), [])
                    if abs(_aware(f.kickoff_at) - row["kickoff"]) <= MATCH_TOLERANCE
                ]
                if candidates:
                    fixture = min(candidates, key=lambda f: abs(_aware(f.kickoff_at) - row["kickoff"]))

            raw_hash = hashlib.sha256(json.dumps(
                {k: row[k] for k in ("kickoff", "status", "home_score", "away_score",
                                     "home_score_halftime", "away_score_halftime", "statistics")},
                sort_keys=True, default=str).encode()).hexdigest()

            if fixture is None:
                fixture = Fixture(
                    competition_id=competition.id, season_id=season.id,
                    home_team_id=home.id, away_team_id=away.id,
                    provider=SOURCE, provider_fixture_id=provider_id, round=None,
                    kickoff_at=row["kickoff"], status=FixtureStatus(row["status"]),
                    home_score=row["home_score"], away_score=row["away_score"],
                    home_score_halftime=row["home_score_halftime"], away_score_halftime=row["away_score_halftime"],
                    statistics=row["statistics"], raw_hash=raw_hash,
                )
                self.db.add(fixture)
                by_provider_id[provider_id] = fixture
                by_pair.setdefault((home.id, away.id), []).append(fixture)
                result.fixtures_created += 1
            elif fixture.provider == SOURCE:
                if fixture.raw_hash != raw_hash:
                    fixture.kickoff_at = row["kickoff"]
                    fixture.status = FixtureStatus(row["status"])
                    fixture.home_score = row["home_score"]
                    fixture.away_score = row["away_score"]
                    fixture.home_score_halftime = row["home_score_halftime"]
                    fixture.away_score_halftime = row["away_score_halftime"]
                    fixture.statistics = row["statistics"]
                    fixture.raw_hash = raw_hash
                    result.fixtures_updated += 1
            else:
                # Jogo de outro fornecedor: não se mexe nos resultados, só se juntam as odds.
                result.fixtures_attached += 1
            touched.append((fixture, row))

        self.db.flush()
        result.odds_written = self._upsert_odds(touched)

    def _upsert_odds(self, touched: list[tuple[Fixture, dict]]) -> int:
        return upsert_odds(self.db, SOURCE, [(fixture.id, row["odds"]) for fixture, row in touched], touch=False)
