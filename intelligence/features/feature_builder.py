from dataclasses import dataclass
from datetime import datetime, timezone


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


def missing_quality(home_snapshot: TeamSnapshot, away_snapshot: TeamSnapshot) -> float:
    coverage_home = min(home_snapshot.matches / 10.0, 1.0)
    coverage_away = min(away_snapshot.matches / 10.0, 1.0)
    return 0.5 * coverage_home + 0.5 * coverage_away
