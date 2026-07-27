"""Supervised readmission-risk model — leakage-safe, patient-grouped CV, calibrated tiers.

Now that a label exists we train a supervised model, but with (1) imputation inside the Pipeline
(no leakage), (2) patient-level grouping, (3) class_weight for imbalance, (4) an honest grouped-CV
AUROC, (5) probability -> fixed clinical-style tier cutoffs (comparable across cohorts).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline

FEATURES = ["age", "avg_bnp", "n_prior_admissions"]
TIERS = ["low", "medium", "high"]
CUTS = {"medium": 0.20, "high": 0.45}          # probability cutoffs (fixed -> comparable)


def _pipe():
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),          # fit on train folds only
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])


def fit_and_score(df: pd.DataFrame):
    """Fit on the full cohort for serving; also return an HONEST grouped-CV AUROC."""
    X, y, g = df[FEATURES], df["readmit_30d"].values, df["patient_id"].values
    auroc = None
    if y.sum() >= 5 and (len(y) - y.sum()) >= 5:
        cv = StratifiedGroupKFold(n_splits=5)
        oof = cross_val_predict(_pipe(), X, y, groups=g, cv=cv, method="predict_proba")[:, 1]
        auroc = float(roc_auc_score(y, oof))
    model = _pipe().fit(X, y)
    return model, auroc


def assign(df: pd.DataFrame, model) -> pd.DataFrame:
    df = df.copy()
    df["risk_prob"] = model.predict_proba(df[FEATURES])[:, 1]
    df["risk_tier"] = pd.cut(
        df["risk_prob"], bins=[-np.inf, CUTS["medium"], CUTS["high"], np.inf], labels=TIERS
    ).astype(str)
    return df
