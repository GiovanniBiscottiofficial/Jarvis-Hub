from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.db import conn
from app.routers.bodyops import MealLogIn, log_meal


def test_recent_meal_can_be_backdated_with_audit(fresh_db):
    yesterday = datetime.now() - timedelta(days=1)
    result = log_meal(MealLogIn(
        name="3 boiled eggs", protein_g=18, calories=234,
        logged_at=yesterday.replace(hour=20, minute=0, second=0).isoformat(),
    ))
    assert result["ok"] is True
    with conn() as database:
        meal = database.execute("SELECT * FROM meal_log WHERE name='3 boiled eggs'").fetchone()
        event = database.execute(
            "SELECT attributes_json FROM context_events WHERE event_type='body.meal_logged'"
        ).fetchone()
    assert meal["ts"].startswith((date.today() - timedelta(days=1)).isoformat())
    assert '"backdated": true' in event["attributes_json"]


def test_old_or_future_meal_cannot_be_backdated(fresh_db):
    with pytest.raises(HTTPException):
        log_meal(MealLogIn(name="old", logged_at=(datetime.now() - timedelta(days=8)).isoformat()))
    with pytest.raises(HTTPException):
        log_meal(MealLogIn(name="future", logged_at=(datetime.now() + timedelta(days=1)).isoformat()))
