import pytest
from fastapi import HTTPException

from app.db import conn
from app.routers.bodyops import WeighIn, add_weighin, scale_readiness


def test_home_assistant_scale_reading_is_logged_with_source(fresh_db):
    result = add_weighin(
        WeighIn(weight_lb=182.4, source="home_assistant:sensor.ihome_weight")
    )
    assert result["ok"] is True
    with conn() as c:
        row = c.execute(
            "SELECT weight_lb, source FROM weighins ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["weight_lb"] == 182.4
    assert row["source"] == "home_assistant:sensor.ihome_weight"
    readiness = scale_readiness()
    assert readiness["configured"] is True
    assert readiness["automation_ready"] is True
    assert readiness["latest"]["source"] == "home_assistant:sensor.ihome_weight"


def test_duplicate_automatic_reading_is_suppressed(fresh_db):
    reading = WeighIn(
        weight_lb=182.4, source="home_assistant:sensor.ihome_weight"
    )
    add_weighin(reading)
    duplicate = add_weighin(reading)
    assert duplicate["duplicate"] is True
    with conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM weighins").fetchone()["n"] == 1


@pytest.mark.parametrize("weight", [0, 19.9, 1000.1])
def test_implausible_scale_reading_is_rejected(fresh_db, weight):
    with pytest.raises(HTTPException) as error:
        add_weighin(
            WeighIn(weight_lb=weight, source="home_assistant:sensor.bad_weight")
        )
    assert error.value.status_code == 400
