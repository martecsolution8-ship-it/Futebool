from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def score_matrix(home_lambda: float, away_lambda: float, rho: float = -0.05, max_goals: int = 10) -> np.ndarray:
    home_lambda = float(np.clip(home_lambda, 0.05, 8.0))
    away_lambda = float(np.clip(away_lambda, 0.05, 8.0))
    goals = np.arange(max_goals + 1)
    matrix = np.outer(poisson.pmf(goals, home_lambda), poisson.pmf(goals, away_lambda))

    # Dixon-Coles adjusts the four low-score outcomes for dependence between goals.
    matrix[0, 0] *= max(0.0, 1 - home_lambda * away_lambda * rho)
    if max_goals >= 1:
        matrix[0, 1] *= max(0.0, 1 + home_lambda * rho)
        matrix[1, 0] *= max(0.0, 1 + away_lambda * rho)
        matrix[1, 1] *= max(0.0, 1 - rho)
    total = float(matrix.sum())
    return matrix / total if total > 0 else np.full_like(matrix, 1 / matrix.size)


def market_probabilities(matrix: np.ndarray) -> dict[str, float]:
    matrix = np.asarray(matrix, dtype=float)
    total = float(matrix.sum())
    if matrix.ndim != 2 or not np.isfinite(matrix).all() or total <= 0:
        raise ValueError("Score matrix must be a finite, non-empty probability matrix")
    matrix = matrix / total
    home_goals, away_goals = np.indices(matrix.shape)
    total_goals = home_goals + away_goals
    return {
        "home_win": float(matrix[home_goals > away_goals].sum()),
        "draw": float(matrix[home_goals == away_goals].sum()),
        "away_win": float(matrix[home_goals < away_goals].sum()),
        "over_15": float(matrix[total_goals >= 2].sum()),
        "over_25": float(matrix[total_goals >= 3].sum()),
        "under_25": float(matrix[total_goals <= 2].sum()),
        "btts": float(matrix[(home_goals >= 1) & (away_goals >= 1)].sum()),
        "over_35": float(matrix[total_goals >= 4].sum()),
        "under_35": float(matrix[total_goals <= 3].sum()),
        "home_over_05": float(matrix[home_goals >= 1].sum()),
        "home_over_15": float(matrix[home_goals >= 2].sum()),
        "away_over_05": float(matrix[away_goals >= 1].sum()),
        "away_over_15": float(matrix[away_goals >= 2].sum()),
    }


@dataclass
class StrengthModel:
    teams: dict[str, int]
    attack: np.ndarray
    defense: np.ndarray
    home_adv: float
    baseline: float
    matches_per_team: dict[str, int]
    rho: float = -0.05
    max_goals: int = 10

    def lambdas(self, home_team: str, away_team: str, competition_id: str | None = None) -> tuple[float, float]:
        home_index, away_index = self.teams[home_team], self.teams[away_team]
        home = np.exp(self.baseline + self.home_adv + self.attack[home_index] - self.defense[away_index])
        away = np.exp(self.baseline + self.attack[away_index] - self.defense[home_index])
        return float(np.clip(home, 0.05, 8.0)), float(np.clip(away, 0.05, 8.0))

    def score_matrix(self, home_team: str, away_team: str, competition_id: str | None = None) -> np.ndarray:
        home, away = self.lambdas(home_team, away_team, competition_id)
        return score_matrix(home, away, self.rho, self.max_goals)


def _tau_log(home_goals: np.ndarray, away_goals: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """log do fator de dependência de Dixon-Coles (só os 4 resultados baixos são alterados)."""
    tau = np.ones_like(lam)
    tau = np.where((home_goals == 0) & (away_goals == 0), 1.0 - lam * mu * rho, tau)
    tau = np.where((home_goals == 0) & (away_goals == 1), 1.0 + lam * rho, tau)
    tau = np.where((home_goals == 1) & (away_goals == 0), 1.0 + mu * rho, tau)
    tau = np.where((home_goals == 1) & (away_goals == 1), 1.0 - rho, tau)
    return np.log(np.clip(tau, 1e-9, None))


def estimate_rho(home_goals, away_goals, lam, mu, weights, lower: float = -0.20, upper: float = 0.05) -> float:
    """Máxima verosimilhança (1 parâmetro) da dependência de Dixon-Coles, dado o ataque/defesa já ajustados."""
    from scipy.optimize import minimize_scalar

    def negative_loglik(rho: float) -> float:
        return float(-np.sum(weights * _tau_log(home_goals, away_goals, lam, mu, rho)))

    result = minimize_scalar(negative_loglik, bounds=(lower, upper), method="bounded")
    return float(result.x) if result.success and np.isfinite(result.x) else -0.05


def fit_strength_model(rows: list[dict], as_of: datetime, half_life_days: float = 240.0,
                       ridge: float = 6.0, rho: float = -0.05, max_goals: int = 10,
                       estimate_dependence: bool = True) -> StrengthModel | None:
    cutoff = _aware(as_of)
    # Só entram jogos anteriores ao instante da análise e com resultado conhecido (sem fuga de futuro).
    history = [row for row in rows if _aware(row["kickoff_at"]) < cutoff
               and row.get("home_score") is not None and row.get("away_score") is not None]
    if len(history) < 30:
        return None

    teams = sorted({str(row[key]) for row in history for key in ("home_team_id", "away_team_id")})
    if len(teams) < 4:
        return None
    team_index = {team: index for index, team in enumerate(teams)}
    home_index = np.asarray([team_index[str(row["home_team_id"])] for row in history], dtype=int)
    away_index = np.asarray([team_index[str(row["away_team_id"])] for row in history], dtype=int)
    home_goals = np.asarray([row["home_score"] for row in history], dtype=float)
    away_goals = np.asarray([row["away_score"] for row in history], dtype=float)
    if (home_goals < 0).any() or (away_goals < 0).any() or not np.isfinite(home_goals).all() or not np.isfinite(away_goals).all():
        return None

    latest = max(_aware(row["kickoff_at"]) for row in history)
    age_days = np.asarray([(latest - _aware(row["kickoff_at"])).total_seconds() / 86400 for row in history])
    weights = np.exp(-np.log(2) * age_days / max(float(half_life_days), 1.0))
    counts = {team: int(np.sum((home_index == index) | (away_index == index))) for team, index in team_index.items()}
    team_count = len(teams)
    initial = np.zeros(2 + 2 * team_count, dtype=float)
    initial[0] = np.log(max((home_goals.mean() + away_goals.mean()) / 2, 0.2))
    log_factorial = gammaln(home_goals + 1) + gammaln(away_goals + 1)

    def objective(parameters: np.ndarray):
        """Perda (Poisson ponderada + ridge) e o seu gradiente analítico (muito mais rápido que diferenças finitas)."""
        baseline, home_adv = parameters[:2]
        attack = parameters[2:2 + team_count]
        defense = parameters[2 + team_count:]
        raw_home = baseline + home_adv + attack[home_index] - defense[away_index]
        raw_away = baseline + attack[away_index] - defense[home_index]
        home_log_rate = np.clip(raw_home, -4, 3)
        away_log_rate = np.clip(raw_away, -4, 3)
        home_rate, away_rate = np.exp(home_log_rate), np.exp(away_log_rate)
        loss = float(np.sum(weights * (home_rate - home_goals * home_log_rate + away_rate - away_goals * away_log_rate + log_factorial)))
        loss += ridge * float(np.dot(attack, attack) + np.dot(defense, defense))
        loss += 100.0 * float(attack.mean() ** 2 + defense.mean() ** 2)

        # d loss / d (log taxa); zero onde o clip está ativo
        g_home = weights * (home_rate - home_goals) * ((raw_home > -4) & (raw_home < 3))
        g_away = weights * (away_rate - away_goals) * ((raw_away > -4) & (raw_away < 3))
        gradient = np.zeros_like(parameters)
        gradient[0] = g_home.sum() + g_away.sum()
        gradient[1] = g_home.sum()
        gradient[2:2 + team_count] = (np.bincount(home_index, weights=g_home, minlength=team_count)
                                      + np.bincount(away_index, weights=g_away, minlength=team_count)
                                      + 2 * ridge * attack + 200.0 * attack.mean() / team_count)
        gradient[2 + team_count:] = (-np.bincount(away_index, weights=g_home, minlength=team_count)
                                     - np.bincount(home_index, weights=g_away, minlength=team_count)
                                     + 2 * ridge * defense + 200.0 * defense.mean() / team_count)
        return loss, gradient

    bounds = [(-1.5, 1.5), (-0.5, 1.0)] + [(-1.5, 1.5)] * (2 * team_count)
    result = minimize(objective, initial, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 1000})
    if not np.isfinite(result.x).all():
        return None

    attack = result.x[2:2 + team_count]
    defense = result.x[2 + team_count:]
    attack = attack - attack.mean()
    defense = defense - defense.mean()
    baseline, home_adv = float(result.x[0]), float(result.x[1])

    if estimate_dependence:
        lam = np.exp(np.clip(baseline + home_adv + attack[home_index] - defense[away_index], -4, 3))
        mu = np.exp(np.clip(baseline + attack[away_index] - defense[home_index], -4, 3))
        rho = estimate_rho(home_goals, away_goals, lam, mu, weights)
    return StrengthModel(team_index, attack, defense, home_adv, baseline, counts, rho, max_goals)
