"""Giovanni's semi-monthly payday calendar.

Paycheck 1 is the month-end check and Paycheck 2 is the check nominally due
on the 15th. Payroll arrives two calendar days early after a weekend nominal
date is first moved to Friday. If the early date itself lands on a weekend,
it is moved back to Friday as well.
"""
from calendar import monthrange
from datetime import date, timedelta


NET_PAY = 2064.24


def _previous_friday(value: date) -> date:
    if value.weekday() == 5:  # Saturday
        return value - timedelta(days=1)
    if value.weekday() == 6:  # Sunday
        return value - timedelta(days=2)
    return value


def nominal_payday(year: int, month: int, paycheck: int) -> date:
    if paycheck == 1:
        return date(year, month, monthrange(year, month)[1])
    if paycheck == 2:
        return date(year, month, 15)
    raise ValueError("paycheck must be 1 or 2")


def actual_payday(year: int, month: int, paycheck: int) -> date:
    business_nominal = _previous_friday(nominal_payday(year, month, paycheck))
    return _previous_friday(business_nominal - timedelta(days=2))


def _month_offset(value: date, offset: int) -> tuple[int, int]:
    index = value.year * 12 + value.month - 1 + offset
    return index // 12, index % 12 + 1


def payday_schedule(today: date | None = None, count: int = 6) -> list[dict]:
    today = today or date.today()
    candidates = []
    for offset in range(-1, 8):
        year, month = _month_offset(today, offset)
        for paycheck in (2, 1):
            nominal = nominal_payday(year, month, paycheck)
            actual = actual_payday(year, month, paycheck)
            if actual >= today:
                candidates.append((actual, nominal, paycheck))
    candidates.sort(key=lambda item: item[0])
    return [
        {
            "paycheck": paycheck,
            "label": f"Paycheck {paycheck}",
            "amount": NET_PAY,
            "date": actual.isoformat(),
            "nominal_date": nominal.isoformat(),
            "days_away": (actual - today).days,
            "period": f"{nominal:%Y-%m}-P{paycheck}",
        }
        for actual, nominal, paycheck in candidates[: max(1, count)]
    ]


def upcoming_period(today: date | None = None) -> dict:
    payday = payday_schedule(today, 1)[0]
    return {
        "month": payday["nominal_date"][:7],
        "paycheck": payday["paycheck"],
        "key": payday["period"],
        "payday": payday["date"],
        "days_away": payday["days_away"],
    }


def scheduled_bill_due_date(
    paycheck: int, due_day: int, today: date | None = None
) -> date:
    """Return the due date funded by the next matching paycheck.

    A month-end Paycheck 1 funds bills due in the following month. Paycheck 2
    funds bills due in its nominal month. This avoids treating a recurring
    bill due on the 1st as overdue before the upcoming month-end check arrives.
    """
    today = today or date.today()
    payday = next(
        item
        for item in payday_schedule(today, 6)
        if item["paycheck"] == paycheck
    )
    nominal = date.fromisoformat(payday["nominal_date"])
    year, month = nominal.year, nominal.month
    if paycheck == 1:
        year, month = _month_offset(nominal, 1)
    day = min(max(1, int(due_day)), monthrange(year, month)[1])
    return date(year, month, day)
