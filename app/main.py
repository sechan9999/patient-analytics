"""Patient Analytics API — cohort risk segmentation.

Upgraded endpoint with query parameters:
  - include_patients: include the per-patient list or return stats only
  - tier_filter:      restrict to a single risk tier

Hardening over the draft version:
  * tier_filter is validated (bad value -> 422, not a silent empty result)
  * total_n is always the full cohort size; filtered_n reflects the filter
    (the draft overwrote `n` and lost the cohort total)
  * all values coerced to JSON-safe primitives (no numpy / Categorical)
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .data import COHORTS, load_cohort
from .features import TIERS, assign_tiers
from .readmit_data import COHORTS as READMIT_COHORTS, load_readmit_cohort
from .readmit_model import CUTS as READMIT_CUTS, assign as readmit_assign, fit_and_score as readmit_fit

app = FastAPI(title="Patient Analytics API", version="1.0.0")

_STATIC = os.path.join(os.path.dirname(__file__), "static")


_ROOT = os.path.dirname(os.path.dirname(__file__))

@app.get("/", include_in_schema=False)
def dashboard():
    index_path = os.path.join(_ROOT, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return FileResponse(os.path.join(_STATIC, "dashboard.html"))


@app.get("/health")
def health():
    return {"status": "ok", "cohorts": list(COHORTS)}


@app.get("/cohorts")
def list_cohorts():
    return {cid: meta["label"] for cid, meta in COHORTS.items()}


@app.get("/cohort/{cohort_id}/risk-segments")
def risk_segments(
    cohort_id: str,
    include_patients: bool = Query(True, description="Include per-patient rows"),
    tier_filter: Optional[str] = Query(None, description="Restrict to one tier: low|medium|high"),
):
    # validate tier_filter up front -> clear 422 instead of a confusing empty payload
    if tier_filter is not None and tier_filter not in TIERS:
        raise HTTPException(422, f"tier_filter must be one of {TIERS}, got '{tier_filter}'")

    df = load_cohort(cohort_id)
    if df.empty:
        raise HTTPException(404, "cohort not found or empty")

    seg = assign_tiers(df)
    total_n = int(len(seg))

    # full-cohort distribution (computed before filtering so it always tells the truth)
    full_counts = seg["risk_tier"].value_counts().reindex(TIERS, fill_value=0)

    if tier_filter:
        seg = seg[seg["risk_tier"] == tier_filter]

    response = {
        "cohort_id": cohort_id,
        "total_n": total_n,                       # full cohort size
        "filtered_n": int(len(seg)),              # after tier_filter (== total_n if no filter)
        "tier_filter": tier_filter,
        "distribution": {k: int(v) for k, v in full_counts.items()},
    }

    if include_patients:
        cols = ["patient_id", "risk_score", "risk_tier"]
        response["patients"] = [
            {"patient_id": str(r.patient_id),
             "risk_score": float(r.risk_score),
             "risk_tier": str(r.risk_tier)}
            for r in seg[cols].itertuples(index=False)
        ]

    return response


# --------------------------------------------------------------------------- #
# Supervised readmission-risk segmentation (heart-failure cohort)
# --------------------------------------------------------------------------- #
READMIT_MODEL_VERSION = "lr-v1"


@lru_cache(maxsize=8)
def _readmit_scored(cohort_id: str):
    """Load cohort, fit model, assign tiers. Cached per cohort. None if empty/unknown."""
    df = load_readmit_cohort(cohort_id)
    if df.empty:
        return None
    model, auroc = readmit_fit(df)
    return readmit_assign(df, model), auroc


@app.get("/cohort/{cohort_id}/readmit-risk")
def readmit_risk(
    cohort_id: str,
    include_patients: bool = Query(True),
    tier_filter: Optional[str] = Query(None, description="low|medium|high"),
):
    if tier_filter is not None and tier_filter not in TIERS:
        raise HTTPException(422, f"tier_filter must be one of {TIERS}, got '{tier_filter}'")

    scored = _readmit_scored(cohort_id)
    if scored is None:
        raise HTTPException(404, "cohort not found or empty")
    seg, auroc = scored

    total_n = int(len(seg))
    full_counts = seg["risk_tier"].value_counts().reindex(TIERS, fill_value=0)  # full-cohort truth
    view = seg[seg["risk_tier"] == tier_filter] if tier_filter else seg

    high = seg[seg["risk_tier"] == "high"]
    calibration = {
        "high_cutoff": READMIT_CUTS["high"],
        "high_mean_prob": round(float(high["risk_prob"].mean()), 3) if len(high) else None,
        "high_observed_readmit_rate": round(float(high["readmit_30d"].mean()), 3) if len(high) else None,
    }

    response = {
        "cohort_id": cohort_id,
        "model_version": READMIT_MODEL_VERSION,
        "cv_auroc": round(auroc, 3) if auroc is not None else None,
        "total_n": total_n,
        "filtered_n": int(len(view)),
        "tier_filter": tier_filter,
        "distribution": {k: int(v) for k, v in full_counts.items()},
        "calibration": calibration,
    }
    if include_patients:
        response["patients"] = [
            {"patient_id": str(r.patient_id),
             "risk_prob": round(float(r.risk_prob), 3),
             "risk_tier": str(r.risk_tier),
             "readmit_30d": int(r.readmit_30d)}
            for r in view.sort_values("risk_prob", ascending=False).itertuples(index=False)
        ]
    return response
