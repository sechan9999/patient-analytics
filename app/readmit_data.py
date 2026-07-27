"""Synthetic heart-failure dataset + leakage-safe, patient-level readmission cohort.

Builds a ~140-patient HF DB (patients/diagnoses/admissions/labs) and returns ONE row per
patient with: age, sex, avg_bnp, n_prior_admissions, readmit_30d (label).
Features are point-in-time at the index discharge; the readmission label is the NEXT admission
within 30 days (label lives after discharge — deliberately not point-in-time capped).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from random import Random

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "readmit.db")

COHORTS = {"hf_adults": {"prefix": "I50", "label": "Heart failure, adults >=18"}}


def seed(path: str = DB_PATH, n: int = 140, seed_val: int = 7) -> None:
    rng = Random(seed_val)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript(
        """
        DROP TABLE IF EXISTS patients; DROP TABLE IF EXISTS diagnoses;
        DROP TABLE IF EXISTS admissions; DROP TABLE IF EXISTS labs;
        CREATE TABLE patients   (patient_id TEXT, birth_year INT, sex TEXT, deceased_date TEXT);
        CREATE TABLE diagnoses  (patient_id TEXT, icd10_code TEXT, diagnosed_at TEXT);
        CREATE TABLE admissions (admission_id TEXT, patient_id TEXT, admit_date TEXT, discharge_date TEXT);
        CREATE TABLE labs       (lab_id TEXT, patient_id TEXT, lab_name TEXT, value REAL, taken_at TEXT);
        """
    )
    aid = lid = 0
    for i in range(1, n + 1):
        pid = f"P{i:04d}"
        age = rng.randint(12, 92)
        birth_year = 2026 - age
        sex = rng.choice(["F", "M"])
        deceased = "2025-10-01" if rng.random() < 0.07 else None
        c.execute("INSERT INTO patients VALUES (?,?,?,?)", (pid, birth_year, sex, deceased))

        is_hf = rng.random() < 0.8
        c.execute("INSERT INTO diagnoses VALUES (?,?,?)",
                  (pid, "I50.9" if is_hf else "E11.9", "2024-02-01"))

        d0 = date(2025, 1, 1) + timedelta(days=rng.randint(0, 200))
        disch = d0 + timedelta(days=rng.randint(2, 12))
        aid += 1
        c.execute("INSERT INTO admissions VALUES (?,?,?,?)",
                  (f"A{aid:05d}", pid, d0.isoformat(), disch.isoformat()))

        bnp = max(0.0, rng.gauss(400 + (age - 50) * 6, 150))
        if rng.random() < 0.07:
            bnp = 0.0  # 0 == missing
        lid += 1
        c.execute("INSERT INTO labs VALUES (?,?,?,?,?)",
                  (f"L{lid:05d}", pid, "BNP", round(bnp, 1), d0.isoformat()))
        lid += 1
        c.execute("INSERT INTO labs VALUES (?,?,?,?,?)",       # decoy non-BNP lab
                  (f"L{lid:05d}", pid, "creatinine", round(rng.uniform(0.6, 1.8), 2), d0.isoformat()))

        n_prior = rng.choices([0, 1, 2, 3], weights=[5, 3, 2, 1])[0]
        for _ in range(n_prior):
            pd0 = d0 - timedelta(days=rng.randint(40, 700))
            aid += 1
            c.execute("INSERT INTO admissions VALUES (?,?,?,?)",
                      (f"A{aid:05d}", pid, pd0.isoformat(), (pd0 + timedelta(days=4)).isoformat()))

        p_readmit = min(0.85, 0.05 + (age > 65) * 0.15 + (bnp > 500) * 0.15 + n_prior * 0.08)
        if is_hf and deceased is None and rng.random() < p_readmit:
            r_admit = disch + timedelta(days=rng.randint(1, 29))
            aid += 1
            c.execute("INSERT INTO admissions VALUES (?,?,?,?)",
                      (f"A{aid:05d}", pid, r_admit.isoformat(), (r_admit + timedelta(days=5)).isoformat()))

    conn.commit()
    conn.close()


def ensure_db():
    if not os.path.exists(DB_PATH):
        seed()


def load_readmit_cohort(cohort_id: str) -> pd.DataFrame:
    """One leakage-safe row per patient: features + readmit_30d label."""
    if cohort_id not in COHORTS:
        return pd.DataFrame()
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        adm = pd.read_sql("SELECT admission_id, patient_id, admit_date, discharge_date FROM admissions", conn)
        pat = pd.read_sql("SELECT patient_id, birth_year, sex, deceased_date FROM patients", conn)
        hf = pd.read_sql(
            f"SELECT DISTINCT patient_id FROM diagnoses WHERE icd10_code LIKE '{COHORTS[cohort_id]['prefix']}%'", conn)
        bnp = pd.read_sql(
            "SELECT patient_id, AVG(NULLIF(value,0)) avg_bnp FROM labs WHERE lab_name='BNP' GROUP BY patient_id", conn)
    finally:
        conn.close()
    if adm.empty:
        return pd.DataFrame()

    pat = pat.merge(hf, on="patient_id", how="inner")
    pat = pat[pat["deceased_date"].isna()].copy()
    pat["age"] = 2026 - pat["birth_year"]
    pat = pat[pat["age"] >= 18].copy()

    adm["admit_date"] = pd.to_datetime(adm["admit_date"])
    adm["discharge_date"] = pd.to_datetime(adm["discharge_date"])
    adm = adm.sort_values(["patient_id", "admit_date"])
    adm["year"] = adm["admit_date"].dt.year

    idx = adm[adm["year"] == 2025].groupby("patient_id", as_index=False).first()
    idx = idx.rename(columns={"admit_date": "index_admit", "discharge_date": "index_disch"})

    nxt = adm.merge(idx[["patient_id", "index_disch"]], on="patient_id")
    nxt = nxt[nxt["admit_date"] > nxt["index_disch"]]
    gap = (nxt["admit_date"] - nxt["index_disch"]).dt.days
    readmit_ids = set(nxt.loc[gap.between(0, 30), "patient_id"])

    prior = adm.merge(idx[["patient_id", "index_admit"]], on="patient_id")
    prior = prior[prior["admit_date"] < prior["index_admit"]]
    n_prior = prior.groupby("patient_id").size().rename("n_prior_admissions")

    df = idx[["patient_id"]].merge(pat[["patient_id", "age", "sex"]], on="patient_id", how="inner")
    df = df.merge(bnp, on="patient_id", how="left")
    df = df.merge(n_prior, on="patient_id", how="left")
    df["n_prior_admissions"] = df["n_prior_admissions"].fillna(0).astype(int)
    df["readmit_30d"] = df["patient_id"].isin(readmit_ids).astype(int)
    return df.reset_index(drop=True)
