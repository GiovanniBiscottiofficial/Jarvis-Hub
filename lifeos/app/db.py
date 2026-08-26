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

CREATE TABLE IF NOT EXISTS meal_photos (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'analyzing',
    meal_name TEXT NOT NULL DEFAULT '',
    foods_json TEXT NOT NULL DEFAULT '[]',
    protein_g REAL,
    calories REAL,
    confidence TEXT NOT NULL DEFAULT 'low',
    notes TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
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
    profile_id INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'manual'
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
    grocy_product_id INTEGER,
    category TEXT NOT NULL DEFAULT 'other',
    low_stock_threshold REAL NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_depleted_at TEXT
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
    start_period TEXT,
    one_time INTEGER NOT NULL DEFAULT 0,
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
    per_paycheck REAL NOT NULL DEFAULT 0,
    ytd_contributions REAL NOT NULL DEFAULT 0,
    lifetime_contributions REAL NOT NULL DEFAULT 0,
    as_of TEXT
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
    done INTEGER NOT NULL DEFAULT 0,
    qty REAL NOT NULL DEFAULT 1,
    unit TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    reason TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT 'Other',
    estimated_price REAL,
    recipe_id TEXT
);

CREATE TABLE IF NOT EXISTS financial_transactions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    posted_date TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    direction TEXT NOT NULL CHECK (direction IN ('debit','credit')),
    amount REAL NOT NULL CHECK (amount > 0),
    merchant TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Uncategorized',
    source TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','verified','matched','excluded')),
    matched_spending_id INTEGER REFERENCES spending(id),
    reviewed_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    apr REAL NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 50,
    priority_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_financial_transactions_status_date
    ON financial_transactions(status, posted_date DESC, id DESC);

CREATE TABLE IF NOT EXISTS account_reconciliations (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    previous_balance REAL NOT NULL,
    actual_balance REAL NOT NULL,
    difference REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    confirmed_by TEXT NOT NULL DEFAULT 'Giovanni'
);

CREATE TABLE IF NOT EXISTS paycheck_cycles (
    period TEXT PRIMARY KEY,
    paycheck INTEGER NOT NULL CHECK (paycheck IN (1,2)),
    payday TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','funded','closed')),
    account_id INTEGER REFERENCES accounts(id),
    amount REAL NOT NULL DEFAULT 0,
    opening_balance REAL,
    closing_balance REAL,
    funded_at TEXT,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS financial_action_audit (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    action TEXT NOT NULL,
    risk TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    subject TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chef_feedback (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    recipe_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('liked','cooked','skipped')),
    profile_id INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_chef_feedback_recipe
    ON chef_feedback(profile_id, recipe_id, ts DESC);

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

CREATE TABLE IF NOT EXISTS body_checkins (
    date TEXT NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    sleep_hours REAL,
    sleep_quality INTEGER,
    energy INTEGER,
    mood INTEGER,
    soreness INTEGER,
    resting_heart_rate REAL,
    source TEXT NOT NULL DEFAULT 'manual',
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (date, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_body_checkins_profile_date
    ON body_checkins(profile_id, date DESC);

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

CREATE TABLE IF NOT EXISTS context_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT,
    state TEXT,
    previous_state TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_context_events_ts
    ON context_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_context_events_entity
    ON context_events(entity_id, ts DESC);

CREATE TABLE IF NOT EXISTS context_facts (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS action_proposals (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    behavior TEXT NOT NULL,
    action_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    reason TEXT NOT NULL,
    risk TEXT NOT NULL,
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    context_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT,
    executed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_proposals_status
    ON action_proposals(status, created_at DESC);

CREATE TABLE IF NOT EXISTS action_audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    action_id TEXT NOT NULL,
    proposal_id INTEGER REFERENCES action_proposals(id),
    requested_by TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
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
    "net_per_paycheck": "2064.24",
    "split_onepay": "1755.24",
    "split_truliant": "309.00",
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

# name, amount, due_day, paycheck (1 = month-end check, 2 = mid-month),
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
    ("Spectrum Internet", 93.95, 28, 1,
     "recurring apartment internet · due on the 28th", 0),
    ("Phone Reconnection", 216.75, 1, 1, "one-time reconnection on third upcoming pay", 0),
    ("Klarna Statement", 61.77, 28, 1, "starts with third upcoming pay", 0),
    ("Old Spectrum Paydown", 39.00, 28, 1, "$39 per check · starts with third upcoming pay", 0),
    ("Duke Energy Payment Plan", 65.50, 28, 1,
     "$65.50 installment · 4 scheduled payments", 0),
    ("Apartment Water Activation", 90.00, 28, 1,
     "one-time water turn-on · first upcoming pay", 0),
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
            ("start_period", "TEXT"),
            ("one_time", "INTEGER NOT NULL DEFAULT 0"),
            ("note", "TEXT NOT NULL DEFAULT ''"),
        ],
        "savings_goals": [("monthly", "REAL NOT NULL DEFAULT 0")],
        "weighins": [("source", "TEXT NOT NULL DEFAULT 'manual'")],
        "debts": [
            ("apr", "REAL NOT NULL DEFAULT 0"),
            ("priority", "INTEGER NOT NULL DEFAULT 50"),
            ("priority_reason", "TEXT NOT NULL DEFAULT ''"),
        ],
        "assets": [
            ("ytd_contributions", "REAL NOT NULL DEFAULT 0"),
            ("lifetime_contributions", "REAL NOT NULL DEFAULT 0"),
            ("as_of", "TEXT"),
        ],
        "pantry": [
            ("category", "TEXT NOT NULL DEFAULT 'other'"),
            ("low_stock_threshold", "REAL NOT NULL DEFAULT 1"),
            ("updated_at", "TEXT"),
            ("last_depleted_at", "TEXT"),
        ],
        "grocery_list": [
            ("qty", "REAL NOT NULL DEFAULT 1"),
            ("unit", "TEXT NOT NULL DEFAULT ''"),
            ("source", "TEXT NOT NULL DEFAULT 'manual'"),
            ("reason", "TEXT NOT NULL DEFAULT ''"),
            ("department", "TEXT NOT NULL DEFAULT 'Other'"),
            ("estimated_price", "REAL"),
            ("recipe_id", "TEXT"),
        ],
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
        # OnePay Savings was a planning placeholder, not a real account. Remove
        # only an untouched zero-balance row so genuine history is never lost.
        c.execute(
            "DELETE FROM accounts WHERE name='OnePay Savings' AND balance=0"
            " AND NOT EXISTS (SELECT 1 FROM deposits WHERE account_id=accounts.id)"
            " AND NOT EXISTS (SELECT 1 FROM bills WHERE account_id=accounts.id)"
            " AND NOT EXISTS (SELECT 1 FROM financial_transactions"
            " WHERE account_id=accounts.id)"
            " AND NOT EXISTS (SELECT 1 FROM account_reconciliations"
            " WHERE account_id=accounts.id)"
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
        # Create every current table before migrations inspect or cross-reference
        # them. CREATE IF NOT EXISTS preserves the live database while allowing
        # an older installation to gain newly introduced tables safely.
        c.executescript(SCHEMA)
        _migrate(c)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        if c.execute(
            "SELECT 1 FROM settings WHERE key='budget_cycle_start'"
        ).fetchone() is None:
            from .paydays import payday_schedule

            first_pay = next(
                item for item in payday_schedule(date.today(), 4)
                if item["paycheck"] == 1
            )
            c.execute(
                "INSERT INTO settings(key,value) VALUES('budget_cycle_start',?)",
                (first_pay["date"],),
            )
        # Correct the legacy one-cent payroll default without overwriting a
        # future user-customized value.
        c.execute(
            "UPDATE settings SET value='2064.24'"
            " WHERE key='net_per_paycheck' AND value='2064.25'"
        )
        c.execute(
            "UPDATE settings SET value='1754.60'"
            " WHERE key='split_onepay' AND value='1754.61'"
        )
        if c.execute(
            "SELECT 1 FROM settings WHERE key='paycheck_split_fixed_309_v1'"
        ).fetchone() is None:
            c.execute("UPDATE settings SET value='1755.24' WHERE key='split_onepay'")
            c.execute("UPDATE settings SET value='309.00' WHERE key='split_truliant'")
            c.execute(
                "INSERT INTO settings(key,value) VALUES('paycheck_split_fixed_309_v1',?)",
                (date.today().isoformat(),),
            )
        c.execute(
            "UPDATE bills SET name='Duke Energy Payment Plan',"
            " note='$65.50 installment · 4 scheduled payments'"
            " WHERE name='Duke Energy (past due)'"
        )
        for m in SEED_MEALS:
            c.execute(
                "INSERT OR IGNORE INTO meals(name,minutes,protein_g,calories,tags,avoided)"
                " VALUES(?,?,?,?,?,?)",
                m,
        )
        for name, role, baseline in SEED_ACCOUNTS:
            inserted = c.execute(
                "INSERT OR IGNORE INTO accounts(name,role) VALUES(?,?)",
                (name, role),
            )
            c.execute("UPDATE accounts SET role=? WHERE name=?", (role, name))
            if inserted.rowcount:
                c.execute(
                    "UPDATE accounts SET balance=? WHERE name=?",
                    (baseline, name),
                )
        if c.execute(
            "SELECT 1 FROM settings WHERE key='finance_commissioning_v3'"
        ).fetchone() is None:
            c.execute("UPDATE accounts SET balance=0 WHERE name='Relay'")
            c.execute(
                "INSERT INTO settings(key,value)"
                " VALUES('finance_commissioning_v3',?)",
                (date.today().isoformat(),),
            )
        # Bills and debts seed only into an empty table: they are live
        # ledger data, so a row the user deleted must stay deleted across
        # restarts instead of being resurrected by the seed list.
        _today = date.today()
        if c.execute("SELECT COUNT(*) AS n FROM bills").fetchone()["n"] == 0:
            for name, amount, due_day, paycheck, note, paid in SEED_BILLS:
                paid_period = f"{_today:%Y-%m}-P{paycheck}" if paid else None
                c.execute(
                    "INSERT INTO bills(name,amount,due_day,paycheck,note,"
                    "paid_period) VALUES(?,?,?,?,?,?)",
                    (name, amount, due_day, paycheck, note, paid_period),
                )
        if c.execute(
            "SELECT 1 FROM settings WHERE key='finance_commissioning_v2'"
        ).fetchone() is None:
            from .paydays import payday_schedule

            schedule = payday_schedule(date.today(), 3)
            first_period = schedule[0]["period"]
            third_period = schedule[2]["period"]
            c.execute(
                "UPDATE bills SET amount=93.95,due_day=28,paycheck=1,"
                " start_period=?,paid_month=NULL,paid_period=NULL,"
                " note='recurring apartment internet · due on the 28th'"
                " WHERE name='Spectrum Internet'",
                (first_period,),
            )
            c.execute(
                "UPDATE bills SET due_day=28,paycheck=1,start_period=?"
                " WHERE name IN ('Klarna Statement','Duke Energy Payment Plan',"
                " 'Old Spectrum Paydown')",
                (third_period,),
            )
            c.execute(
                "UPDATE bills SET note='starts with third upcoming pay'"
                " WHERE name='Klarna Statement'"
            )
            c.execute(
                "UPDATE bills SET note='$39 per check · starts with third upcoming pay'"
                " WHERE name='Old Spectrum Paydown'"
            )
            c.execute(
                "INSERT INTO bills(name,amount,due_day,paycheck,start_period,note)"
                " SELECT 'Apartment Water Activation',90,28,1,?,"
                " 'one-time water turn-on · first upcoming pay'"
                " WHERE NOT EXISTS (SELECT 1 FROM bills"
                " WHERE name='Apartment Water Activation')",
                (first_period,),
            )
            c.execute(
                "UPDATE bills SET amount=90,due_day=28,paycheck=1,start_period=?,one_time=1,"
                " note='one-time water turn-on · first upcoming pay'"
                " WHERE name='Apartment Water Activation'",
                (first_period,),
            )
            c.execute("UPDATE accounts SET balance=-25.73 WHERE name='Truliant'")
            c.execute("UPDATE accounts SET balance=0 WHERE name='Relay'")
            c.execute(
                "INSERT INTO settings(key,value)"
                " VALUES('finance_commissioning_v2',?)",
                (date.today().isoformat(),),
            )
        if c.execute(
            "SELECT 1 FROM settings WHERE key='finance_commissioning_v4'"
        ).fetchone() is None:
            c.execute(
                "UPDATE bills SET one_time=1"
                " WHERE name='Apartment Water Activation'"
            )
            c.execute(
                "INSERT INTO settings(key,value)"
                " VALUES('finance_commissioning_v4',?)",
                (date.today().isoformat(),),
            )
        if c.execute(
            "SELECT 1 FROM settings WHERE key='finance_phone_schedule_v1'"
        ).fetchone() is None:
            from .paydays import payday_schedule

            third_pay = payday_schedule(date.today(), 3)[2]
            # Keep one live Reconnection row and preserve its current amount.
            # It belongs only to the third upcoming paycheck, never every P1.
            c.execute(
                "DELETE FROM bills WHERE name='Phone Reconnection'"
                " AND id<>(SELECT MIN(id) FROM bills WHERE name='Phone Reconnection')"
            )
            c.execute(
                "UPDATE bills SET paycheck=?,start_period=?,one_time=1,"
                " paid_month=NULL,paid_period=NULL,"
                " note='one-time reconnection on third upcoming pay'"
                " WHERE name='Phone Reconnection'",
                (third_pay["paycheck"], third_pay["period"]),
            )
            c.execute("DELETE FROM bills WHERE name='Phone Balance Arrangement'")
            c.execute(
                "INSERT INTO settings(key,value) VALUES('finance_phone_schedule_v1',?)",
                (date.today().isoformat(),),
            )
        if c.execute("SELECT COUNT(*) AS n FROM debts").fetchone()["n"] == 0:
            for d in SEED_DEBTS:
                c.execute(
                    "INSERT INTO debts(name,total,remaining,installment,"
                    "cadence,note) VALUES(?,?,?,?,?,?)",
                    d,
                )
        if c.execute(
            "SELECT 1 FROM settings WHERE key='debt_priority_v1'"
        ).fetchone() is None:
            priorities = (
                (10, "Essential electric service continuity", "Duke Energy Past Due"),
                (20, "Transportation and income continuity", "Car Note Temp Fee"),
                (30, "Communications account arrangement", "Phone Balance"),
                (40, "Legacy utility balance and collections risk", "Old Spectrum"),
                (50, "Consumer installment debt", "Klarna"),
            )
            for priority, reason, name in priorities:
                c.execute(
                    "UPDATE debts SET priority=?,priority_reason=? WHERE name=?",
                    (priority, reason, name),
                )
            c.execute(
                "INSERT INTO settings(key,value) VALUES('debt_priority_v1',?)",
                (date.today().isoformat(),),
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
        if c.execute(
            "SELECT 1 FROM settings WHERE key='retirement_totals_2026_08_25_v1'"
        ).fetchone() is None:
            retirement_totals = (
                (527.45, 491.64, 491.64, "401(k)"),
                (419.29, 393.32, 393.32, "Roth IRA"),
            )
            for balance, ytd, lifetime, name in retirement_totals:
                c.execute(
                    "UPDATE assets SET balance=?,ytd_contributions=?,"
                    " lifetime_contributions=?,as_of='2026-08-25' WHERE name=?",
                    (balance, ytd, lifetime, name),
                )
            c.execute(
                "INSERT INTO settings(key,value)"
                " VALUES('retirement_totals_2026_08_25_v1','2026-08-25')"
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
