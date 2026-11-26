import numpy as np
from sklearn.metrics import brier_score_loss, log_loss


def brier_multiclass(labels: np.ndarray, probabilities: np.ndarray) -> float:
    if len(labels) == 0:
        return float("nan")
    labels_array = np.asarray(labels)
    probabilities_array = np.asarray(probabilities)
    class_count = probabilities_array.shape[1] if probabilities_array.ndim == 2 else 1
    one_hot = np.eye(class_count)[labels_array]
    return float(np.mean(np.sum((probabilities_array - one_hot) ** 2, axis=1)))


def log_loss_safe(labels: np.ndarray, probabilities: np.ndarray, labels_expected: list[int]) -> float:
    if len(labels) == 0:
        return float("nan")
    return float(log_loss(labels, probabilities, labels=labels_expected))


def calibration_bins(labels: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> list[dict]:
    if len(labels) == 0:
        return []
    edges = np.linspace(0.0, 1.0, bins + 1)
    result = []
    for start, end in zip(edges[:-1], edges[1:]):
        mask = (probabilities >= start) & (probabilities < end)
        if mask.any():
            result.append({
                "lower": float(start),
                "upper": float(end),
                "count": int(mask.sum()),
                "mean_probability": float(probabilities[mask].mean()),
                "empirical_frequency": float(labels[mask].mean()),
            })
    return result
