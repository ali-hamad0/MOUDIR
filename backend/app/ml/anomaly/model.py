"""The trained revenue-anomaly artifact (Phase 6, Task 6.9) — the object joblib saves.

The detector answers "is today's revenue weird FOR THIS KIND OF DAY?" without screaming
every Ramadan (the constitution's explicit pitfall). It works in two stages:

  1. a seasonal EXPECTATION model predicts the day's revenue from its calendar context
     (weekday/Ramadan/summer/payday) + a trailing baseline level — so a high-but-expected
     Ramadan day is predicted high, and only the UNEXPLAINED part is suspicious;
  2. the residual is made SCALE-FREE (divided by the trailing baseline) so one global
     threshold works across tenants of very different revenue size (a bakery vs a resort
     shop), then flagged by the chosen method (robust z-score / IQR / IsolationForest).

`deviation` from the feature builder (= revenue − baseline_mean) is deliberately NOT a
feature of the expectation model: it is a deterministic function of revenue, so feeding it
in would let the model reconstruct revenue exactly and the residual would be ~0 — useless.

This class is importable at a stable path so a joblib-loaded artifact rehydrates cleanly;
`flag(features)` takes the build_anomaly_features frame and returns a bool per row.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.ml.features.anomaly import feature_columns

# What the seasonal expectation model is trained on: the calendar flags + the trailing
# baseline (level), but NOT `deviation` (a deterministic function of revenue — see above).
REGRESSOR_FEATURES = [c for c in feature_columns() if c != "deviation"]
# The pure calendar flags (no baseline / revenue) — extra context for IsolationForest.
FLAG_COLUMNS = [
    c for c in feature_columns() if c not in ("baseline_mean", "baseline_std", "deviation")
]


@dataclass
class SeasonalAnomalyDetector:
    """Seasonal expectation + a scale-free relative-residual threshold. The winning method
    (zscore | iqr | isolation_forest) and its fitted parameters are all carried here."""

    seasonal_model: object  # fitted regressor: REGRESSOR_FEATURES -> revenue
    method: str  # "zscore" | "iqr" | "isolation_forest"
    threshold: float = 3.5  # z cutoff (zscore); IQR whisker multiplier (iqr); unused for IF
    center: float = 0.0  # median(relative residual) — zscore
    scale: float = 1.0  # 1.4826 * MAD(relative residual) — zscore
    lower: float = 0.0  # IQR lower bound on the relative residual
    upper: float = 0.0  # IQR upper bound
    iforest: object | None = None  # fitted IsolationForest (isolation_forest method)
    regressor_features: list[str] = field(default_factory=lambda: list(REGRESSOR_FEATURES))
    flag_columns: list[str] = field(default_factory=lambda: list(FLAG_COLUMNS))

    def relative_residual(self, features: pd.DataFrame) -> np.ndarray:
        """The scale-free residual: (revenue − expected) / (trailing baseline + 1)."""
        expected = self.seasonal_model.predict(features[self.regressor_features])
        residual = features["revenue"].to_numpy(dtype=float) - np.asarray(expected, dtype=float)
        return residual / (features["baseline_mean"].to_numpy(dtype=float) + 1.0)

    def flag(self, features: pd.DataFrame) -> np.ndarray:
        """True where the day's revenue is anomalous for its seasonal context."""
        rel = self.relative_residual(features)
        if self.method == "isolation_forest":
            matrix = np.column_stack([rel, features[self.flag_columns].to_numpy(dtype=float)])
            return self.iforest.predict(matrix) == -1
        if self.method == "zscore":
            z = (rel - self.center) / (self.scale or 1.0)
            return np.abs(z) > self.threshold
        # iqr
        return (rel < self.lower) | (rel > self.upper)

    def isolation_matrix(self, rel: np.ndarray, features: pd.DataFrame) -> np.ndarray:
        """The IsolationForest input layout — shared by fit (trainer) and flag (here)."""
        return np.column_stack([rel, features[self.flag_columns].to_numpy(dtype=float)])
