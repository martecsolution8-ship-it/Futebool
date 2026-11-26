from dataclasses import dataclass
from datetime import datetime, timezone
from bisect import bisect_left


@dataclass
class TeamSnapshot:
    matches: int
    wins: int
    draws: int
    losses: int
    goals_for: float
    goals_against: float
    recent_form: float
    matches_5: int = 0
    matches_10: int = 0
    home_matches: int = 0
    home_goals_for_avg: float = 0.0
    home_goals_against_avg: float = 0.0
    away_matches: int = 0
    away_goals_for_avg: float = 0.0
    away_goals_against_avg: float = 0.0
    over_15_pct: float = 0.0
    over_25_pct: float = 0.0
    btts_pct: float = 0.0
    clean_sheet_pct: float = 0.0
    rest_days: int | None = None

    def as_vector(self) -> list[float]:
        if self.matches == 0:
            return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return [
            self.wins / self.matches,
            self.draws / self.matches,
            self.goals_for / self.matches,
            self.goals_against / self.matches,
            self.recent_form / max(self.matches, 1),
            self.losses / self.matches,
        ]


def team_history(fixtures: list[dict], team_id: str, cutoff: datetime, limit: int = 10) -> TeamSnapshot:
    cutoff = cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=timezone.utc)
    past = []
    for fixture in fixtures:
        kickoff = fixture["kickoff_at"]
        kickoff = kickoff if kickoff.tzinfo is not None else kickoff.replace(tzinfo=timezone.utc)
        if kickoff >= cutoff:
            continue
        if fixture["home_team_id"] != team_id and fixture["away_team_id"] != team_id:
            continue
        if fixture["home_score"] is None or fixture["away_score"] is None:
            continue
        past.append(fixture)

    past.sort(key=lambda item: item["kickoff_at"] if item["kickoff_at"].tzinfo is not None else item["kickoff_at"].replace(tzinfo=timezone.utc), reverse=True)
    recent = past[:limit]
    wins = draws = losses = 0
    goals_for = 0.0
    goals_against = 0.0
    recent_form = 0.0

    for index, fixture in enumerate(recent):
        is_home = fixture["home_team_id"] == team_id
        scored = fixture["home_score"] if is_home else fixture["away_score"]
        conceded = fixture["away_score"] if is_home else fixture["home_score"]
        goals_for += scored
        goals_against += conceded
        if scored > conceded:
            wins += 1
            points = 3
        elif scored == conceded:
            draws += 1
            points = 1
        else:
            losses += 1
            points = 0
        weight = 1.0 / (1.0 + index * 0.15)
        recent_form += points * weight

    def venue_stats(is_home: bool) -> tuple[int, float, float]:
        venue = [f for f in recent if (f["home_team_id"] == team_id) == is_home]
        if not venue:
            return 0, 0.0, 0.0
        for_goals = sum(f["home_score"] if is_home else f["away_score"] for f in venue)
        against_goals = sum(f["away_score"] if is_home else f["home_score"] for f in venue)
        return len(venue), for_goals / len(venue), against_goals / len(venue)

    home_matches, home_gf, home_ga = venue_stats(True)
    away_matches, away_gf, away_ga = venue_stats(False)
    n = len(recent)
    over_15 = sum((f["home_score"] + f["away_score"] >= 2) for f in recent) / n if n else 0.0
    over_25 = sum((f["home_score"] + f["away_score"] >= 3) for f in recent) / n if n else 0.0
    btts = sum((f["home_score"] >= 1 and f["away_score"] >= 1) for f in recent) / n if n else 0.0
    clean_sheets = sum(
        (f["away_score"] == 0 if f["home_team_id"] == team_id else f["home_score"] == 0)
        for f in recent
    ) / n if n else 0.0

    rest_days = None
    if recent:
        latest = recent[0]["kickoff_at"]
        latest = latest if latest.tzinfo is not None else latest.replace(tzinfo=timezone.utc)
        rest_days = max(0, (cutoff - latest).days)

    return TeamSnapshot(
        len(recent), wins, draws, losses, goals_for, goals_against, recent_form,
        matches_5=min(len(recent), 5), matches_10=len(recent),
        home_matches=home_matches, home_goals_for_avg=home_gf, home_goals_against_avg=home_ga,
        away_matches=away_matches, away_goals_for_avg=away_gf, away_goals_against_avg=away_ga,
        over_15_pct=over_15, over_25_pct=over_25, btts_pct=btts, clean_sheet_pct=clean_sheets,
        rest_days=rest_days,
    )


def head_to_head(fixtures: list[dict], home_team_id: str, away_team_id: str, cutoff: datetime, limit: int = 5) -> list[dict]:
    cutoff = cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=timezone.utc)
    matches = []
    for fixture in fixtures:
        kickoff = fixture["kickoff_at"]
        kickoff = kickoff if kickoff.tzinfo is not None else kickoff.replace(tzinfo=timezone.utc)
        if kickoff >= cutoff:
            continue
        pair = {str(fixture["home_team_id"]), str(fixture["away_team_id"])}
        if pair == {str(home_team_id), str(away_team_id)} and fixture["home_score"] is not None and fixture["away_score"] is not None:
            matches.append(fixture)
    matches.sort(key=lambda item: item["kickoff_at"], reverse=True)
    return matches[:limit]


class HistoricalIndex:
    """Índice por equipa e confronto directo reutilizado durante uma execução."""

    def __init__(self, fixtures: list[dict]):
        self._team: dict[str, list[dict]] = {}
        self._h2h: dict[tuple[str, str], list[dict]] = {}
        self._team_times: dict[str, list[datetime]] = {}
        self._h2h_times: dict[tuple[str, str], list[datetime]] = {}
        self._cache: dict[tuple[str, datetime, int], TeamSnapshot] = {}
        for fixture in fixtures:
            kickoff = fixture["kickoff_at"]
            kickoff = kickoff if kickoff.tzinfo is not None else kickoff.replace(tzinfo=timezone.utc)
            normalized = dict(fixture)
            normalized["kickoff_at"] = kickoff
            home_id = str(fixture["home_team_id"])
            away_id = str(fixture["away_team_id"])
            if fixture["home_score"] is None or fixture["away_score"] is None:
                continue
            self._team.setdefault(home_id, []).append(normalized)
            self._team.setdefault(away_id, []).append(normalized)
            key = tuple(sorted((home_id, away_id)))
            self._h2h.setdefault(key, []).append(normalized)
        for team_id, matches in self._team.items():
            matches.sort(key=lambda item: item["kickoff_at"])
            self._team_times[team_id] = [item["kickoff_at"] for item in matches]
        for key, matches in self._h2h.items():
            matches.sort(key=lambda item: item["kickoff_at"])
            self._h2h_times[key] = [item["kickoff_at"] for item in matches]

    @staticmethod
    def _cutoff(cutoff: datetime) -> datetime:
        return cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=timezone.utc)

    def team_history(self, team_id: str, cutoff: datetime, limit: int = 10) -> TeamSnapshot:
        cutoff = self._cutoff(cutoff)
        cache_key = (str(team_id), cutoff, limit)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        matches = self._team.get(str(team_id), [])
        cut_index = bisect_left(self._team_times.get(str(team_id), []), cutoff)
        past = matches[:cut_index]
        recent = list(reversed(past))[:limit]

        wins = draws = losses = 0
        goals_for = 0.0
        goals_against = 0.0
        recent_form = 0.0
        for index, fixture in enumerate(recent):
            is_home = str(fixture["home_team_id"]) == str(team_id)
            scored = fixture["home_score"] if is_home else fixture["away_score"]
            conceded = fixture["away_score"] if is_home else fixture["home_score"]
            goals_for += scored
            goals_against += conceded
            if scored > conceded:
                wins += 1
                points = 3
            elif scored == conceded:
                draws += 1
                points = 1
            else:
                losses += 1
                points = 0
            recent_form += points * (1.0 / (1.0 + index * 0.15))

        def venue_stats(is_home: bool) -> tuple[int, float, float]:
            venue = [fixture for fixture in recent if (str(fixture["home_team_id"]) == str(team_id)) == is_home]
            if not venue:
                return 0, 0.0, 0.0
            for_goals = sum(fixture["home_score"] if is_home else fixture["away_score"] for fixture in venue)
            against_goals = sum(fixture["away_score"] if is_home else fixture["home_score"] for fixture in venue)
            return len(venue), for_goals / len(venue), against_goals / len(venue)

        home_matches, home_gf, home_ga = venue_stats(True)
        away_matches, away_gf, away_ga = venue_stats(False)
        n = len(recent)
        over_15 = sum(f["home_score"] + f["away_score"] >= 2 for f in recent) / n if n else 0.0
        over_25 = sum(f["home_score"] + f["away_score"] >= 3 for f in recent) / n if n else 0.0
        btts = sum(f["home_score"] >= 1 and f["away_score"] >= 1 for f in recent) / n if n else 0.0
        clean_sheets = sum(
            (f["away_score"] == 0 if str(f["home_team_id"]) == str(team_id) else f["home_score"] == 0)
            for f in recent
        ) / n if n else 0.0
        rest_days = None
        if recent:
            rest_days = max(0, (cutoff - recent[0]["kickoff_at"]).days)

        snapshot = TeamSnapshot(
            n, wins, draws, losses, goals_for, goals_against, recent_form,
            matches_5=min(n, 5), matches_10=n,
            home_matches=home_matches, home_goals_for_avg=home_gf, home_goals_against_avg=home_ga,
            away_matches=away_matches, away_goals_for_avg=away_gf, away_goals_against_avg=away_ga,
            over_15_pct=over_15, over_25_pct=over_25, btts_pct=btts, clean_sheet_pct=clean_sheets,
            rest_days=rest_days,
        )
        self._cache[cache_key] = snapshot
        return snapshot

    def head_to_head(self, home_team_id: str, away_team_id: str, cutoff: datetime, limit: int = 5) -> list[dict]:
        cutoff = self._cutoff(cutoff)
        key = tuple(sorted((str(home_team_id), str(away_team_id))))
        matches = self._h2h.get(key, [])
        cut_index = bisect_left(self._h2h_times.get(key, []), cutoff)
        return list(reversed(matches[:cut_index]))[:limit]


def missing_quality(home_snapshot: TeamSnapshot, away_snapshot: TeamSnapshot) -> float:
    coverage_home = min(home_snapshot.matches / 10.0, 1.0)
    coverage_away = min(away_snapshot.matches / 10.0, 1.0)
    return 0.5 * coverage_home + 0.5 * coverage_away
