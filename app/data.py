"""Synthetic clinical data + leakage-safe cohort loading.

Seeds an in-memory-ish SQLite DB with patients / encounters / labs / conditions,
then loads named cohorts using the *corrected* query (no join fan-out, point-in-time
safe, deceased excluded). This is the reliable feature workflow the API serves.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from functools import lru_cache
from random import Random

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "clinical.db")

# Registry: cohort_id -> ICD-10 prefix defining the cohort.
COHORTS: dict[str, dict] = {
    "diabetic_adults": {"icd10_prefix": "E11", "label": "Type 2 diabetes, adults >=18"},
    "hypertensive_adults": {"icd10_prefix": "I10", "label": "Essential hypertension, adults >=18"},
}

INDEX_DATE = "2026-01-01"


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def seed_db(path: str = DB_PATH, n_patients: int = 150, seed: int = 42) -> None:
    """Create a deterministic synthetic dataset. Safe to re-run (drops + rebuilds)."""
    rng = Random(seed)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS patients;
        DROP TABLE IF EXISTS encounters;
        DROP TABLE IF EXISTS labs;
        DROP TABLE IF EXISTS conditions;
        CREATE TABLE patients   (patient_id TEXT PRIMARY KEY, dob TEXT, sex TEXT, deceased_date TEXT);
        CREATE TABLE encounters (encounter_id TEXT PRIMARY KEY, patient_id TEXT, encounter_date TEXT, type TEXT);
        CREATE TABLE labs       (lab_id TEXT PRIMARY KEY, patient_id TEXT, lab_name TEXT, value REAL, taken_at TEXT);
        CREATE TABLE conditions (patient_id TEXT, icd10_code TEXT, diagnosed_at TEXT);
        """
    )

    idx = date.fromisoformat(INDEX_DATE)
    enc_id = lab_id = 0
    for i in range(1, n_patients + 1):
        pid = f"P{i:04d}"
        age = rng.randint(12, 90)                       # some minors on purpose
        dob = date(idx.year - age, rng.randint(1, 12), rng.randint(1, 28)).isoformat()
        sex = rng.choice(["F", "M"])
        # ~8% deceased (must be excluded from active cohort)
        deceased = (idx - timedelta(days=rng.randint(1, 900))).isoformat() if rng.random() < 0.08 else None
        cur.execute("INSERT INTO patients VALUES (?,?,?,?)", (pid, dob, sex, deceased))

        # Conditions: ~55% diabetic, ~35% hypertensive (can overlap). Some dupes + a future dx.
        if rng.random() < 0.55:
            dxd = (idx - timedelta(days=rng.randint(30, 1500))).isoformat()
            cur.execute("INSERT INTO conditions VALUES (?,?,?)", (pid, "E11.9", dxd))
            if rng.random() < 0.3:                      # duplicate diagnosis row
                cur.execute("INSERT INTO conditions VALUES (?,?,?)", (pid, "E11.65", dxd))
            if rng.random() < 0.05:                     # leakage trap: diagnosed AFTER index
                future = (idx + timedelta(days=rng.randint(10, 200))).isoformat()
                cur.execute("INSERT INTO conditions VALUES (?,?,?)", (pid, "E11.9", future))
        if rng.random() < 0.35:
            cur.execute(
                "INSERT INTO conditions VALUES (?,?,?)",
                (pid, "I10", (idx - timedelta(days=rng.randint(30, 1500))).isoformat()),
            )

        # Encounters: 0..14, mostly before index, a few after (must be ignored).
        for _ in range(rng.randint(0, 14)):
            enc_id += 1
            when = idx - timedelta(days=rng.randint(-120, 1000))   # negative => after index
            cur.execute(
                "INSERT INTO encounters VALUES (?,?,?,?)",
                (f"E{enc_id:06d}", pid, when.isoformat(), rng.choice(["office", "ER", "tele"])),
            )

        # Glucose labs: 1..6, some physiologic 0 (== missing) and after-index values.
        base = rng.gauss(120, 35)
        for _ in range(rng.randint(1, 6)):
            lab_id += 1
            val = 0.0 if rng.random() < 0.06 else max(40.0, rng.gauss(base, 20))
            when = idx - timedelta(days=rng.randint(-60, 1000))
            cur.execute(
                "INSERT INTO labs VALUES (?,?,?,?,?)",
                (f"L{lab_id:06d}", pid, "glucose", round(val, 1), when.isoformat()),
            )

    conn.commit()
    conn.close()


def ensure_db() -> None:
    if not os.path.exists(DB_PATH):
        seed_db()


# --------------------------------------------------------------------------- #
# Cohort loading (corrected, leakage-safe query)
# --------------------------------------------------------------------------- #
_COHORT_SQL = """
WITH dx AS (                                    -- dedup + point-in-time
    SELECT DISTINCT patient_id
    FROM conditions
    WHERE icd10_code LIKE :prefix AND diagnosed_at <= :idx
),
enc AS (
    SELECT patient_id, COUNT(DISTINCT encounter_id) AS n_encounters
    FROM encounters WHERE encounter_date <= :idx
    GROUP BY patient_id
),
lab AS (
    SELECT patient_id, AVG(NULLIF(value, 0)) AS avg_glucose   -- 0 == missing
    FROM labs WHERE lab_name = 'glucose' AND taken_at <= :idx
    GROUP BY patient_id
)
SELECT p.patient_id, p.dob, p.sex,
       lab.avg_glucose,
       COALESCE(enc.n_encounters, 0) AS n_encounters
FROM patients p
JOIN dx        ON dx.patient_id  = p.patient_id
LEFT JOIN enc  ON enc.patient_id = p.patient_id
LEFT JOIN lab  ON lab.patient_id = p.patient_id
WHERE p.deceased_date IS NULL
"""


def load_cohort(cohort_id: str, index_date: str = INDEX_DATE) -> pd.DataFrame:
    """Return the cohort feature frame. Unknown cohort_id -> empty frame (=> 404)."""
    if cohort_id not in COHORTS:
        return pd.DataFrame()
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql(
            _COHORT_SQL,
            conn,
            params={"prefix": COHORTS[cohort_id]["icd10_prefix"] + "%", "idx": index_date},
        )
    finally:
        conn.close()

    if df.empty:
        return df
    idx = pd.Timestamp(index_date)
    dob = pd.to_datetime(df["dob"])
    df["age"] = ((idx - dob).dt.days / 365.25).astype(int)   # date-accurate age
    return df[df["age"] >= 18].reset_index(drop=True)         # adult = >= 18
