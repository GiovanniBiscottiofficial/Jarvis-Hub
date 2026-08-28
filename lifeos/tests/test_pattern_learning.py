from datetime import date, timedelta

from app.db import conn
from app.pattern_learning import pattern_snapshot, record_commute_observation


def _patterns_by_id():
    return {item["id"]: item for item in pattern_snapshot()["patterns"]}


def test_cross_domain_patterns_are_evidence_backed_and_advisory(fresh_db):
    today = date.today()
    with conn() as database:
        for offset, minutes in enumerate((28, 31, 29)):
            sample_date = (today - timedelta(days=offset)).isoformat()
            database.execute(
                "INSERT INTO commute_history(date,profile_id,minutes,miles,source,traffic_live,planned_departure)"
                " VALUES(?,?,?,?,?,?,?)",
                (sample_date, 1, minutes, 26.5, "Waze", 1, "7:19 AM"),
            )
            database.execute(
                "INSERT INTO meal_log(ts,name,protein_g,profile_id) VALUES(?,?,?,?)",
                (f"{sample_date} 18:15:00", "Atkins shake", 15, 1),
            )
            database.execute(
                "INSERT INTO steps(date,profile_id,count) VALUES(?,?,?)",
                (sample_date, 1, 7000 + offset * 500),
            )
            database.execute(
                "INSERT INTO spending(ts,amount,merchant) VALUES(?,?,?)",
                (f"{sample_date} 12:00:00", 12 + offset, "Publix"),
            )
            database.execute(
                "INSERT INTO context_events(ts,source,event_type,entity_id,state,previous_state)"
                " VALUES(?,?,?,?,?,?)",
                (f"{sample_date} 17:32:00", "home_assistant", "state_changed", "person.giovanni", "home", "not_home"),
            )

    patterns = _patterns_by_id()
    for pattern_id in (
        "commute:home_to_work",
        "food:atkins shake",
        "body:steps",
        "finance:publix",
        "routine:arrival",
    ):
        assert patterns[pattern_id]["status"] == "established"
        assert patterns[pattern_id]["sample_count"] >= 3
        assert patterns[pattern_id]["authority"] == "advisory_only"

    policy = pattern_snapshot()["policy"]
    assert policy["patterns_authorize_actions"] is False
    assert policy["raw_audio_stored"] is False
    assert policy["camera_frames_stored"] is False
    assert policy["identity_recognition"] is False
    assert policy["exact_location_history_stored"] is False


def test_pattern_stays_emerging_until_three_samples(fresh_db):
    with conn() as database:
        for offset in range(2):
            sample_date = (date.today() - timedelta(days=offset)).isoformat()
            database.execute(
                "INSERT INTO meal_log(ts,name,protein_g,profile_id) VALUES(?,?,?,?)",
                (f"{sample_date} 12:00:00", "Tuna", 30, 1),
            )
    assert _patterns_by_id()["food:tuna"]["status"] == "emerging"


def test_commute_recording_revises_same_day_without_location_history(fresh_db):
    record_commute_observation(1, {
        "minutes": 31.4,
        "miles": 26.6,
        "source": "Waze",
        "traffic_live": True,
        "planned_departure": "7:19 AM",
    })
    record_commute_observation(1, {
        "minutes": 34.0,
        "miles": 26.6,
        "source": "Waze",
        "traffic_live": True,
        "planned_departure": "7:16 AM",
    })
    with conn() as database:
        rows = database.execute("SELECT * FROM commute_history").fetchall()
    assert len(rows) == 1
    assert rows[0]["minutes"] == 34.0
    assert rows[0]["planned_departure"] == "7:16 AM"
