import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


class ResultLogisticModel:
    def __init__(self):
        self.scaler = StandardScaler()
        self.model = LogisticRegression(max_iter=1000)

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        if len(features) < 30:
            raise ValueError("Insufficient chronologically prepared history")
        scaled = self.scaler.fit_transform(features)
        self.model.fit(scaled, labels)

    def predict_proba(self, features: np.ndarray) -> tuple[list[int], np.ndarray]:
        scaled = self.scaler.transform(features)
        return list(self.model.classes_), self.model.predict_proba(scaled)


class ComplementaryRandomForest:
    def __init__(self):
        self.model = RandomForestClassifier(n_estimators=200, min_samples_leaf=5, random_state=42, n_jobs=-1)

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        if len(features) < 50:
            raise ValueError("Insufficient history for Random Forest")
        self.model.fit(features, labels)

    def predict_proba(self, features: np.ndarray) -> tuple[list[int], np.ndarray]:
        return list(self.model.classes_), self.model.predict_proba(features)
