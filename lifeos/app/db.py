"""SQLite storage for LifeOS (Vault Flow + Body Ops)."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("LIFEOS_DB", "/data/lifeos.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    minutes INTEGER NOT NULL,
    protein_g REAL NOT NULL DEFAULT 0,
    calories REAL NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '',
    avoided INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meal_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    name TEXT NOT NULL,
    protein_g REAL NOT NULL DEFAULT 0,
    calories REAL NOT NULL DEFAULT 0,
    override_kind TEXT
);

CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    meal TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('sometimes','today'))
);

CREATE TABLE IF NOT EXISTS weighins (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    weight_lb REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    date TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vitamins (
    date TEXT PRIMARY KEY,
    taken INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    kind TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    calories REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    balance REAL NOT NULL DEFAULT 0,
    vaultborne INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    due_day INTEGER NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    paid_month TEXT
);

CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    amount REAL NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    source TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS nudges (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
"""

DEFAULT_SETTINGS = {
    "protein_target_g": "100",
    "step_target": "8000",
    "calorie_target": "2000",
}

SEED_MEALS = [
    # name, minutes, protein_g, calories, tags, avoided
    ("Sweet potato + grilled chicken", 15, 42, 480, "sweet_potato,preferred", 0),
    ("Sweet potato + eggs", 15, 24, 420, "sweet_potato,preferred", 0),
    ("Greek yogurt + berries", 5, 18, 220, "quick,snack", 0),
    ("Protein shake", 5, 30, 200, "quick,snack", 0),
    ("Tuna packet + crackers", 5, 25, 260, "quick", 0),
    ("Chicken + veggies stir-fry", 15, 40, 450, "", 0),
    ("Cottage cheese bowl", 5, 22, 210, "quick,snack", 0),
    ("Turkey wrap", 5, 28, 340, "quick", 0),
    ("Ground turkey + sweet potato hash", 15, 38, 470, "sweet_potato,preferred", 0),
    ("Rice bowl with chicken", 15, 35, 520, "rice", 1),
    ("Sandwich (bread)", 5, 20, 380, "bread", 1),
    ("Mashed potatoes + meatloaf", 15, 30, 550, "mashed_potatoes", 1),
]

SEED_ACCOUNTS = ["True Lion", "OnePay", "FreePlay", "Relay"]


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        for m in SEED_MEALS:
            c.execute(
                "INSERT OR IGNORE INTO meals(name,minutes,protein_g,calories,tags,avoided)"
                " VALUES(?,?,?,?,?,?)",
                m,
            )
        for a in SEED_ACCOUNTS:
            c.execute("INSERT OR IGNORE INTO accounts(name) VALUES(?)", (a,))


def get_setting(key: str) -> str:
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""


def set_setting(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
