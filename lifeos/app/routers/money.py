"""Audited Money Command Center built on the existing Budget & Vault ledgers."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import conn, get_setting
from ..paydays import NET_PAY, bill_due_date_for_payday, payday_schedule

router = APIRouter(prefix="/api/money", tags=["money-command"])


class ImportIn(BaseModel):
    content: str
    account_id: int
    format: Literal["auto", "csv", "ofx"] = "auto"
    source: str = "statement_import"


class TransactionReviewIn(BaseModel):
    decision: Literal["verify", "exclude", "match"]
    confirm: bool = False
    spending_id: int | None = None
    category: str | None = None
    note: str = ""


class ReconcileIn(BaseModel):
    actual_balance: float
    reason: str = "Statement reconciliation"
    confirm: bool = False


class FundPaycheckIn(BaseModel):
    # Retained as an optional compatibility field for older headless clients.
    # Funding always follows the authoritative fixed account split.
    account_id: int | None = None
    confirm: bool = False


class ClosePaycheckIn(BaseModel):
    confirm: bool = False


class SimulationIn(BaseModel):
    one_time_spending: float = 0
    daily_spending_change: float = 0
    income_change_per_check: float = 0
    periods: int = 6


def _money_setting(key: str, default: float) -> float:
    try:
        return float(get_setting(key) or default)
    except (TypeError, ValueError):
        return default


def _paycheck_split() -> dict[str, float]:
    net = _money_setting("net_per_paycheck", NET_PAY)
    truliant = min(309.00, net)
    return {"net": net, "onepay": round(net - truliant, 2), "truliant": truliant, "relay": 0.0}


def _payroll_asset_preview(c, payday: str) -> list[dict]:
    pay_year = payday[:4]
    rows = []
    for asset in c.execute(
        "SELECT * FROM assets WHERE kind='retirement' AND per_paycheck>0 ORDER BY name"
    ).fetchall():
        contribution = round(float(asset["per_paycheck"]), 2)
        same_year = str(asset["as_of"] or "")[:4] == pay_year
        ytd_before = float(asset["ytd_contributions"]) if same_year else 0.0
        rows.append({
            "id": asset["id"],
            "name": asset["name"],
            "contribution": contribution,
            "balance_before": float(asset["balance"]),
            "balance_after": round(float(asset["balance"]) + contribution, 2),
            "ytd_before": ytd_before,
            "ytd_after": round(ytd_before + contribution, 2),
            "lifetime_before": float(asset["lifetime_contributions"]),
            "lifetime_after": round(float(asset["lifetime_contributions"]) + contribution, 2),
            "as_of": payday,
        })
    return rows


def _fingerprint(account_id: int, posted: str, direction: str, amount: float, merchant: str, external_id: str = "") -> str:
    material = "|".join((str(account_id), posted, direction, f"{amount:.2f}", merchant.strip().lower(), external_id.strip()))
    return hashlib.sha256(material.encode()).hexdigest()


def _category(merchant: str) -> str:
    text = merchant.lower()
    groups = {
        "Groceries": ("food lion", "walmart", "aldi", "grocery", "market"),
        "Dining": ("restaurant", "doordash", "uber eats", "cafe", "grill"),
        "Transportation": ("shell", "exxon", "bp ", "fuel", "gas", "uber", "lyft"),
        "Utilities": ("duke", "spectrum", "water", "electric", "internet"),
        "Health": ("pharmacy", "cvs", "walgreens", "medical"),
    }
    return next((name for name, words in groups.items() if any(word in text for word in words)), "Uncategorized")


def _parse_date(value: str) -> str:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d"):
        try:
            return datetime.strptime(raw[:10] if "-" in raw else raw[:8] if fmt == "%Y%m%d" else raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unrecognized transaction date: {raw}")


def _parse_csv(content: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    for source in reader:
        normalized = {str(k).strip().lower(): str(v or "").strip() for k, v in source.items()}
        posted = normalized.get("date") or normalized.get("posted date") or normalized.get("transaction date")
        merchant = normalized.get("merchant") or normalized.get("description") or normalized.get("name") or "Imported transaction"
        debit = normalized.get("debit") or normalized.get("withdrawal")
        credit = normalized.get("credit") or normalized.get("deposit")
        amount_raw = normalized.get("amount")
        type_hint = (normalized.get("type") or normalized.get("transaction type") or "").lower()
        if debit:
            amount, direction = abs(float(debit.replace("$", "").replace(",", ""))), "debit"
        elif credit:
            amount, direction = abs(float(credit.replace("$", "").replace(",", ""))), "credit"
        elif amount_raw:
            signed = float(amount_raw.replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
            is_debit = signed < 0 or any(word in type_hint for word in ("debit", "purchase", "withdrawal", "payment"))
            amount, direction = abs(signed), "debit" if is_debit else "credit"
        else:
            continue
        rows.append({"posted_date": _parse_date(posted), "merchant": merchant, "amount": amount, "direction": direction, "external_id": normalized.get("id", "")})
    return rows


def _ofx_value(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}>([^<\r\n]+)", block, re.I)
    return match.group(1).strip() if match else ""


def _parse_ofx(content: str) -> list[dict]:
    rows = []
    for block in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|$)", content, re.I | re.S):
        signed = float(_ofx_value(block, "TRNAMT") or 0)
        if not signed:
            continue
        rows.append({
            "posted_date": _parse_date(_ofx_value(block, "DTPOSTED")[:8]),
            "merchant": _ofx_value(block, "NAME") or _ofx_value(block, "MEMO") or "Imported transaction",
            "amount": abs(signed),
            "direction": "debit" if signed < 0 else "credit",
            "external_id": _ofx_value(block, "FITID"),
        })
    return rows


def _audit(c, action: str, risk: str, confirmed: bool, subject: str, before: dict, after: dict, reason: str = "") -> None:
    c.execute(
        "INSERT INTO financial_action_audit(action,risk,confirmed,subject,before_json,after_json,reason) VALUES(?,?,?,?,?,?,?)",
        (action, risk, int(confirmed), subject, json.dumps(before), json.dumps(after), reason.strip()),
    )


def _average_daily_spending(c, days: int = 30) -> float:
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    row = c.execute("SELECT COALESCE(SUM(amount),0) total FROM spending WHERE date(ts)>=?", (cutoff,)).fetchone()
    return round(float(row["total"]) / days, 2)


def _payoff_strategies(debts: list[dict]) -> dict:
    priority = sorted(debts, key=lambda debt: (int(debt.get("priority") or 999), -float(debt["remaining"]), debt["name"]))
    snowball = sorted(debts, key=lambda debt: (float(debt["remaining"]), debt["name"]))
    missing_apr = [debt["name"] for debt in debts if float(debt.get("apr") or 0) <= 0]
    avalanche = [] if missing_apr else sorted(
        debts,
        key=lambda debt: (-float(debt["apr"]), float(debt["remaining"]), debt["name"]),
    )
    return {
        "recommended": "priority",
        "priority": priority,
        "snowball": snowball,
        "avalanche": avalanche,
        "avalanche_ready": not missing_apr,
        "missing_apr": missing_apr,
    }


def _mission_rows(c, count: int = 6) -> list[dict]:
    missions = []
    for payday in payday_schedule(count=count):
        bills = [dict(row) for row in c.execute(
            "SELECT id,name,amount,due_day,paycheck,paid_period,start_period,one_time,note FROM bills WHERE paycheck=?"
            " AND (start_period IS NULL OR start_period='' OR start_period<=?)"
            " AND (one_time=0 OR start_period=?) ORDER BY due_day,name",
            (payday["paycheck"], payday["period"], payday["period"]),
        ).fetchall()]
        for bill in bills:
            bill["due_date"] = bill_due_date_for_payday(payday, bill["due_day"]).isoformat()
            bill["paid"] = bill["paid_period"] == payday["period"]
        cycle = c.execute("SELECT * FROM paycheck_cycles WHERE period=?", (payday["period"],)).fetchone()
        bill_total = round(sum(float(bill["amount"]) for bill in bills), 2)
        split = _paycheck_split()
        net = split["net"]
        payroll_assets = _payroll_asset_preview(c, payday["date"])
        missions.append({
            **payday,
            "amount": net,
            "distribution": split,
            "payroll_contributions": payroll_assets,
            "payroll_contribution_total": round(sum(row["contribution"] for row in payroll_assets), 2),
            "status": cycle["status"] if cycle else "planned",
            "account_id": cycle["account_id"] if cycle else None,
            "opening_balance": cycle["opening_balance"] if cycle else None,
            "closing_balance": cycle["closing_balance"] if cycle else None,
            "bills": bills,
            "bill_total": bill_total,
            "planned_remaining": round(split["onepay"] - bill_total, 2),
        })
    return missions


def _cashflow(c, scenario: SimulationIn) -> dict:
    periods = max(2, min(scenario.periods, 12))
    missions = _mission_rows(c, periods)
    cash = round(sum(float(row["balance"]) for row in c.execute("SELECT balance FROM accounts").fetchall()) - scenario.one_time_spending, 2)
    daily = max(0, _average_daily_spending(c) + scenario.daily_spending_change)
    prior = date.today()
    low = cash
    rows = []
    for mission in missions:
        paydate = date.fromisoformat(mission["date"])
        days = max(1, (paydate - prior).days)
        income = mission["amount"] + scenario.income_change_per_check
        everyday = round(daily * days, 2)
        cash = round(cash + income - mission["bill_total"] - everyday, 2)
        low = min(low, cash)
        rows.append({"period": mission["period"], "payday": mission["date"], "income": income, "bills": mission["bill_total"], "everyday_spending": everyday, "projected_cash": cash})
        prior = paydate
    return {
        "starting_cash": round(sum(float(row["balance"]) for row in c.execute("SELECT balance FROM accounts").fetchall()), 2),
        "average_daily_spending": daily,
        "scenario": scenario.model_dump(),
        "lowest_projected_cash": low,
        "shortfall": low < 0,
        "forecast": rows,
        "policy": "Simulation only. No account, bill, debt, or goal was changed.",
    }


@router.get("/command-center")
def command_center():
    with conn() as c:
        c.execute("BEGIN")
        accounts = [dict(row) for row in c.execute("SELECT * FROM accounts ORDER BY id").fetchall()]
        inbox = [dict(row) for row in c.execute(
            "SELECT t.*,a.name account_name FROM financial_transactions t LEFT JOIN accounts a ON a.id=t.account_id"
            " ORDER BY CASE t.status WHEN 'pending' THEN 0 ELSE 1 END,t.posted_date DESC,t.id DESC LIMIT 100"
        ).fetchall()]
        reconciliations = [dict(row) for row in c.execute(
            "SELECT r.*,a.name account_name FROM account_reconciliations r JOIN accounts a ON a.id=r.account_id ORDER BY r.id DESC LIMIT 12"
        ).fetchall()]
        audits = [dict(row) for row in c.execute("SELECT * FROM financial_action_audit ORDER BY id DESC LIMIT 20").fetchall()]
        missions = _mission_rows(c)
        forecast = _cashflow(c, SimulationIn())
        debts = [dict(row) for row in c.execute("SELECT * FROM debts WHERE remaining>0 ORDER BY priority,remaining DESC").fetchall()]
        recent_spending = [dict(row) for row in c.execute(
            "SELECT id,ts,amount,merchant FROM spending WHERE date(ts)>=date('now','-45 days') ORDER BY ts DESC,id DESC LIMIT 60"
        ).fetchall()]
        pending = sum(1 for item in inbox if item["status"] == "pending")
        return {
            "accounts": accounts,
            "transaction_inbox": inbox,
            "pending_count": pending,
            "reconciliations": reconciliations,
            "paycheck_missions": missions,
            "forecast": forecast,
            "debts": debts,
            "payoff_strategies": _payoff_strategies(debts),
            "recent_spending": recent_spending,
            "audit": audits,
            "readiness": {
                "status": "review_needed" if pending else "reconciled" if reconciliations else "starting",
                "guidance": f"Review {pending} imported transaction{'s' if pending != 1 else ''}." if pending else "Transaction inbox is clear.",
                "last_reconciled": reconciliations[0]["created_at"] if reconciliations else None,
            },
            "policies": {
                "analysis": "automatic_read_only",
                "imports": "pending_until_reviewed",
                "balance_changes": "explicit_confirmation",
                "money_movement": "explicit_confirmation",
                "remote_execution": False,
            },
        }


@router.post("/import")
def import_statement(body: ImportIn):
    if not body.content.strip():
        raise HTTPException(400, "statement content is required")
    with conn() as c:
        account = c.execute("SELECT id FROM accounts WHERE id=?", (body.account_id,)).fetchone()
        if account is None:
            raise HTTPException(404, "account not found")
        fmt = body.format
        if fmt == "auto":
            fmt = "ofx" if "<OFX" in body.content.upper() or "<STMTTRN>" in body.content.upper() else "csv"
        try:
            rows = _parse_ofx(body.content) if fmt == "ofx" else _parse_csv(body.content)
        except (ValueError, TypeError) as error:
            raise HTTPException(400, f"statement could not be parsed: {error}") from error
        imported = duplicates = 0
        for row in rows:
            fingerprint = _fingerprint(body.account_id, row["posted_date"], row["direction"], row["amount"], row["merchant"], row["external_id"])
            cursor = c.execute(
                "INSERT OR IGNORE INTO financial_transactions(posted_date,account_id,direction,amount,merchant,category,source,external_id,fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                (row["posted_date"], body.account_id, row["direction"], row["amount"], row["merchant"], _category(row["merchant"]), body.source[:80], row["external_id"], fingerprint),
            )
            imported += cursor.rowcount
            duplicates += int(cursor.rowcount == 0)
        _audit(c, "finance.import_statement", "low", False, str(body.account_id), {}, {"imported": imported, "duplicates": duplicates}, f"{fmt.upper()} import; no balances changed")
        return {"ok": True, "format": fmt, "imported": imported, "duplicates": duplicates, "pending_review": imported}


@router.post("/transactions/{transaction_id}/review")
def review_transaction(transaction_id: int, body: TransactionReviewIn):
    with conn() as c:
        tx = c.execute("SELECT * FROM financial_transactions WHERE id=?", (transaction_id,)).fetchone()
        if tx is None:
            raise HTTPException(404, "transaction not found")
        if tx["status"] != "pending":
            raise HTTPException(409, "transaction was already reviewed")
        if body.decision in {"verify", "match"} and not body.confirm:
            return {"ok": False, "requires_confirmation": True, "preview": dict(tx), "effect": "Verify changes the ledger; Match links an existing spending entry."}
        if body.decision == "exclude":
            c.execute("UPDATE financial_transactions SET status='excluded',reviewed_at=datetime('now','localtime'),note=? WHERE id=?", (body.note[:250], transaction_id))
        elif body.decision == "match":
            if body.spending_id is None or c.execute("SELECT id FROM spending WHERE id=?", (body.spending_id,)).fetchone() is None:
                raise HTTPException(400, "valid spending_id is required to match")
            c.execute("UPDATE financial_transactions SET status='matched',matched_spending_id=?,reviewed_at=datetime('now','localtime'),note=? WHERE id=?", (body.spending_id, body.note[:250], transaction_id))
        else:
            account = c.execute("SELECT * FROM accounts WHERE id=?", (tx["account_id"],)).fetchone()
            if account is None:
                raise HTTPException(409, "transaction account no longer exists")
            delta = -float(tx["amount"]) if tx["direction"] == "debit" else float(tx["amount"])
            new_balance = round(float(account["balance"]) + delta, 2)
            spending_id = None
            if tx["direction"] == "debit":
                cursor = c.execute("INSERT INTO spending(ts,amount,merchant) VALUES(?,?,?)", (tx["posted_date"] + " 12:00:00", tx["amount"], tx["merchant"]))
                spending_id = cursor.lastrowid
            else:
                c.execute("INSERT INTO deposits(ts,amount,account_id,source) VALUES(?,?,?,'verified_import')", (tx["posted_date"] + " 12:00:00", tx["amount"], tx["account_id"]))
            c.execute("UPDATE accounts SET balance=? WHERE id=?", (new_balance, tx["account_id"]))
            c.execute("UPDATE financial_transactions SET status='verified',category=?,matched_spending_id=?,reviewed_at=datetime('now','localtime'),note=? WHERE id=?", ((body.category or tx["category"])[:80], spending_id, body.note[:250], transaction_id))
            _audit(c, "finance.verify_transaction", "high", True, str(transaction_id), {"balance": account["balance"], "transaction": dict(tx)}, {"balance": new_balance}, body.note)
        return {"ok": True, "decision": body.decision}


@router.post("/accounts/{account_id}/reconcile")
def reconcile_account(account_id: int, body: ReconcileIn):
    with conn() as c:
        account = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if account is None:
            raise HTTPException(404, "account not found")
        difference = round(body.actual_balance - float(account["balance"]), 2)
        preview = {"account": account["name"], "ledger_balance": account["balance"], "actual_balance": body.actual_balance, "difference": difference}
        if not body.confirm:
            return {"ok": False, "requires_confirmation": True, "preview": preview}
        c.execute("UPDATE accounts SET balance=? WHERE id=?", (body.actual_balance, account_id))
        c.execute("INSERT INTO account_reconciliations(account_id,previous_balance,actual_balance,difference,reason) VALUES(?,?,?,?,?)", (account_id, account["balance"], body.actual_balance, difference, body.reason[:250]))
        _audit(c, "finance.reconcile_balance", "high", True, account["name"], {"balance": account["balance"]}, {"balance": body.actual_balance, "difference": difference}, body.reason)
        return {"ok": True, "reconciliation": preview}


@router.post("/paychecks/{period}/fund")
def fund_paycheck(period: str, body: FundPaycheckIn):
    payday = next((item for item in payday_schedule(count=12) if item["period"] == period), None)
    if payday is None:
        raise HTTPException(404, "paycheck period is not in the current planning horizon")
    with conn() as c:
        accounts = {
            row["name"]: row
            for row in c.execute(
                "SELECT * FROM accounts WHERE name IN ('OnePay','Truliant')"
            ).fetchall()
        }
        if set(accounts) != {"OnePay", "Truliant"}:
            raise HTTPException(409, "OnePay and Truliant must both be commissioned")
        existing = c.execute("SELECT * FROM paycheck_cycles WHERE period=?", (period,)).fetchone()
        if existing and existing["status"] in {"funded", "closed"}:
            raise HTTPException(409, "paycheck is already funded")
        split = _paycheck_split()
        onepay = accounts["OnePay"]
        truliant = accounts["Truliant"]
        payroll_assets = _payroll_asset_preview(c, payday["date"])
        preview = {
            "period": period,
            "amount": split["net"],
            "distribution": {
                "OnePay": {
                    "deposit": split["onepay"],
                    "before": onepay["balance"],
                    "after": round(onepay["balance"] + split["onepay"], 2),
                },
                "Truliant": {
                    "deposit": split["truliant"],
                    "before": truliant["balance"],
                    "after": round(truliant["balance"] + split["truliant"], 2),
                },
                "Relay": {"deposit": 0.0, "purpose": "savings buckets only"},
            },
            "payroll_contributions": payroll_assets,
            "payroll_contribution_total": round(sum(row["contribution"] for row in payroll_assets), 2),
        }
        if not body.confirm:
            return {"ok": False, "requires_confirmation": True, "preview": preview}
        for name in ("OnePay", "Truliant"):
            allocation = preview["distribution"][name]
            account = accounts[name]
            c.execute("UPDATE accounts SET balance=? WHERE id=?", (allocation["after"], account["id"]))
            c.execute(
                "INSERT INTO deposits(amount,account_id,source) VALUES(?,?,?)",
                (allocation["deposit"], account["id"], f"paycheck_funding:{period}"),
            )
        for asset in payroll_assets:
            c.execute(
                "UPDATE assets SET balance=?,ytd_contributions=?,"
                " lifetime_contributions=?,as_of=? WHERE id=?",
                (
                    asset["balance_after"], asset["ytd_after"],
                    asset["lifetime_after"], asset["as_of"], asset["id"],
                ),
            )
        c.execute(
            "INSERT INTO paycheck_cycles(period,paycheck,payday,status,account_id,amount,opening_balance,funded_at)"
            " VALUES(?,?,?,'funded',?,?,?,datetime('now','localtime'))"
            " ON CONFLICT(period) DO UPDATE SET status='funded',account_id=excluded.account_id,"
            " amount=excluded.amount,opening_balance=excluded.opening_balance,"
            " funded_at=datetime('now','localtime')",
            (period, payday["paycheck"], payday["date"], onepay["id"], split["net"], onepay["balance"]),
        )
        _audit(
            c, "finance.fund_paycheck", "high", True, period,
            {
                "accounts": {name: {"balance": accounts[name]["balance"]} for name in ("OnePay", "Truliant")},
                "retirement": [{"name": asset["name"], "balance": asset["balance_before"], "ytd": asset["ytd_before"]} for asset in payroll_assets],
            },
            {
                "distribution": preview["distribution"], "deposit": split["net"],
                "retirement": payroll_assets,
            },
            "Giovanni confirmed bank split and configured payroll retirement contributions; Relay unchanged",
        )
        return {"ok": True, "funded": preview}


@router.post("/paychecks/{period}/close")
def close_paycheck(period: str, body: ClosePaycheckIn):
    with conn() as c:
        cycle = c.execute("SELECT pc.*,a.balance FROM paycheck_cycles pc JOIN accounts a ON a.id=pc.account_id WHERE pc.period=?", (period,)).fetchone()
        if cycle is None or cycle["status"] != "funded":
            raise HTTPException(409, "paycheck must be funded before it can close")
        if not body.confirm:
            return {"ok": False, "requires_confirmation": True, "preview": {"period": period, "opening_balance": cycle["opening_balance"], "closing_balance": cycle["balance"]}}
        c.execute("UPDATE paycheck_cycles SET status='closed',closing_balance=?,closed_at=datetime('now','localtime') WHERE period=?", (cycle["balance"], period))
        _audit(c, "finance.close_paycheck", "medium", True, period, {"status": "funded"}, {"status": "closed", "closing_balance": cycle["balance"]}, "Giovanni confirmed cycle close")
        return {"ok": True, "period": period, "closing_balance": cycle["balance"]}


@router.post("/simulate")
def simulate_cashflow(body: SimulationIn):
    with conn() as c:
        result = _cashflow(c, body)
        _audit(c, "finance.simulate_cashflow", "none", False, "cashflow", {}, result["scenario"], "Read-only simulation")
        return result
