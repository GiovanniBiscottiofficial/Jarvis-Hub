import json

import pytest
from fastapi import HTTPException

from app.db import conn
from app.paydays import payday_schedule
from app.routers.money import (
    ClosePaycheckIn,
    FundPaycheckIn,
    ImportIn,
    ReconcileIn,
    SimulationIn,
    TransactionReviewIn,
    close_paycheck,
    command_center,
    fund_paycheck,
    import_statement,
    reconcile_account,
    review_transaction,
    simulate_cashflow,
)


def first_account():
    with conn() as c:
        return dict(c.execute("SELECT * FROM accounts ORDER BY id LIMIT 1").fetchone())


def test_command_center_only_exposes_commissioned_accounts(fresh_db):
    center = command_center()
    names = {account["name"] for account in center["accounts"]}
    assert names == {"OnePay", "Truliant", "Relay"}


def test_phone_reconnection_appears_once_on_third_upcoming_pay(fresh_db):
    missions = command_center()["paycheck_missions"]
    phone_by_mission = [
        [bill["name"] for bill in mission["bills"] if "Phone" in bill["name"]]
        for mission in missions
    ]
    assert phone_by_mission[0] == []
    assert phone_by_mission[1] == []
    assert phone_by_mission[2] == ["Phone Reconnection"]
    assert all("Phone Balance Arrangement" not in names for names in phone_by_mission)


def test_csv_import_is_pending_and_deduplicated(fresh_db):
    account = first_account()
    statement = "Date,Description,Amount\n2026-08-20,Food Lion,-42.18\n2026-08-21,Payroll,2064.24\n"
    first = import_statement(ImportIn(content=statement, account_id=account["id"]))
    second = import_statement(ImportIn(content=statement, account_id=account["id"]))
    assert first == {"ok": True, "format": "csv", "imported": 2, "duplicates": 0, "pending_review": 2}
    assert second["imported"] == 0
    assert second["duplicates"] == 2
    center = command_center()
    assert center["pending_count"] == 2
    assert center["transaction_inbox"][0]["status"] == "pending"


def test_verification_requires_confirmation_and_posts_once(fresh_db):
    account = first_account()
    starting = account["balance"]
    import_statement(ImportIn(
        content="Date,Description,Amount\n2026-08-20,Food Lion,-42.18\n",
        account_id=account["id"],
    ))
    transaction_id = command_center()["transaction_inbox"][0]["id"]
    preview = review_transaction(transaction_id, TransactionReviewIn(decision="verify"))
    assert preview["requires_confirmation"] is True
    assert first_account()["balance"] == starting
    verified = review_transaction(transaction_id, TransactionReviewIn(decision="verify", confirm=True))
    assert verified["ok"] is True
    assert first_account()["balance"] == pytest.approx(starting - 42.18)
    with conn() as c:
        spending = c.execute("SELECT * FROM spending WHERE merchant='Food Lion'").fetchone()
        audit = c.execute("SELECT * FROM financial_action_audit WHERE action='finance.verify_transaction'").fetchone()
    assert spending["amount"] == 42.18
    assert audit["confirmed"] == 1
    with pytest.raises(HTTPException) as error:
        review_transaction(transaction_id, TransactionReviewIn(decision="verify", confirm=True))
    assert error.value.status_code == 409


def test_reconciliation_preview_then_confirm_preserves_difference(fresh_db):
    account = first_account()
    actual = -25.73
    preview = reconcile_account(account["id"], ReconcileIn(actual_balance=actual))
    assert preview["requires_confirmation"] is True
    assert first_account()["balance"] == account["balance"]
    result = reconcile_account(account["id"], ReconcileIn(actual_balance=actual, reason="Bank app balance", confirm=True))
    assert result["reconciliation"]["difference"] == pytest.approx(actual - account["balance"])
    assert first_account()["balance"] == actual
    with conn() as c:
        row = c.execute("SELECT * FROM account_reconciliations").fetchone()
    assert row["actual_balance"] == actual
    assert row["confirmed_by"] == "Giovanni"


def test_paycheck_funding_and_close_are_confirmed_and_idempotent(fresh_db):
    payday = payday_schedule(count=1)[0]
    with conn() as c:
        before = {row["name"]: row["balance"] for row in c.execute("SELECT name,balance FROM accounts")}
    preview = fund_paycheck(payday["period"], FundPaycheckIn())
    assert preview["requires_confirmation"] is True
    assert preview["preview"]["distribution"]["Truliant"]["deposit"] == 309.00
    assert preview["preview"]["distribution"]["OnePay"]["deposit"] == 1755.24
    assert preview["preview"]["distribution"]["Relay"]["deposit"] == 0
    funded = fund_paycheck(payday["period"], FundPaycheckIn(confirm=True))
    assert funded["funded"]["amount"] == 2064.24
    with conn() as c:
        after = {row["name"]: row["balance"] for row in c.execute("SELECT name,balance FROM accounts")}
        deposits = [dict(row) for row in c.execute("SELECT amount,account_id FROM deposits WHERE source=?", (f"paycheck_funding:{payday['period']}",))]
    assert after["Truliant"] == pytest.approx(before["Truliant"] + 309.00)
    assert after["OnePay"] == pytest.approx(before["OnePay"] + 1755.24)
    assert after["Relay"] == before["Relay"]
    assert sorted(row["amount"] for row in deposits) == [309.00, 1755.24]
    with pytest.raises(HTTPException) as duplicate:
        fund_paycheck(payday["period"], FundPaycheckIn(confirm=True))
    assert duplicate.value.status_code == 409
    close_preview = close_paycheck(payday["period"], ClosePaycheckIn())
    assert close_preview["requires_confirmation"] is True
    closed = close_paycheck(payday["period"], ClosePaycheckIn(confirm=True))
    assert closed["ok"] is True


def test_simulation_never_changes_money_state(fresh_db):
    before = command_center()
    result = simulate_cashflow(SimulationIn(one_time_spending=250, daily_spending_change=5, periods=4))
    after = command_center()
    assert len(result["forecast"]) == 4
    assert result["policy"].startswith("Simulation only")
    assert [a["balance"] for a in before["accounts"]] == [a["balance"] for a in after["accounts"]]
    assert [m["status"] for m in before["paycheck_missions"]] == [m["status"] for m in after["paycheck_missions"]]
    with conn() as c:
        audit = c.execute("SELECT * FROM financial_action_audit WHERE action='finance.simulate_cashflow'").fetchone()
    assert json.loads(audit["after_json"])["one_time_spending"] == 250
