from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.routers import insights
from app.routers.insights import weekly_review


AUTH = {"Authorization": "Bearer test-token"}


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


def test_local_speech_returns_piper_audio_and_enforces_address_policy(
    fresh_db, monkeypatch
):
    captured = []

    async def fake_piper(text):
        captured.append(text)
        return b"RIFF-test-wave"

    monkeypatch.setattr(insights, "_piper_wav", fake_piper)
    response = TestClient(app).post(
        "/api/speech/local",
        headers=AUTH,
        json={"text": "  Weekly brief, sir.  "},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"RIFF-test-wave"
    assert captured == ["Weekly brief, Giovanni."]


def test_local_speech_rejects_oversized_copy_without_calling_piper(
    fresh_db, monkeypatch
):
    async def fail_if_called(_text):
        raise AssertionError("Piper must not run for rejected input")

    monkeypatch.setattr(insights, "_piper_wav", fail_if_called)
    response = TestClient(app).post(
        "/api/speech/local",
        headers=AUTH,
        json={"text": "x" * 6001},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "speech text is too long"
