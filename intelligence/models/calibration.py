from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize


def fit_platt(probabilities: np.ndarray, labels: np.ndarray) -> dict | None:
    probabilities = np.asarray(probabilities, dtype=float).reshape(-1)
    labels = np.asarray(labels, dtype=float).reshape(-1)
    if len(probabilities) != len(labels) or len(labels) < 20:
        return None
    if not np.isfinite(probabilities).all() or not np.isfinite(labels).all():
        return None
    if np.any((labels < 0) | (labels > 1)) or len(np.unique(labels)) < 2:
        return None

    clipped = np.clip(probabilities, 1e-6, 1 - 1e-6)
    logits = np.log(clipped / (1 - clipped))

    def objective(parameters: np.ndarray) -> float:
        slope, intercept = parameters
        calibrated_logits = np.clip(slope * logits + intercept, -35, 35)
        return float(np.mean(np.logaddexp(0, calibrated_logits) - labels * calibrated_logits)
                     + 1e-4 * (slope - 1) ** 2)

    fit = minimize(objective, np.array([1.0, 0.0]), method="L-BFGS-B", bounds=[(0.05, 10.0), (-10.0, 10.0)])
    if not fit.success or not np.isfinite(fit.x).all():
        return None
    return {"slope": float(fit.x[0]), "intercept": float(fit.x[1]), "n": int(len(labels))}


def apply_platt(probability: float, parameters: dict | None) -> float:
    probability = float(np.clip(probability, 0.0, 1.0))
    if not parameters:
        return probability
    slope = float(parameters.get("slope", 1.0))
    intercept = float(parameters.get("intercept", 0.0))
    clipped = float(np.clip(probability, 1e-6, 1 - 1e-6))
    logit = math.log(clipped / (1 - clipped))
    calibrated = slope * logit + intercept
    if calibrated >= 0:
        return float(1 / (1 + math.exp(-min(calibrated, 35))))
    exp_value = math.exp(max(calibrated, -35))
    return float(exp_value / (1 + exp_value))