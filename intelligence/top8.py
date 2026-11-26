from dataclasses import dataclass


@dataclass(frozen=True)
class RankedPrediction:
    fixture_id: str
    market: str
    selection: str
    probability: float
    confidence: float
    data_quality: float
    edge: float = 0.0


def classify_risk(probability: float) -> str:
    if probability >= 0.72:
        return "conservative"
    if probability >= 0.62:
        return "balanced"
    return "bold"


def select_top8(predictions: list[RankedPrediction], min_probability: float = 0.58, min_quality: float = 0.40) -> list[RankedPrediction]:
    eligible = [p for p in predictions if p.probability >= min_probability and p.data_quality >= min_quality]
    ranked = sorted(eligible, key=lambda p: (p.confidence * 0.7 + p.probability * 0.3), reverse=True)

    groups = {"conservative": [], "balanced": [], "bold": []}
    used_fixtures = set()
    for prediction in ranked:
        if prediction.fixture_id in used_fixtures:
            continue
        risk = classify_risk(prediction.probability)
        groups[risk].append(prediction)
        used_fixtures.add(prediction.fixture_id)

    selected = []
    for risk, limit in (("conservative", 2), ("balanced", 4), ("bold", 2)):
        selected.extend(groups[risk][:limit])

    if len(selected) < 8:
        selected_ids = {p.fixture_id for p in selected}
        for prediction in ranked:
            if prediction.fixture_id not in selected_ids:
                selected.append(prediction)
                selected_ids.add(prediction.fixture_id)
            if len(selected) == 8:
                break
    return selected
