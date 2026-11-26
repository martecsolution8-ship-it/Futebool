import math
from dataclasses import dataclass

from scipy.stats import poisson


@dataclass(frozen=True)
class MatchProbabilities:
    home_win: float
    draw: float
    away_win: float
    over_15: float
    over_25: float
    under_25: float
    both_score: float


def poisson_matrix(home_goals_mean: float, away_goals_mean: float, max_goals: int = 10) -> tuple[list[list[float]], MatchProbabilities]:
    home_goals_mean = max(float(home_goals_mean), 0.05)
    away_goals_mean = max(float(away_goals_mean), 0.05)
    matrix = [[poisson.pmf(h, home_goals_mean) * poisson.pmf(a, away_goals_mean)
               for a in range(max_goals + 1)] for h in range(max_goals + 1)]

    total = sum(sum(row) for row in matrix)
    home_win = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(h))
    draw = sum(matrix[h][h] for h in range(max_goals + 1))
    away_win = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(h + 1, max_goals + 1))
    over_15 = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a >= 2)
    over_25 = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a >= 3)
    under_25 = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a <= 2)
    both_score = sum(matrix[h][a] for h in range(1, max_goals + 1) for a in range(1, max_goals + 1))

    values = [home_win, draw, away_win]
    values = [value / sum(values) for value in values]
    return matrix, MatchProbabilities(
        home_win=values[0],
        draw=values[1],
        away_win=values[2],
        over_15=over_15 / total,
        over_25=over_25 / total,
        under_25=under_25 / total,
        both_score=both_score / total,
    )


def elo_expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


class EloTable:
    def __init__(self, base_rating: float = 1500.0, k_factor: float = 20.0):
        self.base_rating = base_rating
        self.k_factor = k_factor
        self.ratings: dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.base_rating)

    def probability(self, home_team: str, away_team: str) -> tuple[float, float, float]:
        elo_home = elo_expected(self.rating(home_team), self.rating(away_team) + 65.0)
        elo_away = 1.0 - elo_expected(self.rating(away_team), self.rating(home_team) - 65.0)
        draw = max(0.12, 0.26 - abs(elo_home - 0.5) * 0.18)
        home = (1.0 - draw) * elo_home
        away = (1.0 - draw) * elo_away
        total = home + draw + away
        return home / total, draw / total, away / total

    def update(self, home_team: str, away_team: str, home_goals: int, away_goals: int) -> None:
        if home_goals > away_goals:
            actual = 1.0
        elif home_goals == away_goals:
            actual = 0.5
        else:
            actual = 0.0
        expected = elo_expected(self.rating(home_team), self.rating(away_team) + 65.0)
        goal_factor = 1.0 + min(abs(home_goals - away_goals) * 0.25, 0.75)
        delta = self.k_factor * goal_factor * (actual - expected)
        self.ratings[home_team] = self.rating(home_team) + delta
        self.ratings[away_team] = self.rating(away_team) - delta


def normalize_probabilities(values: dict[str, float]) -> dict[str, float]:
    cleaned = {key: max(0.0, min(1.0, float(value))) for key, value in values.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return {key: 1.0 / len(cleaned) for key in cleaned}
    return {key: value / total for key, value in cleaned.items()}
