"""Liga eventos de fornecedores de odds a jogos já existentes e grava as odds."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.sports import Fixture, SyncRun, SyncStatus
from app.services.odds_providers import ProviderEvent
from app.services.odds_store import upsert_odds
from app.services.team_resolver import TeamResolver

MATCH_TOLERANCE = timedelta(hours=36)


@dataclass
class IngestResult:
    provider: str
    events: int = 0
    matched: int = 0
    odds_changed: int = 0
    unmatched_events: list[str] = field(default_factory=list)
    unmatched_teams: list[str] = field(default_factory=list)
    linked_teams: list[tuple[str, str]] = field(default_factory=list)


class OddsIngestService:
    """Nunca cria jogos nem equipas: só junta odds a jogos que os outros fornecedores já sincronizaram."""

    def __init__(self, db: Session, provider: str):
        self.db, self.provider = db, provider
        self.resolver = TeamResolver(db, provider, create_missing=False)

    def ingest(self, events: list[ProviderEvent]) -> IngestResult:
        result = IngestResult(provider=self.provider, events=len(events))
        entries = []
        for event in events:
            home, away = self.resolver.resolve(event.home), self.resolver.resolve(event.away)
            if home is None or away is None:
                result.unmatched_events.append(f"{event.home} - {event.away}")
                continue
            fixture = self.db.scalar(
                select(Fixture)
                .where(Fixture.home_team_id == home.id, Fixture.away_team_id == away.id,
                       Fixture.kickoff_at >= event.kickoff - MATCH_TOLERANCE,
                       Fixture.kickoff_at <= event.kickoff + MATCH_TOLERANCE)
                .order_by(Fixture.kickoff_at)
                .limit(1)
            )
            if fixture is None:
                result.unmatched_events.append(f"{event.home} - {event.away} ({event.kickoff:%Y-%m-%d})")
                continue
            result.matched += 1
            if event.odds:
                entries.append((fixture.id, event.odds))

        result.odds_changed = upsert_odds(self.db, self.provider, entries, touch=True)
        self.db.commit()
        result.unmatched_teams = sorted(self.resolver.unmatched)
        result.linked_teams = list(self.resolver.linked)
        return result


def record_run(db: Session, provider: str, operation: str, result: IngestResult, meta: dict) -> SyncRun:
    run = SyncRun(
        provider=provider, operation=operation, status=SyncStatus.SUCCESS,
        records_received=result.events, records_written=result.odds_changed,
        completed_at=datetime.now(timezone.utc),
        metadata_json={**meta, "matched": result.matched, "unmatched_events": result.unmatched_events[:50],
                       "unmatched_teams": result.unmatched_teams[:50]},
    )
    db.add(run)
    db.commit()
    return run


def last_requests_remaining(db: Session, provider: str) -> int | None:
    runs = db.scalars(
        select(SyncRun).where(SyncRun.provider == provider, SyncRun.status == SyncStatus.SUCCESS)
        .order_by(SyncRun.started_at.desc()).limit(5)
    ).all()
    for run in runs:
        remaining = (run.metadata_json or {}).get("requests_remaining")
        if isinstance(remaining, int):
            return remaining
    return None
