"""Contract tests for the supervised readmit-risk endpoint.

Locks in the hardened contract so the "filter collapses the distribution" regression can't return:
  * distribution always reflects the FULL cohort (never the filtered view)
  * total_n (full) is separate from filtered_n (after tier_filter)
  * tier_filter validated -> 422; unknown cohort -> 404
  * stable 3-tier schema, JSON-safe primitive types, probs in [0,1]
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.readmit_data import seed

TIERS = ["low", "medium", "high"]
URL = "/cohort/hf_adults/readmit-risk"


@pytest.fixture(scope="module")
def client():
    seed()                              # deterministic dataset
    return TestClient(app)


def test_default_shape(client):
    b = client.get(URL).json()
    assert set(b["distribution"]) == set(TIERS)
    assert b["total_n"] == b["filtered_n"]
    assert sum(b["distribution"].values()) == b["total_n"]
    assert len(b["patients"]) == b["total_n"]
    assert b["model_version"] == "lr-v1"


def test_tier_filter_keeps_full_distribution(client):
    full = client.get(f"{URL}?include_patients=false").json()
    hi = client.get(f"{URL}?tier_filter=high").json()
    # distribution must equal the unfiltered cohort — not collapse to {0,0,high}
    assert hi["distribution"] == full["distribution"]
    assert hi["total_n"] == full["total_n"]
    assert hi["filtered_n"] == hi["distribution"]["high"]
    assert hi["filtered_n"] <= hi["total_n"]
    assert all(p["risk_tier"] == "high" for p in hi["patients"])


def test_filtered_n_partitions_cohort(client):
    total = client.get(f"{URL}?include_patients=false").json()["total_n"]
    s = 0
    for t in TIERS:
        b = client.get(f"{URL}?tier_filter={t}&include_patients=false").json()
        assert b["total_n"] == total
        s += b["filtered_n"]
    assert s == total


def test_invalid_tier_returns_422(client):
    assert client.get(f"{URL}?tier_filter=extreme").status_code == 422


def test_unknown_cohort_returns_404(client):
    assert client.get("/cohort/nope/readmit-risk").status_code == 404


def test_include_patients_false_omits_list(client):
    assert "patients" not in client.get(f"{URL}?include_patients=false").json()


def test_json_safe_types_and_unit_interval(client):
    b = client.get(URL).json()
    p = b["patients"][0]
    assert isinstance(p["patient_id"], str)
    assert isinstance(p["risk_prob"], float)
    assert p["risk_tier"] in TIERS
    assert p["readmit_30d"] in (0, 1)
    for q in b["patients"]:
        assert 0.0 <= q["risk_prob"] <= 1.0
