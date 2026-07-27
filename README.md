# Patient Analytics API — Cohort Risk Segmentation

Runnable FastAPI service that mirrors the Ropes assessment role: a **leakage-safe cohort
feature workflow** + a **risk-segmentation endpoint** with query parameters.

## What's inside
```
patient-analytics/
├── app/
│   ├── data.py               # synthetic SQLite seed + corrected, point-in-time cohort query
│   ├── features.py           # risk_score + assign_tiers (transparent, null-safe, JSON-safe)
│   ├── main.py               # FastAPI app: endpoint + dashboard route
│   └── static/dashboard.html # zero-dependency dashboard (donut chart + high-risk table)
├── tests/test_api.py         # pytest coverage of every query-param path
├── seed.py                   # one-shot DB seeder
└── requirements.txt
```

## Dashboard
Open `http://127.0.0.1:8000/` after starting the server. A self-contained HTML page (no build step,
no CDN) that calls the API and shows a live risk-distribution donut, tier tiles, and a high-risk
patient table, with a cohort selector. It consumes the same two calls a real dashboard would:
`?include_patients=false` for stats and `?tier_filter=high` for the review list.

## Run it
```bash
cd patient-analytics
pip install -r requirements.txt
python seed.py                          # build synthetic clinical.db
uvicorn app.main:app --reload           # http://127.0.0.1:8000/docs
```

## Endpoint
`GET /cohort/{cohort_id}/risk-segments`

| Param | Type | Default | Meaning |
|-------|------|---------|---------|
| `cohort_id` (path) | str | — | `diabetic_adults` or `hypertensive_adults` |
| `include_patients` | bool | `true` | include per-patient rows, or stats only |
| `tier_filter` | str | none | `low` \| `medium` \| `high` (bad value → **422**) |

### Examples
```bash
curl "http://127.0.0.1:8000/cohort/diabetic_adults/risk-segments"
curl "http://127.0.0.1:8000/cohort/diabetic_adults/risk-segments?tier_filter=high"
curl "http://127.0.0.1:8000/cohort/diabetic_adults/risk-segments?include_patients=false"
curl "http://127.0.0.1:8000/cohort/diabetic_adults/risk-segments?tier_filter=high&include_patients=false"
```

### Response
```json
{
  "cohort_id": "diabetic_adults",
  "total_n": 62,
  "filtered_n": 62,
  "tier_filter": null,
  "distribution": {"low": 20, "medium": 28, "high": 14},
  "patients": [{"patient_id": "P0002", "risk_score": 4.0, "risk_tier": "high"}]
}
```

## Improvements over the draft endpoint
1. **`tier_filter` validated** → invalid tier returns `422`, not a confusing all-zero payload.
2. **`total_n` preserved** → the draft overwrote `n` with the filtered count and lost the cohort
   total; here `distribution` always reports the *full* cohort truth while `filtered_n` reflects
   the filter.
3. **JSON-safe types** → `risk_score`/`risk_tier` are cast from numpy/Categorical to `float`/`str`.
4. **Leakage-safe features** → cohort query is point-in-time (`<= index_date`), dedups diagnoses,
   drops deceased patients, treats glucose `0` as missing, avoids join fan-out.

## Test
```bash
pytest -q
```
