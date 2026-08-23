from datetime import date

from app.paydays import (
    NET_PAY,
    actual_payday,
    payday_schedule,
    scheduled_bill_due_date,
    upcoming_period,
)


def test_saturday_fifteenth_moves_to_friday_then_two_days_early():
    # August 15, 2026 is Saturday: nominal -> Friday 14th -> Wednesday 12th.
    assert actual_payday(2026, 8, 2) == date(2026, 8, 12)


def test_early_date_that_lands_on_weekend_moves_to_friday():
    # August 31, 2026 is Monday; two days early is Saturday, so pay is Friday.
    assert actual_payday(2026, 8, 1) == date(2026, 8, 28)


def test_coming_pay_is_paycheck_one_and_amount_is_exact():
    schedule = payday_schedule(date(2026, 8, 23), 3)
    assert schedule[0] == {
        "paycheck": 1,
        "label": "Paycheck 1",
        "amount": 2064.24,
        "date": "2026-08-28",
        "nominal_date": "2026-08-31",
        "days_away": 5,
        "period": "2026-08-P1",
    }
    assert schedule[1]["paycheck"] == 2
    assert schedule[1]["date"] == "2026-09-11"
    assert NET_PAY == 2064.24


def test_upcoming_period_follows_payroll_order():
    assert upcoming_period(date(2026, 8, 23))["key"] == "2026-08-P1"
    assert upcoming_period(date(2026, 8, 29))["key"] == "2026-09-P2"


def test_bill_due_dates_begin_with_the_upcoming_first_pay_cycle():
    today = date(2026, 8, 23)
    assert scheduled_bill_due_date(1, 1, today) == date(2026, 9, 1)
    assert scheduled_bill_due_date(1, 28, today) == date(2026, 8, 28)
    assert scheduled_bill_due_date(2, 15, today) == date(2026, 9, 15)


def test_vault_plan_never_infers_overdue_from_day_of_month(fresh_db, monkeypatch):
    from app.routers import vaultflow

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 23)

    monkeypatch.setattr(vaultflow, "date", FixedDate)
    result = vaultflow.plan()
    assert result["cycle_starts"] == "2026-08-28"
    assert all("overdue" not in item["status"] for item in result["recommendations"])
    assert all(item["due_date"] >= "2026-08-23" for item in result["recommendations"])


def test_commissioned_bills_and_balances_match_giovanni_plan(fresh_db):
    from app.db import conn
    from app.paydays import payday_schedule

    schedule = payday_schedule(count=3)
    with conn() as c:
        accounts = {
            row["name"]: row["balance"]
            for row in c.execute("SELECT name,balance FROM accounts")
        }
        bills = {
            row["name"]: dict(row)
            for row in c.execute("SELECT * FROM bills")
        }
    assert accounts["Truliant"] == -25.73
    assert accounts["Relay"] == 0
    assert accounts["OnePay Savings"] == 0
    assert bills["Spectrum Internet"]["amount"] == 93.95
    assert bills["Spectrum Internet"]["due_day"] == 28
    assert bills["Spectrum Internet"]["paycheck"] == 1
    assert bills["Apartment Water Activation"]["amount"] == 90
    assert bills["Apartment Water Activation"]["start_period"] == schedule[0]["period"]
    for name in ("Klarna Statement", "Duke Energy Payment Plan", "Old Spectrum Paydown"):
        assert bills[name]["start_period"] == schedule[2]["period"]
        assert bills[name]["paycheck"] == 1


def test_reconcile_accepts_real_negative_account_balance(fresh_db):
    from app.db import conn
    from app.routers import vaultflow

    with conn() as c:
        account_id = c.execute(
            "SELECT id FROM accounts WHERE name='Truliant'"
        ).fetchone()["id"]
    assert vaultflow.set_balance(
        account_id, vaultflow.BalanceIn(balance=-25.73)
    ) == {"ok": True}


def test_budget_overview_exposes_countdowns_and_corrected_net(fresh_db):
    from app.routers import budget

    overview = budget.overview()
    assert overview["paycheck_in"]["net"] == 2064.24
    assert sum(
        overview["paycheck_in"][key] for key in ("onepay", "truliant")
    ) == 2064.24
    assert len(overview["paydays"]) == 4
    assert {item["paycheck"] for item in overview["paydays"][:2]} == {1, 2}
    assert overview["protected_cash"] == 0


def test_new_budget_cycle_is_scheduled_not_overdue(fresh_db):
    from app.db import set_setting
    from app.routers import budget

    set_setting("budget_cycle_start", "2999-08-28")
    overview = budget.overview()
    assert overview["audit"] == "scheduled"
    assert overview["cycle_pending"] is True
    assert "No bill is overdue before that deposit" in overview["audit_note"]


def test_vault_bills_are_grouped_by_paycheck(fresh_db):
    from app.routers import vaultflow

    bills = vaultflow.list_bills()
    paychecks = [bill["paycheck"] for bill in bills]
    assert paychecks == sorted(paychecks)


def test_bill_payment_records_the_selected_paycheck_period(fresh_db):
    from app.db import conn
    from app.routers import budget

    with conn() as c:
        bill_id = c.execute(
            "SELECT id FROM bills WHERE name='Spectrum Internet'"
        ).fetchone()["id"]
    period = payday_schedule(count=1)[0]["period"]
    result = budget.mark_bill_paid(bill_id, period)
    assert result["period"] == period
    with conn() as c:
        row = c.execute(
            "SELECT paid_period FROM bills WHERE id=?", (bill_id,)
        ).fetchone()
    assert row["paid_period"] == period
