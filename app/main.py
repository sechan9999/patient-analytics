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
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .data import COHORTS, load_cohort
from .features import TIERS, assign_tiers

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
