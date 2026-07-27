"""API tests — run with: pytest -q  (from the patient-analytics/ dir)."""
from fastapi.testclient import TestClient

from app.data import seed_db
from app.main import app

seed_db()                       # deterministic dataset
client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert "diabetic_adults" in r.json()["cohorts"]


def test_default_call_includes_patients():
    r = client.get("/cohort/diabetic_adults/risk-segments")
    assert r.status_code == 200
    body = r.json()
    assert body["total_n"] == body["filtered_n"]           # no filter
    assert set(body["distribution"]) == {"low", "medium", "high"}
    assert sum(body["distribution"].values()) == body["total_n"]
    assert len(body["patients"]) == body["total_n"]


def test_include_patients_false_omits_list():
    r = client.get("/cohort/diabetic_adults/risk-segments?include_patients=false")
    body = r.json()
    assert "patients" not in body


def test_tier_filter_high():
    r = client.get("/cohort/diabetic_adults/risk-segments?tier_filter=high")
    body = r.json()
    # distribution still reports the FULL cohort truth
    assert sum(body["distribution"].values()) == body["total_n"]
    # filtered list + count reflect only the high tier
    assert body["filtered_n"] == body["distribution"]["high"]
    assert all(p["risk_tier"] == "high" for p in body["patients"])


def test_invalid_tier_filter_422():
    r = client.get("/cohort/diabetic_adults/risk-segments?tier_filter=extreme")
    assert r.status_code == 422


def test_unknown_cohort_404():
    r = client.get("/cohort/nope/risk-segments")
    assert r.status_code == 404


def test_json_safe_types():
    r = client.get("/cohort/diabetic_adults/risk-segments?tier_filter=high")
    p = r.json()["patients"][0]
    assert isinstance(p["risk_score"], (int, float))
    assert isinstance(p["risk_tier"], str)
