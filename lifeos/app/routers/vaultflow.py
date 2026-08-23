"""Vault Flow: accounts, deposits vs bills, payment recommendations, nudges."""
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn
from ..paydays import payday_schedule, scheduled_bill_due_date

router = APIRouter(prefix="/api/vault", tags=["vaultflow"])


class AccountIn(BaseModel):
    name: str
    balance: float = 0
    vaultborne: bool = False


class BalanceIn(BaseModel):
    balance: float


class BillIn(BaseModel):
    name: str
    amount: float
    due_day: int
    paycheck: int = 1
    account_id: int | None = None


class DepositIn(BaseModel):
    amount: float
    account_id: int
    source: str = ""


class DepositByNameIn(BaseModel):
    amount: float
    account: str
    source: str = "voice"


class BillPaidByNameIn(BaseModel):
    name: str


class SpendIn(BaseModel):
    amount: float
    merchant: str = ""


class GoalIn(BaseModel):
    name: str
    target: float = 0


class GoalContributeIn(BaseModel):
    name: str
    amount: float


def _month() -> str:
    return date.today().strftime("%Y-%m")


def _find_by_name(c, table: str, spoken: str):
    """Fuzzy name lookup for voice input: the stored name may contain the
    spoken phrase, or the spoken phrase may contain the stored name
    ("the electric bill" -> "Electric")."""
    spoken = spoken.strip()
    return c.execute(
        f"SELECT id, name FROM {table}"
        " WHERE name LIKE ? COLLATE NOCASE OR ? LIKE '%' || name || '%' COLLATE NOCASE"
        " ORDER BY LENGTH(name) LIMIT 1",
        (f"%{spoken}%", spoken),
    ).fetchone()


@router.get("/accounts")
def list_accounts():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]


@router.post("/accounts")
def add_account(body: AccountIn):
    if not body.name.strip():
        raise HTTPException(400, "account name is required")
    if body.balance < 0:
        raise HTTPException(400, "account balance cannot be negative")
    with conn() as c:
        c.execute(
            "INSERT INTO accounts(name,balance,vaultborne) VALUES(?,?,?)",
            (body.name.strip(), body.balance, int(body.vaultborne)),
        )
        return {"ok": True}


@router.put("/accounts/{account_id}/balance")
def set_balance(account_id: int, body: BalanceIn):
    if body.balance < 0:
        raise HTTPException(400, "account balance cannot be negative")
    with conn() as c:
        cur = c.execute(
            "UPDATE accounts SET balance=? WHERE id=?", (body.balance, account_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "account not found")
        return {"ok": True}


@router.get("/bills")
def list_bills():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM bills ORDER BY"
                " CASE paycheck WHEN 1 THEN 0 WHEN 2 THEN 1 ELSE 2 END,"
                " due_day, name"
            ).fetchall()
        ]


@router.post("/bills")
def add_bill(body: BillIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "bill name is required")
    if body.amount <= 0:
        raise HTTPException(400, "bill amount must be positive")
    if not 1 <= body.due_day <= 31:
        raise HTTPException(400, "due day must be between 1 and 31")
    if body.paycheck not in (1, 2):
        raise HTTPException(400, "paycheck must be 1 or 2")
    with conn() as c:
        if body.account_id is not None:
            account = c.execute(
                "SELECT id FROM accounts WHERE id=?", (body.account_id,)
            ).fetchone()
            if account is None:
                raise HTTPException(404, "account not found")
        c.execute(
            "INSERT INTO bills(name,amount,due_day,paycheck,account_id)"
            " VALUES(?,?,?,?,?)",
            (name, body.amount, body.due_day, body.paycheck, body.account_id),
        )
        return {"ok": True}


@router.post("/bills/{bill_id}/paid")
def mark_paid(bill_id: int):
    with conn() as c:
        cur = c.execute(
            "UPDATE bills SET paid_month=? WHERE id=?", (_month(), bill_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "bill not found")
        return {"ok": True}


@router.post("/deposits")
def add_deposit(body: DepositIn):
    if body.amount <= 0:
        raise HTTPException(400, "deposit amount must be positive")
    with conn() as c:
        account = c.execute(
            "SELECT id FROM accounts WHERE id=?", (body.account_id,)
        ).fetchone()
        if account is None:
            raise HTTPException(404, "account not found")
        c.execute(
            "INSERT INTO deposits(amount,account_id,source) VALUES(?,?,?)",
            (body.amount, body.account_id, body.source.strip()),
        )
        c.execute(
            "UPDATE accounts SET balance=ROUND(balance+?,2) WHERE id=?",
            (body.amount, body.account_id),
        )
        return {"ok": True}


@router.post("/deposits/by-name")
def add_deposit_by_name(body: DepositByNameIn):
    """Voice-friendly deposit: matches the account by (partial) name."""
    if body.amount <= 0:
        raise HTTPException(400, "deposit amount must be positive")
    account_name = body.account.strip()
    if not account_name:
        raise HTTPException(400, "account name is required")
    with conn() as c:
        row = _find_by_name(c, "accounts", account_name)
        if row is None:
            raise HTTPException(404, f"no account matching '{account_name}'")
        c.execute(
            "INSERT INTO deposits(amount,account_id,source) VALUES(?,?,?)",
            (body.amount, row["id"], body.source.strip()),
        )
        c.execute(
            "UPDATE accounts SET balance=ROUND(balance+?,2) WHERE id=?",
            (body.amount, row["id"]),
        )
        return {"ok": True, "account": row["name"]}


@router.post("/bills/paid/by-name")
def mark_paid_by_name(body: BillPaidByNameIn):
    """Voice-friendly bill payment: matches the bill by (partial) name."""
    with conn() as c:
        row = _find_by_name(c, "bills", body.name)
        if row is None:
            raise HTTPException(404, f"no bill matching '{body.name}'")
        c.execute(
            "UPDATE bills SET paid_month=? WHERE id=?", (_month(), row["id"])
        )
        return {"ok": True, "bill": row["name"]}


@router.post("/spending")
def log_spending(body: SpendIn):
    if body.amount <= 0:
        raise HTTPException(400, "spending amount must be positive")
    with conn() as c:
        c.execute(
            "INSERT INTO spending(amount,merchant) VALUES(?,?)",
            (round(body.amount, 2), body.merchant.strip()),
        )
        return {"ok": True, "week_total": week_spending(c)}


@router.get("/spending")
def list_spending():
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    with conn() as c:
        entries = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM spending WHERE date(ts)>=? ORDER BY ts DESC",
                (week_ago,),
            ).fetchall()
        ]
        return {
            "week_total": week_spending(c),
            "month_total": month_spending(c),
            "entries": entries,
        }


def week_spending(c) -> float:
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    row = c.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM spending WHERE date(ts)>=?",
        (week_ago,),
    ).fetchone()
    return row["s"]


def month_spending(c) -> float:
    month_start = date.today().replace(day=1).isoformat()
    row = c.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM spending WHERE date(ts)>=?",
        (month_start,),
    ).fetchone()
    return row["s"]


@router.get("/goals")
def list_goals():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM savings_goals ORDER BY name").fetchall()
        ]


@router.post("/goals")
def add_goal(body: GoalIn):
    """'Create a savings goal called vacation for 1000 dollars.'"""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "goal name is required")
    if body.target < 0:
        raise HTTPException(400, "goal target cannot be negative")
    with conn() as c:
        c.execute(
            "INSERT INTO savings_goals(name,target) VALUES(?,?)"
            " ON CONFLICT(name) DO UPDATE SET target=excluded.target",
            (name, body.target),
        )
        return {"ok": True, "goal": name}


@router.post("/goals/contribute")
def contribute_goal(body: GoalContributeIn):
    """'Add 50 to my vacation fund' — fuzzy-matches the goal by name and
    creates it on the fly if it doesn't exist yet."""
    if body.amount <= 0:
        raise HTTPException(400, "goal contribution must be positive")
    goal_name = body.name.strip()
    if not goal_name:
        raise HTTPException(400, "goal name is required")
    with conn() as c:
        row = _find_by_name(c, "savings_goals", goal_name)
        if row is None:
            c.execute(
                "INSERT INTO savings_goals(name,saved) VALUES(?,?)",
                (goal_name, body.amount),
            )
            name = goal_name
            saved = body.amount
        else:
            c.execute(
                "UPDATE savings_goals SET saved=ROUND(saved+?,2) WHERE id=?",
                (body.amount, row["id"]),
            )
            name = row["name"]
            saved = round(row["saved"] + body.amount, 2)
        return {"ok": True, "goal": name, "saved": saved}


@router.get("/plan")
def plan():
    """Line deposits/balances against unpaid bills; recommend payments;
    show leftover discretionary balance."""
    today = date.today()
    paydays = payday_schedule(today, 4)
    next_by_paycheck = {
        paycheck: next(item for item in paydays if item["paycheck"] == paycheck)
        for paycheck in (1, 2)
    }
    with conn() as c:
        accounts = [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]
        bills = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM bills WHERE paid_month IS NULL OR paid_month<>?",
                (_month(),),
            ).fetchall()
        ]
        total_available = sum(a["balance"] for a in accounts)
        bills.sort(key=lambda b: (b["paycheck"], b["due_day"], b["name"]))
        recommendations = []
        for b in bills:
            paycheck = b["paycheck"] if b["paycheck"] in (1, 2) else 1
            payday = next_by_paycheck[paycheck]
            due_date = scheduled_bill_due_date(paycheck, b["due_day"], today)
            recommendations.append(
                {
                    "bill": b["name"],
                    "amount": b["amount"],
                    "due_day": b["due_day"],
                    "due_date": due_date.isoformat(),
                    "status": f"scheduled from Paycheck {paycheck}",
                    "recommend": f"Paycheck {paycheck}",
                    "payday": payday["date"],
                }
            )
        food_nudges = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM nudges WHERE kind='food_override' AND resolved=0"
                " ORDER BY ts DESC LIMIT 5"
            ).fetchall()
        ]
        return {
            "total_available": total_available,
            "unpaid_bills_total": sum(b["amount"] for b in bills),
            "recommendations": recommendations,
            "leftover_after_bills": total_available,
            "cycle_starts": next_by_paycheck[1]["date"],
            "food_nudges": food_nudges,
            "note": "Vaultborne accounts stay separate from discretionary flows.",
        }


@router.post("/nudges/{nudge_id}/resolve")
def resolve_nudge(nudge_id: int):
    with conn() as c:
        c.execute("UPDATE nudges SET resolved=1 WHERE id=?", (nudge_id,))
        return {"ok": True}
