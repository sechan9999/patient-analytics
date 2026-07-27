"""Seed the SQLite DB. Run once before starting the server: python seed.py"""
from app.data import seed_db, DB_PATH

if __name__ == "__main__":
    seed_db()
    print(f"Seeded synthetic clinical data -> {DB_PATH}")
