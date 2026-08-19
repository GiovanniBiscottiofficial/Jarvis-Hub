"""Vault Flow: accounts, deposits vs bills, payment recommendations, nudges."""
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn

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
    with conn() as c:
        c.execute(
            "INSERT INTO accounts(name,balance,vaultborne) VALUES(?,?,?)",
            (body.name, body.balance, int(body.vaultborne)),
        )
        return {"ok": True}


@router.put("/accounts/{account_id}/balance")
def set_balance(account_id: int, body: BalanceIn):
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
        return [dict(r) for r in c.execute("SELECT * FROM bills").fetchall()]


@router.post("/bills")
def add_bill(body: BillIn):
    with conn() as c:
        c.execute(
            "INSERT INTO bills(name,amount,due_day,account_id) VALUES(?,?,?,?)",
            (body.name, body.amount, body.due_day, body.account_id),
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
    with conn() as c:
        c.execute(
            "INSERT INTO deposits(amount,account_id,source) VALUES(?,?,?)",
            (body.amount, body.account_id, body.source),
        )
        c.execute(
            "UPDATE accounts SET balance=balance+? WHERE id=?",
            (body.amount, body.account_id),
        )
        return {"ok": True}


@router.post("/deposits/by-name")
def add_deposit_by_name(body: DepositByNameIn):
    """Voice-friendly deposit: matches the account by (partial) name."""
    with conn() as c:
        row = _find_by_name(c, "accounts", body.account)
        if row is None:
            raise HTTPException(404, f"no account matching '{body.account}'")
        c.execute(
            "INSERT INTO deposits(amount,account_id,source) VALUES(?,?,?)",
            (body.amount, row["id"], body.source),
        )
        c.execute(
            "UPDATE accounts SET balance=balance+? WHERE id=?",
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


@router.get("/plan")
def plan():
    """Line deposits/balances against unpaid bills; recommend payments;
    show leftover discretionary balance."""
    today_day = date.today().day
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
        bills.sort(key=lambda b: (b["due_day"] < today_day, b["due_day"]))
        recommendations, remaining = [], total_available
        for b in bills:
            status = "due soon" if b["due_day"] >= today_day else "overdue"
            afford = remaining >= b["amount"]
            recommendations.append(
                {
                    "bill": b["name"],
                    "amount": b["amount"],
                    "due_day": b["due_day"],
                    "status": status,
                    "recommend": "pay now" if afford else "hold — insufficient funds",
                }
            )
            if afford:
                remaining -= b["amount"]
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
            "leftover_after_bills": remaining,
            "food_nudges": food_nudges,
            "note": "Vaultborne accounts stay separate from discretionary flows.",
        }


@router.post("/nudges/{nudge_id}/resolve")
def resolve_nudge(nudge_id: int):
    with conn() as c:
        c.execute("UPDATE nudges SET resolved=1 WHERE id=?", (nudge_id,))
        return {"ok": True}
