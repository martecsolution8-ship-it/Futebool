"""Resolve nomes de equipas de um fornecedor para `Team`, com aliases persistentes."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.odds import TeamAlias
from app.models.sports import Team
from app.services.team_names import best_match, canonical_key, normalize_team_name


class TeamResolver:
    """create_missing=True (importador de histórico) cria equipas novas;
    create_missing=False (conectores de odds) devolve None quando não encontra."""

    def __init__(self, db: Session, source: str, create_missing: bool = False):
        self.db = db
        self.source = source
        self.create_missing = create_missing
        self.created: list[str] = []
        self.linked: list[tuple[str, str]] = []  # (nome do fornecedor, equipa) em correspondências não exactas
        self.unmatched: set[str] = set()
        self._teams: dict[str, Team] | None = None
        self._aliases: dict[str, uuid.UUID] = {}

    def reset(self) -> None:
        self._teams = None
        self._aliases = {}

    def _load(self) -> None:
        if self._teams is not None:
            return
        self._teams = {}
        for team in self.db.scalars(select(Team)).all():
            for label in (team.name, team.short_name):
                if label:
                    self._teams.setdefault(normalize_team_name(label), team)
        for alias in self.db.scalars(select(TeamAlias).where(TeamAlias.source == self.source)).all():
            self._aliases[alias.alias_normalized] = alias.team_id

    def resolve(self, raw_name: str, country: str | None = None) -> Team | None:
        self._load()
        assert self._teams is not None
        key = normalize_team_name(raw_name)
        team_id = self._aliases.get(key)
        if team_id is not None:
            team = self.db.get(Team, team_id)
            if team is not None:
                return team

        canonical = canonical_key(raw_name)
        team = best_match(canonical, self._teams)
        if team is None:
            if not self.create_missing:
                self.unmatched.add(raw_name)
                return None
            team = Team(name=raw_name, country=country, external_ids={self.source: key})
            self.db.add(team)
            self.db.flush()
            self._teams[canonical] = team
            self.created.append(raw_name)
        else:
            known = {normalize_team_name(team.name), normalize_team_name(team.short_name or "")}
            if canonical not in known:
                self.linked.append((raw_name, team.name))

        self.db.add(TeamAlias(team_id=team.id, source=self.source, alias=raw_name[:200], alias_normalized=key))
        self._aliases[key] = team.id
        self.db.flush()
        return team
