from datetime import date, timedelta

from app.routers.insights import weekly_review


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def test_weekly_intelligence_is_conservative_when_evidence_is_missing(fresh_db):
    review = weekly_review()

    assert review["period_start"] == day(-6)
    assert review["period_end"] == day(0)
    assert review["confidence"]["label"] == "limited"
    assert review["confidence"]["score"] == 0
    assert review["operating_score"] is None
    assert review["verdict"].startswith("Evidence incomplete")
    assert review["weight"]["delta_lb"] is None
    assert any(item["title"] == "Close the data gaps" for item in review["priorities"])
    assert "Evidence is limited" in review["speech"]
    assert "Giovanni" in review["speech"]
    assert "sir" not in review["speech"].lower()


def test_weekly_intelligence_compares_periods_and_cites_recommendations(fresh_db):
    from app import db

    with db.conn() as connection:
        for offset in range(-13, 1):
            current = offset >= -6
            protein = 120 if current else 80
            steps = 9000 if current else 6000
            connection.execute(
                "INSERT INTO meal_log(ts,name,protein_g,profile_id) VALUES(?,?,?,1)",
                (f"{day(offset)} 08:00:00", "protein anchor", protein),
            )
            connection.execute(
                "INSERT INTO steps(date,profile_id,count) VALUES(?,1,?)",
                (day(offset), steps),
            )
            connection.execute(
                "INSERT INTO vitamins(date,profile_id,taken) VALUES(?,1,?)",
                (day(offset), 1 if current else 0),
            )
        connection.execute(
            "INSERT INTO weighins(ts,weight_lb,profile_id) VALUES(?,?,1)",
            (f"{day(-6)} 07:00:00", 190),
        )
        connection.execute(
            "INSERT INTO weighins(ts,weight_lb,profile_id) VALUES(?,?,1)",
            (f"{day(0)} 07:00:00", 188.5),
        )
        for offset in (-5, -3, -1):
            connection.execute(
                "INSERT INTO workouts(ts,kind,minutes,profile_id) VALUES(?,?,?,1)",
                (f"{day(offset)} 18:00:00", "strength", 20),
            )

    review = weekly_review()

    assert review["confidence"]["label"] == "strong"
    assert review["confidence"]["score"] == 100
    assert review["trends"]["protein"]["direction"] == "up"
    assert review["trends"]["steps"]["direction"] == "up"
    assert review["target_days"] == {"protein": 7, "steps": 7, "vitamins": 7}
    assert review["weight"]["delta_lb"] == -1.5
    assert any(item["title"] == "Protein rhythm held" for item in review["wins"])
    assert any(item["title"] == "Training cadence is on line" for item in review["wins"])
    assert review["priorities"]
    assert all(set(item) == {"domain", "title", "evidence"} for item in review["priorities"])
    assert review["policy"].startswith("Recommendations are advisory")
