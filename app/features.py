"""Risk scoring + tier assignment (transparent, label-free, clinician-auditable)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TIERS = ["low", "medium", "high"]


def risk_score(row) -> float:
    """Additive, explainable score from clinical features. Higher = more risk."""
    s = 0.0
    g = row.avg_glucose
    if pd.notna(g):
        if g >= 200:
            s += 3
        elif g >= 126:      # diabetic range
            s += 2
        elif g >= 100:      # pre-diabetic
            s += 1
    if row.age >= 65:
        s += 2
    elif row.age >= 50:
        s += 1
    if row.n_encounters >= 10:   # high utilization
        s += 2
    elif row.n_encounters >= 5:
        s += 1
    return s


def assign_tiers(df: pd.DataFrame, method: str = "clinical") -> pd.DataFrame:
    """Add `risk_score` and `risk_tier` columns. Deterministic + null-safe."""
    df = df.copy()
    df["risk_score"] = df.apply(risk_score, axis=1)
    if method == "clinical":                     # fixed, comparable thresholds
        tier = pd.cut(df["risk_score"], bins=[-1, 1, 3, np.inf], labels=TIERS)
    else:                                        # data-driven tertiles
        tier = pd.qcut(df["risk_score"], q=3, labels=TIERS, duplicates="drop")
    # store as plain strings -> JSON-safe, no pandas Categorical leaking into responses
    df["risk_tier"] = tier.astype(str)
    return df
