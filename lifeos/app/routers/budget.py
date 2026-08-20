"""Semi-monthly financial command center: paycheck allocation, safe-to-spend,
bank reconciliation audit, sinking funds, debt paydown, net worth + forecast.

Money flow: OnePay (operating — bills + pocket cash), Truliant (savings
split), Relay (earmarked sinking buckets). Paycheck #1 covers days 1–14,
Paycheck #2 covers the 15th onward.
"""
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn, get_setting

router = APIRouter(prefix="/api/budget", tags=["budget"])


class DebtPaymentIn(BaseModel):
    amount: float


class DebtIn(BaseModel):
    name: str
    total: float = 0
    remaining: float = 0
    installment: float = 0
    cadence: str = "per paycheck"
    note: str = ""


class AssetBalanceIn(BaseModel):
    balance: float


def current_period(today: date | None = None) -> dict:
    today = today or date.today()
    n = 1 if today.day < 15 else 2
    return {"month": today.strftime("%Y-%m"), "paycheck": n,
            "key": f"{today:%Y-%m}-P{n}"}


def _cfg(key: str, default: float = 0) -> float:
    try:
        return float(get_setting(key) or default)
    except ValueError:
        return default


def _period_bills(c, period: dict) -> list[dict]:
    bills = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM bills WHERE paycheck=? ORDER BY due_day, name",
            (period["paycheck"],),
        ).fetchall()
    ]
    for b in bills:
        b["paid"] = b["paid_period"] == period["key"]
    return bills


def _fund_monthly_total(c) -> float:
    row = c.execute(
        "SELECT COALESCE(SUM(monthly),0) s FROM savings_goals"
    ).fetchone()
    return row["s"]


def _overview(c) -> dict:
    period = current_period()
    bills = _period_bills(c, period)
    accounts = [
        dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    ]
    goals = [
        dict(r)
        for r in c.execute("SELECT * FROM savings_goals ORDER BY name").fetchall()
    ]
    debts = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM debts WHERE remaining>0 ORDER BY remaining DESC"
        ).fetchall()
    ]
    assets = [
        dict(r) for r in c.execute("SELECT * FROM assets ORDER BY name").fetchall()
    ]

    onepay_in = _cfg("split_onepay", 1754.61)
    truliant_in = _cfg("split_truliant", 309.64)
    allocated = sum(b["amount"] for b in bills)
    fund_half = _fund_monthly_total(c) / 2  # buckets funded across both checks
    safe_to_spend = round(onepay_in - allocated - fund_half, 2)
    unpaid = [b for b in bills if not b["paid"]]

    # Audit: real bank cash vs what the budget says is still committed.
    cash = {a["name"]: a["balance"] for a in accounts}
    ecosystem_cash = sum(a["balance"] for a in accounts)
    unpaid_total = sum(b["amount"] for b in unpaid)
    bucket_saved = sum(g["saved"] for g in goals)
    relay_balance = cash.get("Relay", 0)
    relay_gap = round(relay_balance - bucket_saved, 2)
    onepay_gap = round(cash.get("OnePay", 0) - unpaid_total, 2)
    if onepay_gap < 0:
        audit = "action needed"
        audit_note = (
            f"OnePay is ${-onepay_gap:.2f} short of the ${unpaid_total:.2f}"
            " still committed this paycheck."
        )
    elif abs(relay_gap) > 1:
        audit = "buffered"
        audit_note = (
            f"Relay holds ${relay_balance:.2f} vs ${bucket_saved:.2f} earmarked"
            f" in buckets ({'+' if relay_gap > 0 else ''}{relay_gap:.2f})."
        )
    else:
        audit = "balanced"
        audit_note = "Every dollar is accounted for."

    total_debt = sum(d["remaining"] for d in debts)
    net_worth = round(ecosystem_cash + sum(a["balance"] for a in assets), 2)

    return {
        "period": period,
        "paycheck_in": {
            "net": _cfg("net_per_paycheck", 2064.25),
            "onepay": onepay_in,
            "truliant": truliant_in,
        },
        "bills": bills,
        "allocated": round(allocated, 2),
        "bucket_contribution": round(fund_half, 2),
        "safe_to_spend": safe_to_spend,
        "unpaid_total": round(unpaid_total, 2),
        "accounts": accounts,
        "ecosystem_cash": round(ecosystem_cash, 2),
        "audit": audit,
        "audit_note": audit_note,
        "funds": goals,
        "debts": debts,
        "total_debt": round(total_debt, 2),
        "assets": assets,
        "net_worth": net_worth,
    }


@router.get("/overview")
def overview():
    with conn() as c:
        return _overview(c)


@router.get("/forecast")
def forecast(periods: int = 6):
    """Project the next N paychecks: income minus that check's allocations
    and bucket contributions = expected surplus (or squeeze)."""
    periods = max(1, min(periods, 12))
    with conn() as c:
        onepay_in = _cfg("split_onepay", 1754.61)
        truliant_in = _cfg("split_truliant", 309.64)
        fund_half = _fund_monthly_total(c) / 2
        by_check = {}
        for n in (1, 2):
            row = c.execute(
                "SELECT COALESCE(SUM(amount),0) s FROM bills WHERE paycheck=?",
                (n,),
            ).fetchone()
            by_check[n] = row["s"]
        cur = current_period()
        month, n = cur["month"], cur["paycheck"]
        running = 0.0
        out = []
        for _ in range(periods):
            n += 1
            if n > 2:
                n = 1
                y, m = (int(x) for x in month.split("-"))
                m += 1
                if m > 12:
                    y, m = y + 1, 1
                month = f"{y:04d}-{m:02d}"
            surplus = round(onepay_in - by_check[n] - fund_half, 2)
            running = round(running + surplus + truliant_in, 2)
            out.append(
                {
                    "period": f"{month}-P{n}",
                    "bills": round(by_check[n], 2),
                    "surplus": surplus,
                    "savings_split": truliant_in,
                    "projected_buffer": running,
                }
            )
        return {"forecast": out}


@router.post("/bills/{bill_id}/paid")
def mark_bill_paid(bill_id: int):
    """Mark a bill paid for the current semi-monthly period."""
    period = current_period()
    with conn() as c:
        cur = c.execute(
            "UPDATE bills SET paid_period=?, paid_month=? WHERE id=?",
            (period["key"], period["month"], bill_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "bill not found")
        return {"ok": True, "period": period["key"]}


@router.post("/bills/{bill_id}/unpaid")
def mark_bill_unpaid(bill_id: int):
    with conn() as c:
        cur = c.execute(
            "UPDATE bills SET paid_period=NULL WHERE id=?", (bill_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "bill not found")
        return {"ok": True}


@router.get("/debts")
def list_debts():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM debts ORDER BY remaining DESC").fetchall()
        ]


@router.post("/debts")
def add_debt(body: DebtIn):
    with conn() as c:
        c.execute(
            "INSERT INTO debts(name,total,remaining,installment,cadence,note)"
            " VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET"
            " total=excluded.total, remaining=excluded.remaining,"
            " installment=excluded.installment, cadence=excluded.cadence,"
            " note=excluded.note",
            (body.name.strip(), body.total, body.remaining or body.total,
             body.installment, body.cadence, body.note),
        )
        return {"ok": True}


@router.post("/debts/{debt_id}/payment")
def pay_debt(debt_id: int, body: DebtPaymentIn):
    with conn() as c:
        row = c.execute("SELECT * FROM debts WHERE id=?", (debt_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "debt not found")
        remaining = max(0, round(row["remaining"] - body.amount, 2))
        c.execute("UPDATE debts SET remaining=? WHERE id=?", (remaining, debt_id))
        return {"ok": True, "remaining": remaining, "paid_off": remaining == 0}


@router.get("/assets")
def list_assets():
    with conn() as c:
        return [
            dict(r) for r in c.execute("SELECT * FROM assets ORDER BY name").fetchall()
        ]


@router.put("/assets/{asset_id}/balance")
def set_asset_balance(asset_id: int, body: AssetBalanceIn):
    with conn() as c:
        cur = c.execute(
            "UPDATE assets SET balance=? WHERE id=?", (body.balance, asset_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "asset not found")
        return {"ok": True}


def budget_speech(c) -> dict:
    """Ready-to-speak budget answers for Jarvis voice intents."""
    o = _overview(c)
    p = o["period"]
    unpaid = [b for b in o["bills"] if not b["paid"]]
    sts = o["safe_to_spend"]
    budget = (
        f"Paycheck {p['paycheck']} plan: ${o['allocated']:.0f} allocated to"
        f" bills, ${o['bucket_contribution']:.0f} to buckets, leaving"
        f" ${sts:.0f} safe to spend. Audit says {o['audit']}:"
        f" {o['audit_note']}"
    )
    if unpaid:
        items = ", ".join(f"{b['name']} ${b['amount']:.0f}" for b in unpaid[:6])
        paycheck = (
            f"Still to pay on paycheck {p['paycheck']}: {items}"
            f" — ${o['unpaid_total']:.0f} total."
        )
    else:
        paycheck = f"Everything on paycheck {p['paycheck']} is paid, sir."
    networth = (
        f"Ecosystem cash is ${o['ecosystem_cash']:.0f}; net worth including"
        f" retirement is ${o['net_worth']:.0f}; total debt remaining is"
        f" ${o['total_debt']:.0f}."
    )
    return {
        "budget": budget,
        "paycheck": paycheck,
        "networth": networth,
        "safe_to_spend": sts,
        "audit_health": o["audit"],
    }
