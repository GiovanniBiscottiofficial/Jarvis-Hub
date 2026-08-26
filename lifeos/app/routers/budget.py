"""Semi-monthly financial command center: paycheck allocation, safe-to-spend,
bank reconciliation audit, sinking funds, debt paydown, net worth + forecast.

Money flow: OnePay (operating — bills + pocket cash), Truliant (savings
split), Relay (earmarked sinking buckets). Paycheck #1 covers days 1–14,
Paycheck #2 covers the 15th onward.
"""
from datetime import date
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn, get_setting
from ..paydays import NET_PAY, payday_schedule, upcoming_period

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
    apr: float = 0


class AssetBalanceIn(BaseModel):
    balance: float


class WindfallIn(BaseModel):
    amount: float
    route: str  # "debt" | "split" | "buffer"


class FundContributionIn(BaseModel):
    amount: float


def current_period(today: date | None = None) -> dict:
    return upcoming_period(today)


def _cfg(key: str, default: float = 0) -> float:
    try:
        return float(get_setting(key) or default)
    except ValueError:
        return default


def _period_bills(c, period: dict) -> list[dict]:
    bills = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM bills WHERE paycheck=?"
            " AND (start_period IS NULL OR start_period='' OR start_period<=?)"
            " AND (one_time=0 OR start_period=?)"
            " ORDER BY due_day, name",
            (period["paycheck"], period["key"], period["key"]),
        ).fetchall()
    ]
    for b in bills:
        b["paid"] = (
            b["paid_period"] == period["key"]
            or b["paid_month"] == period["month"]
        )
    return bills


def _fund_monthly_total(c) -> float:
    row = c.execute(
        "SELECT COALESCE(SUM(monthly),0) s FROM savings_goals"
    ).fetchone()
    return row["s"]


def _adjust_balance(c, role: str, delta: float) -> None:
    """Ledger sync: move real cash on the matching account (by role)."""
    row = c.execute(
        "SELECT id, balance FROM accounts WHERE role=? ORDER BY id LIMIT 1",
        (role,),
    ).fetchone()
    if row is None:
        raise HTTPException(409, f"no {role} account configured")
    new_balance = round(row["balance"] + delta, 2)
    if new_balance < 0:
        raise HTTPException(409, f"insufficient funds in {role} account")
    c.execute(
        "UPDATE accounts SET balance=? WHERE id=?",
        (new_balance, row["id"]),
    )


def _adjust_account_balance(c, account_id: int, delta: float) -> None:
    row = c.execute(
        "SELECT balance FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(409, "bill account no longer exists")
    new_balance = round(row["balance"] + delta, 2)
    if new_balance < 0:
        raise HTTPException(409, "insufficient funds in bill account")
    c.execute(
        "UPDATE accounts SET balance=? WHERE id=?",
        (new_balance, account_id),
    )


_SLIDEABLE = ("paydown", "queued", "catchup", "past due")


def _auto_shift(unpaid: list[dict], fund_half: float, deficit: float) -> dict:
    """Suggest non-urgent items that can slide to the next check without
    penalties: bucket contributions first, then catchup/paydown
    arrangements (never rent, car note, insurance, or utilities)."""
    suggestions = []
    if fund_half > 0:
        suggestions.append(
            {"kind": "buckets", "name": "Sinking bucket contributions",
             "amount": round(fund_half, 2)}
        )
    for b in sorted(unpaid, key=lambda b: b["amount"]):
        text = f"{b['name']} {b['note']}".lower()
        if any(k in text for k in _SLIDEABLE):
            suggestions.append(
                {"kind": "bill", "name": b["name"], "amount": b["amount"]}
            )
    shiftable = round(sum(s["amount"] for s in suggestions), 2)
    return {
        "deficit": round(deficit, 2),
        "suggestions": suggestions,
        "shiftable": shiftable,
        "covers_deficit": shiftable >= deficit,
    }


def _debt_free_estimate(c) -> dict | None:
    """Project when temporary catchups/arrangements hit zero at the current
    installment pace."""
    rows = c.execute("SELECT * FROM debts WHERE remaining>0").fetchall()
    total = sum(r["remaining"] for r in rows)
    if total <= 0:
        return None
    per_check = 0.0
    for r in rows:
        if r["installment"] <= 0:
            continue
        per_check += (
            r["installment"] / 2 if "month" in r["cadence"] else r["installment"]
        )
    if per_check <= 0:
        return {"total": round(total, 2), "checks": None, "period": None}
    checks = -(-total // per_check)  # ceil
    horizon = payday_schedule(count=int(checks))[-1]
    return {
        "total": round(total, 2),
        "checks": int(checks),
        "period": horizon["period"],
    }


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

    onepay_in = _cfg("split_onepay", 1754.60)
    truliant_in = _cfg("split_truliant", 309.64)
    allocated = sum(b["amount"] for b in bills)
    fund_half = _fund_monthly_total(c) / 2  # buckets funded across both checks
    safe_to_spend = round(onepay_in - allocated - fund_half, 2)
    unpaid = [b for b in bills if not b["paid"]]

    # Audit: real bank cash vs what the budget says is still committed.
    cash = {a["name"]: a["balance"] for a in accounts}
    ecosystem_cash = sum(a["balance"] for a in accounts)
    # Protected rule: savings/bucket money is never spendable pocket cash
    protected_cash = round(sum(
        max(0, a["balance"])
        for a in accounts
        if a["role"] in ("savings", "buckets")
    ), 2)
    unpaid_total = sum(b["amount"] for b in unpaid)
    bucket_saved = sum(g["saved"] for g in goals)
    relay_balance = cash.get("Relay", 0)
    relay_gap = round(relay_balance - bucket_saved, 2)
    onepay_gap = round(cash.get("OnePay", 0) - unpaid_total, 2)
    auto_shift = None
    cycle_start_text = get_setting("budget_cycle_start")
    try:
        cycle_start = date.fromisoformat(cycle_start_text)
    except ValueError:
        cycle_start = date.today()
    cycle_pending = date.today() < cycle_start
    if cycle_pending:
        audit = "scheduled"
        audit_note = (
            f"The first budget cycle begins with Paycheck 1 on"
            f" {cycle_start.strftime('%B')} {cycle_start.day}. No bill is overdue"
            " before that deposit."
        )
        onepay_gap = round(cash.get("OnePay", 0), 2)
    elif onepay_gap < 0:
        audit = "action needed"
        audit_note = (
            f"OnePay is ${-onepay_gap:.2f} short of the ${unpaid_total:.2f}"
            " still committed this paycheck."
        )
        auto_shift = _auto_shift(unpaid, fund_half, -onepay_gap)
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
    debt_free = _debt_free_estimate(c)

    return {
        "period": period,
        "paycheck_in": {
            "net": _cfg("net_per_paycheck", NET_PAY),
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
        "protected_cash": protected_cash,
        "pocket_cash": onepay_gap,
        "audit": audit,
        "audit_note": audit_note,
        "cycle_start": cycle_start.isoformat(),
        "cycle_pending": cycle_pending,
        "auto_shift": auto_shift,
        "debt_free": debt_free,
        "funds": goals,
        "debts": debts,
        "total_debt": round(total_debt, 2),
        "assets": assets,
        "net_worth": net_worth,
        "paydays": payday_schedule(count=4),
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
        onepay_in = _cfg("split_onepay", 1754.60)
        truliant_in = _cfg("split_truliant", 309.64)
        fund_half = _fund_monthly_total(c) / 2
        running = 0.0
        out = []
        for payday in payday_schedule(count=periods):
            n = payday["paycheck"]
            row = c.execute(
                "SELECT COALESCE(SUM(amount),0) s FROM bills WHERE paycheck=?"
                " AND (start_period IS NULL OR start_period='' OR start_period<=?)"
                " AND (one_time=0 OR start_period=?)",
                (n, payday["period"], payday["period"]),
            ).fetchone()
            bills = row["s"]
            surplus = round(onepay_in - bills - fund_half, 2)
            running = round(running + surplus + truliant_in, 2)
            out.append(
                {
                    "period": payday["period"],
                    "payday": payday["date"],
                    "bills": round(bills, 2),
                    "surplus": surplus,
                    "savings_split": truliant_in,
                    "projected_buffer": running,
                }
            )
        return {"forecast": out}


def _payment_period(row, requested: str | None) -> dict:
    if requested is None:
        return current_period()
    match = re.fullmatch(r"(\d{4}-\d{2})-P([12])", requested)
    if match is None:
        raise HTTPException(400, "period must use YYYY-MM-P1 or YYYY-MM-P2")
    paycheck = int(match.group(2))
    if row["paycheck"] in (1, 2) and row["paycheck"] != paycheck:
        raise HTTPException(409, "bill is assigned to a different paycheck")
    if row["start_period"] and requested < row["start_period"]:
        raise HTTPException(409, f"bill begins with {row['start_period']}")
    return {"month": match.group(1), "paycheck": paycheck, "key": requested}


@router.post("/bills/{bill_id}/paid")
def mark_bill_paid(bill_id: int, period: str | None = None):
    """Mark a bill paid for the current month and sync its account balance."""
    with conn() as c:
        row = c.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "bill not found")
        period = _payment_period(row, period)
        if row["paid_period"] == period["key"] or row["paid_month"] == period["month"]:
            return {"ok": True, "period": row["paid_period"] or period["key"], "already_paid": True}
        c.execute(
            "UPDATE bills SET paid_period=?, paid_month=? WHERE id=?",
            (period["key"], period["month"], bill_id),
        )
        account_id = row["account_id"]
        if account_id is None:
            _adjust_balance(c, "operating", -row["amount"])
        else:
            _adjust_account_balance(c, account_id, -row["amount"])
        return {"ok": True, "period": period["key"], "already_paid": False}


@router.post("/bills/{bill_id}/unpaid")
def mark_bill_unpaid(bill_id: int, period: str | None = None):
    with conn() as c:
        row = c.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "bill not found")
        period = _payment_period(row, period)
        is_current_payment = (
            row["paid_period"] == period["key"]
            or row["paid_month"] == period["month"]
        )
        if is_current_payment:
            if row["account_id"] is None:
                _adjust_balance(c, "operating", row["amount"])
            else:
                _adjust_account_balance(c, row["account_id"], row["amount"])
            c.execute(
                "UPDATE bills SET paid_period=NULL, paid_month=NULL WHERE id=?",
                (bill_id,),
            )
        return {"ok": True, "already_unpaid": not is_current_payment}


@router.get("/debts")
def list_debts():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM debts ORDER BY remaining DESC").fetchall()
        ]


@router.post("/debts")
def add_debt(body: DebtIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "debt name is required")
    if body.total < 0 or body.remaining < 0 or body.installment < 0 or body.apr < 0:
        raise HTTPException(400, "debt amounts cannot be negative")
    if body.remaining > body.total and body.total > 0:
        raise HTTPException(400, "remaining debt cannot exceed total debt")
    with conn() as c:
        c.execute(
            "INSERT INTO debts(name,total,remaining,installment,cadence,note,apr)"
            " VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET"
            " total=excluded.total, remaining=excluded.remaining,"
            " installment=excluded.installment, cadence=excluded.cadence,"
            " note=excluded.note, apr=excluded.apr",
            (name, body.total, body.remaining or body.total,
             body.installment, body.cadence.strip(), body.note.strip(), body.apr),
        )
        return {"ok": True}


@router.post("/debts/{debt_id}/payment")
def pay_debt(debt_id: int, body: DebtPaymentIn):
    """Ledger sync: the payment comes out of the operating account."""
    with conn() as c:
        row = c.execute("SELECT * FROM debts WHERE id=?", (debt_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "debt not found")
        if body.amount <= 0:
            raise HTTPException(400, "payment amount must be positive")
        if body.amount > row["remaining"]:
            raise HTTPException(400, "payment amount exceeds remaining debt")
        remaining = round(row["remaining"] - body.amount, 2)
        c.execute("UPDATE debts SET remaining=? WHERE id=?", (remaining, debt_id))
        _adjust_balance(c, "operating", -body.amount)
        return {"ok": True, "remaining": remaining, "paid_off": remaining == 0}


@router.post("/funds/{goal_id}/contribute")
def contribute_fund(goal_id: int, body: FundContributionIn):
    """Ledger sync: moves cash operating -> buckets and grows the goal."""
    if body.amount <= 0:
        raise HTTPException(400, "amount must be positive")
    with conn() as c:
        row = c.execute(
            "SELECT * FROM savings_goals WHERE id=?", (goal_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "goal not found")
        c.execute(
            "UPDATE savings_goals SET saved=ROUND(saved+?,2) WHERE id=?",
            (body.amount, goal_id),
        )
        _adjust_balance(c, "operating", -body.amount)
        _adjust_balance(c, "buckets", body.amount)
        return {"ok": True, "saved": round(row["saved"] + body.amount, 2)}


@router.post("/windfall")
def windfall(body: WindfallIn):
    """Route side income / windfalls: 100% to highest debt, 50/50 debt +
    buckets, or straight into the safe-to-spend buffer."""
    if body.amount <= 0:
        raise HTTPException(400, "amount must be positive")
    if body.route not in ("debt", "split", "buffer"):
        raise HTTPException(400, "route must be debt, split or buffer")
    with conn() as c:
        _adjust_balance(c, "operating", body.amount)  # cash lands in OnePay
        applied = []
        debt_share = {"debt": body.amount, "split": round(body.amount / 2, 2),
                      "buffer": 0.0}[body.route]
        bucket_share = round(body.amount / 2, 2) if body.route == "split" else 0.0
        pool = debt_share
        for d in c.execute(
            "SELECT * FROM debts WHERE remaining>0 ORDER BY remaining DESC"
        ).fetchall():
            if pool <= 0:
                break
            pay = min(pool, d["remaining"])
            c.execute(
                "UPDATE debts SET remaining=ROUND(remaining-?,2) WHERE id=?",
                (pay, d["id"]),
            )
            applied.append({"debt": d["name"], "paid": round(pay, 2)})
            pool = round(pool - pay, 2)
        paid_out = round(debt_share - pool, 2)
        if paid_out > 0:
            _adjust_balance(c, "operating", -paid_out)
        if bucket_share > 0:
            goal = c.execute(
                "SELECT * FROM savings_goals WHERE name LIKE '%Emergency%'"
                " ORDER BY id LIMIT 1"
            ).fetchone() or c.execute(
                "SELECT * FROM savings_goals ORDER BY id LIMIT 1"
            ).fetchone()
            if goal is not None:
                c.execute(
                    "UPDATE savings_goals SET saved=ROUND(saved+?,2) WHERE id=?",
                    (bucket_share, goal["id"]),
                )
            _adjust_balance(c, "operating", -bucket_share)
            _adjust_balance(c, "buckets", bucket_share)
        buffer_kept = round(body.amount - paid_out - bucket_share, 2)
        return {
            "ok": True,
            "route": body.route,
            "debt_payments": applied,
            "to_buckets": bucket_share,
            "kept_in_onepay": buffer_kept,
        }


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
        paycheck = f"Everything on paycheck {p['paycheck']} is paid, Giovanni."
    networth = (
        f"Ecosystem cash is ${o['ecosystem_cash']:.0f}; net worth including"
        f" retirement is ${o['net_worth']:.0f}; total debt remaining is"
        f" ${o['total_debt']:.0f}."
    )
    df = o["debt_free"]
    if df and df["checks"]:
        networth += (
            f" At the current pace you're debt free in {df['checks']} paychecks,"
            f" around {df['period']}."
        )
    return {
        "budget": budget,
        "paycheck": paycheck,
        "networth": networth,
        "safe_to_spend": sts,
        "audit_health": o["audit"],
    }
