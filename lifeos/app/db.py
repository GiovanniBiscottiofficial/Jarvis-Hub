"""SQLite storage for LifeOS (Vault Flow + Body Ops)."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import date

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
    override_kind TEXT,
    profile_id INTEGER NOT NULL DEFAULT 1
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
    weight_lb REAL NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS steps (
    date TEXT NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, profile_id)
);

CREATE TABLE IF NOT EXISTS vitamins (
    date TEXT NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    taken INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, profile_id)
);

CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    kind TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    calories REAL NOT NULL DEFAULT 0,
    profile_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workout_plan (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 15,
    done INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    profile_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    protein_target_g REAL NOT NULL DEFAULT 100,
    step_target INTEGER NOT NULL DEFAULT 8000,
    calorie_target INTEGER NOT NULL DEFAULT 2000
);

CREATE TABLE IF NOT EXISTS pantry (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    qty REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT '',
    protein_g_per_serving REAL NOT NULL DEFAULT 0,
    grocy_product_id INTEGER
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    balance REAL NOT NULL DEFAULT 0,
    vaultborne INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    due_day INTEGER NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    paid_month TEXT,
    paycheck INTEGER NOT NULL DEFAULT 0,
    paid_period TEXT,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    total REAL NOT NULL DEFAULT 0,
    remaining REAL NOT NULL DEFAULT 0,
    installment REAL NOT NULL DEFAULT 0,
    cadence TEXT NOT NULL DEFAULT 'per paycheck',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'retirement',
    balance REAL NOT NULL DEFAULT 0,
    per_paycheck REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    amount REAL NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    source TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS spending (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    amount REAL NOT NULL,
    merchant TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS grocery_list (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    item TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    received_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    fact TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS favorites (
    slot TEXT PRIMARY KEY,
    meal_name TEXT NOT NULL,
    protein_g REAL NOT NULL DEFAULT 0,
    calories REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS water (
    date TEXT NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    glasses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, profile_id)
);

CREATE TABLE IF NOT EXISTS savings_goals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    target REAL NOT NULL DEFAULT 0,
    saved REAL NOT NULL DEFAULT 0,
    monthly REAL NOT NULL DEFAULT 0
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
    "water_target_glasses": "8",
    "active_profile": "1",
    # Semi-monthly paycheck profile (per paycheck, two checks a month)
    "gross_annual_salary": "58992.00",
    "net_per_paycheck": "2064.25",
    "split_onepay": "1754.61",
    "split_truliant": "309.64",
    "deduct_roth": "24.59",
    "deduct_401k": "24.59",
    "deduct_hsa": "15.98",
}

SEED_PROFILES = ["Giovanni"]

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

# name, role, baseline balance (applied only while the balance is still 0)
SEED_ACCOUNTS = [
    ("OnePay", "operating", 113.00),
    ("Truliant", "savings", 147.96),
    ("Relay", "buckets", 311.68),
]

# name, amount, due_day, paycheck (1 = 1st-of-month check, 2 = mid-month),
# note, already paid this cycle
SEED_BILLS = [
    ("Flex Pay Rent (1st half)", 360.24, 1, 1, "includes Flex fee", 0),
    ("Flex Pay Rent (2nd half)", 515.00, 15, 2, "includes $15.00 fee", 0),
    ("Car Note (check 1)", 307.00, 1, 1, "$195 base + $112 temporary charge", 0),
    ("Car Note (check 2)", 307.00, 15, 2, "$195 base + $112 temporary charge", 0),
    ("Gas / Fuel (check 1)", 80.00, 1, 1, "fill-up budget", 0),
    ("Gas / Fuel (check 2)", 80.00, 15, 2, "fill-up budget", 0),
    ("Car Insurance Catchup", 213.00, 1, 1, "late catchup payment", 1),
    ("Car Insurance (monthly)", 105.00, 15, 2, "regular monthly bill", 0),
    ("Spectrum Internet", 100.00, 15, 2, "$100 with activation, $80/mo after", 0),
    ("Phone Reconnection", 216.75, 1, 1, "restores service ($480.84 balance)", 1),
    ("Phone Balance Arrangement", 66.02, 15, 2, "bi-weekly installment of $264.09", 0),
    ("Klarna Statement", 61.77, 1, 1, "statement paydown", 1),
    ("Old Spectrum Paydown", 39.00, 15, 2, "$39 per check", 0),
    ("Duke Energy (past due)", 65.50, 15, 2, "queued — $65.50/paycheck planned", 0),
]

# name, total, remaining, installment, cadence, note
SEED_DEBTS = [
    ("Klarna", 230.75, 230.75, 61.77, "per statement",
     "Codeium x2, Steam $39.26, Sheetz $36.82, Speedway $19.75, DTLR $13.60"),
    ("Phone Balance", 480.84, 264.09, 66.02, "bi-weekly",
     "payment arrangement after $216.75 reconnection"),
    ("Old Spectrum", 78.00, 78.00, 39.00, "per paycheck", "old account paydown"),
    ("Car Insurance Catchup", 213.00, 213.00, 213.00, "one-time",
     "late catchup on paycheck #1"),
]

# name, target, saved, monthly contribution
SEED_GOALS = [
    ("3-Month Emergency Safety Net", 3540.00, 100.00, 50.00),
    ("Car Maintenance & Tires", 1200.00, 20.00, 30.00),
    ("Travel & Vacation Fund", 5000.00, 15.00, 50.00),
    ("Passport", 165.00, 15.00, 10.00),
    ("Date Nights Fund", 0, 0, 30.00),
    ("Baltimore Business Trip", 0, 0, 40.00),
    ("Birthday Fund (7/20)", 0, 0, 30.00),
    ("Household Supplies & Personals", 0, 0, 30.00),
    ("Wardrobe Fund", 0, 0, 30.00),
]

# name, kind, per-paycheck contribution
SEED_ASSETS = [
    ("Roth IRA", "retirement", 24.59),
    ("401(k)", "retirement", 24.59),
    ("HSA", "health", 15.98),
]


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


def _migrate(c: sqlite3.Connection) -> None:
    """Upgrade an older database in place."""
    # v0.3 budget columns
    simple_adds = {
        "accounts": [("role", "TEXT NOT NULL DEFAULT ''")],
        "bills": [
            ("paycheck", "INTEGER NOT NULL DEFAULT 0"),
            ("paid_period", "TEXT"),
            ("note", "TEXT NOT NULL DEFAULT ''"),
        ],
        "savings_goals": [("monthly", "REAL NOT NULL DEFAULT 0")],
    }
    for table, adds in simple_adds.items():
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        for col, decl in adds:
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # the savings split account is Truliant (early seeds called it True Lion)
    if {r["name"] for r in c.execute("PRAGMA table_info(accounts)")}:
        c.execute(
            "UPDATE accounts SET name='Truliant' WHERE name='True Lion'"
            " AND NOT EXISTS (SELECT 1 FROM accounts WHERE name='Truliant')"
        )
        # FreePlay isn't part of the 3-account money flow; drop it if unused
        c.execute(
            "DELETE FROM accounts WHERE name='FreePlay' AND balance=0"
            " AND NOT EXISTS (SELECT 1 FROM deposits WHERE account_id=accounts.id)"
            " AND NOT EXISTS (SELECT 1 FROM bills WHERE account_id=accounts.id)"
        )
    for table in ("meal_log", "weighins", "workouts"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if cols and "profile_id" not in cols:
            c.execute(
                f"ALTER TABLE {table} ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1"
            )
    for table in ("steps", "vitamins"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if cols and "profile_id" not in cols:
            value_col = "count" if table == "steps" else "taken"
            c.execute(f"ALTER TABLE {table} RENAME TO {table}_v01")
            c.execute(
                f"""CREATE TABLE {table} (
                    date TEXT NOT NULL,
                    profile_id INTEGER NOT NULL DEFAULT 1,
                    {value_col} INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (date, profile_id)
                )"""
            )
            c.execute(
                f"INSERT INTO {table}(date,profile_id,{value_col})"
                f" SELECT date,1,{value_col} FROM {table}_v01"
            )
            c.execute(f"DROP TABLE {table}_v01")


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with conn() as c:
        _migrate(c)
        c.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        for m in SEED_MEALS:
            c.execute(
                "INSERT OR IGNORE INTO meals(name,minutes,protein_g,calories,tags,avoided)"
                " VALUES(?,?,?,?,?,?)",
                m,
            )
        for name, role, baseline in SEED_ACCOUNTS:
            c.execute(
                "INSERT OR IGNORE INTO accounts(name,role) VALUES(?,?)",
                (name, role),
            )
            c.execute("UPDATE accounts SET role=? WHERE name=?", (role, name))
            c.execute(
                "UPDATE accounts SET balance=? WHERE name=? AND balance=0",
                (baseline, name),
            )
        _today = date.today()
        _period = f"{_today:%Y-%m}-P{1 if _today.day < 15 else 2}"
        for name, amount, due_day, paycheck, note, paid in SEED_BILLS:
            paid_period = _period if paid and paycheck == 1 else None
            c.execute(
                "INSERT INTO bills(name,amount,due_day,paycheck,note,paid_period)"
                " SELECT ?,?,?,?,?,?"
                " WHERE NOT EXISTS (SELECT 1 FROM bills WHERE name=?)",
                (name, amount, due_day, paycheck, note, paid_period, name),
            )
        for d in SEED_DEBTS:
            c.execute(
                "INSERT OR IGNORE INTO debts(name,total,remaining,installment,"
                "cadence,note) VALUES(?,?,?,?,?,?)",
                d,
            )
        for name, target, saved, monthly in SEED_GOALS:
            c.execute(
                "INSERT OR IGNORE INTO savings_goals(name,target,saved,monthly)"
                " VALUES(?,?,?,?)",
                (name, target, saved, monthly),
            )
            c.execute(
                "UPDATE savings_goals SET monthly=? WHERE name=? AND monthly=0",
                (monthly, name),
            )
        for a in SEED_ASSETS:
            c.execute(
                "INSERT OR IGNORE INTO assets(name,kind,per_paycheck)"
                " VALUES(?,?,?)",
                a,
            )
        # correct the old seed name on existing databases (before seeding,
        # so the rename never collides with a freshly inserted 'Giovanni')
        c.execute(
            "UPDATE profiles SET name='Giovanni' WHERE name='Keona'"
            " AND NOT EXISTS (SELECT 1 FROM profiles WHERE name='Giovanni')"
        )
        for p in SEED_PROFILES:
            c.execute("INSERT OR IGNORE INTO profiles(name) VALUES(?)", (p,))


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


def active_profile(c) -> sqlite3.Row:
    row = c.execute(
        "SELECT p.* FROM profiles p JOIN settings s ON s.key='active_profile'"
        " AND p.id=CAST(s.value AS INTEGER)"
    ).fetchone()
    if row is None:
        row = c.execute("SELECT * FROM profiles ORDER BY id LIMIT 1").fetchone()
    return row
