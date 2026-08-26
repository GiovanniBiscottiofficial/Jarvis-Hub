from datetime import date, timedelta

import pytest

from app.body_intelligence import (
    adaptive_targets,
    body_timeline,
    daily_loop,
    habit_insights,
    readiness_snapshot,
)
from app.db import active_profile, conn
from app.routers.bodyops import BodyCheckIn, body_checkin


def test_missing_health_data_is_neutral_not_failure(fresh_db):
    readiness = readiness_snapshot()
    assert 45 <= readiness["score"] <= 80
    assert readiness["confidence_percent"] == 0
    assert all("neutral credit" in item["reason"] for item in readiness["components"])
    assert readiness["medical_policy"].startswith("Wellness guidance")


def test_checkin_is_validated_and_changes_readiness(fresh_db):
    before = readiness_snapshot()
    result = body_checkin(
        BodyCheckIn(
            sleep_hours=8,
            sleep_quality=4,
            energy=5,
            mood=4,
            soreness=1,
            source="manual",
        )
    )
    assert result["ok"] is True
    assert result["readiness"]["score"] > before["score"]
    assert result["readiness"]["confidence_percent"] >= 50
    with pytest.raises(Exception):
        body_checkin(BodyCheckIn(energy=6))


def test_adaptive_targets_scale_movement_not_nutrition(fresh_db):
    low = {"score": 40}
    high = {"score": 90}
    low_targets = adaptive_targets(low)
    high_targets = adaptive_targets(high)
    assert low_targets["steps"] < high_targets["steps"]
    assert low_targets["protein_g"] == high_targets["protein_g"]
    assert low_targets["water_glasses"] == high_targets["water_glasses"]
    assert low_targets["calories"] == high_targets["calories"]


def test_weight_trend_and_timeline_preserve_sources(fresh_db):
    with conn() as c:
        pid = active_profile(c)["id"]
        c.execute(
            "INSERT INTO weighins(ts,weight_lb,profile_id,source) VALUES(?,?,?,?)",
            (date.today().isoformat() + " 07:00:00", 185.2, pid, "home_assistant:sensor.ihome_weight"),
        )
        c.execute(
            "INSERT INTO weighins(ts,weight_lb,profile_id,source) VALUES(?,?,?,?)",
            ((date.today() - timedelta(days=5)).isoformat() + " 07:00:00", 186.0, pid, "manual"),
        )
    readiness = readiness_snapshot()
    assert readiness["weights"]["windows"]["7"]["samples"] == 2
    assert readiness["weights"]["change_since_previous_lb"] == -0.8
    weight = next(item for item in body_timeline() if item["kind"] == "weight")
    assert weight["label"].startswith("home_assistant:")
    assert weight["quality"] == "measured"
    manual = next(item for item in body_timeline() if item["label"] == "manual")
    assert manual["quality"] == "self-reported"


def test_habits_use_logged_samples_and_explain_uncertainty(fresh_db):
    with conn() as c:
        pid = active_profile(c)["id"]
        for offset, protein in enumerate((40, 70, 90, 45, 100, 110)):
            day = (date.today() - timedelta(days=offset)).isoformat()
            c.execute(
                "INSERT INTO meal_log(ts,name,protein_g,profile_id) VALUES(?,?,?,?)",
                (day + " 18:00:00", "Test meal", protein, pid),
            )
    insights = habit_insights()
    protein = next(item for item in insights if item["metric"] == "protein")
    assert protein["samples"] == 6
    assert "logged" in protein["pattern"].lower()
    assert "missing entries are not assumed failures" in protein["caution"].lower()


def test_daily_loop_connects_readiness_chef_and_evening_review(fresh_db):
    loop = daily_loop()
    assert {"readiness", "targets", "morning", "evening", "timeline", "habits"} <= loop.keys()
    assert loop["morning"]["affirmation"]
    assert loop["data_policy"]["missing_is_not_failure"] is True
    assert loop["data_policy"]["medical_advice"] is False
