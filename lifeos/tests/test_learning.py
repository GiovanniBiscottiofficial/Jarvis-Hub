from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.context_engine import list_proposals


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH = {"Authorization": "Bearer test-token"}


def _client():
    return TestClient(main.app, headers=AUTH)


def _feedback(client, **overrides):
    payload = {
        "domain": "home",
        "subject": "evening lighting",
        "value": "keep the hallway dark after 9 PM",
        "signal": "stated",
        "source": "lifeos_ui",
        "context": {"surface": "test"},
    }
    payload.update(overrides)
    return client.post("/api/learning/feedback", json=payload)


def test_explicit_evidence_creates_action_locked_candidate(fresh_db):
    client = _client()
    before_proposals = list_proposals()
    response = _feedback(client)

    assert response.status_code == 200
    result = response.json()
    assert result["acted_on"] is False
    assert result["preference"]["status"] == "candidate"
    assert result["preference"]["sentiment"] == "prefer"
    assert result["preference"]["evidence_count"] == 1
    assert list_proposals() == before_proposals

    ledger = client.get("/api/learning").json()
    assert ledger["summary"]["candidate"] == 1
    assert ledger["policy"]["inferences_authorize_actions"] is False
    assert ledger["policy"]["explicit_evidence_only"] is True
    assert "automatic_patterns" in ledger
    assert ledger["automatic_patterns"]["policy"]["patterns_authorize_actions"] is False


def test_repeated_consistent_feedback_increases_confidence(fresh_db):
    client = _client()
    first = _feedback(client, signal="liked").json()["preference"]
    second = _feedback(client, signal="chosen").json()["preference"]
    third = _feedback(client, signal="liked").json()["preference"]

    assert first["confidence"] < second["confidence"] < third["confidence"]
    assert third["evidence_count"] == 3
    assert "92% agreement" in third["reason"]


def test_giovanni_can_confirm_reject_and_forget_learning(fresh_db):
    client = _client()
    preference = _feedback(client).json()["preference"]
    confirmed = client.post(
        f"/api/learning/preferences/{preference['id']}/decision",
        json={"decision": "confirm", "reason": "Giovanni approved this guidance."},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["preference"]["status"] == "confirmed"

    forgotten = client.post(
        f"/api/learning/preferences/{preference['id']}/decision",
        json={"decision": "forget", "reason": "Giovanni changed his mind."},
    )
    assert forgotten.status_code == 200
    ledger = client.get("/api/learning").json()
    assert ledger["preferences"] == []
    assert ledger["recent_observations"] == []
    assert [entry["action"] for entry in ledger["audit"]][:2] == [
        "forgotten",
        "confirmed",
    ]


def test_negative_correction_is_exposed_as_avoidance(fresh_db):
    client = _client()
    preference = _feedback(
        client,
        value="bright hallway lights after 9 PM",
        signal="corrected",
    ).json()["preference"]
    assert preference["sentiment"] == "avoid"
    assert preference["status"] == "candidate"

    rejected = client.post(
        f"/api/learning/preferences/{preference['id']}/decision",
        json={"decision": "reject"},
    ).json()["preference"]
    assert rejected["status"] == "rejected"
    reconsidered = client.post(
        f"/api/learning/preferences/{preference['id']}/decision",
        json={"decision": "reconsider"},
    ).json()["preference"]
    assert reconsidered["status"] == "candidate"


def test_contradictory_evidence_cannot_silently_flip_confirmed_guidance(fresh_db):
    client = _client()
    _feedback(client, signal="liked")
    preference = _feedback(client, signal="chosen").json()["preference"]
    client.post(
        f"/api/learning/preferences/{preference['id']}/decision",
        json={"decision": "confirm"},
    )
    challenged = _feedback(client, signal="corrected").json()["preference"]
    assert challenged["sentiment"] == "prefer"
    assert challenged["status"] == "confirmed"

    challenged = _feedback(client, signal="corrected").json()["preference"]
    assert challenged["sentiment"] == "avoid"
    assert challenged["status"] == "candidate"


def test_voice_memory_is_confirmed_learning_and_forget_is_shared(fresh_db):
    client = _client()
    remembered = client.post(
        "/api/memory", json={"fact": "my parking space is 22B"}
    )
    assert remembered.status_code == 200
    preferences = client.get("/api/learning").json()["preferences"]
    assert len(preferences) == 1
    assert preferences[0]["domain"] == "memory"
    assert preferences[0]["status"] == "confirmed"

    assert client.post("/api/memory/forget").json()["ok"] is True
    assert client.get("/api/learning").json()["preferences"] == []


def test_chef_feedback_feeds_learning_without_auto_confirmation(fresh_db):
    client = _client()
    response = client.post(
        "/api/pantry/chef/feedback",
        json={"recipe_id": "blackened-chicken-tenders", "action": "liked"},
    )
    assert response.status_code == 200
    preference = client.get("/api/learning").json()["preferences"][0]
    assert preference["domain"] == "food"
    assert preference["subject"] == "recipe"
    assert preference["value"] == "blackened-chicken-tenders"
    assert preference["status"] == "candidate"


def test_context_surfaces_learning_summary(fresh_db):
    client = _client()
    _feedback(client)
    context = client.get("/api/context").json()
    command = client.get("/api/command-center").json()
    assert context["learning"]["summary"]["candidate"] == 1
    assert command["context"]["learning"]["policy"]["local_only"] is True


def test_learning_ui_is_touch_safe_and_uses_safe_dom():
    index = (REPO_ROOT / "lifeos/app/static/index.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "lifeos/app/static/app.js").read_text(encoding="utf-8")
    styles = (REPO_ROOT / "lifeos/app/static/style.css").read_text(encoding="utf-8")
    for element_id in (
        "learning-status",
        "learning-candidates",
        "learning-confirmed",
        "learning-rejected",
        "learning-evidence",
        "learning-patterns",
        "learning-pattern-coverage",
        "learning-pattern-count",
        "learning-submit",
    ):
        assert f'id="{element_id}"' in index
        assert f'"{element_id}"' in script
    learning_script = script.split("// ---------- Learning Ledger ----------", 1)[1].split(
        "// ---------- Review + profiles ----------", 1
    )[0]
    assert "innerHTML" not in learning_script
    assert ".learning-actions button { min-height: 44px; }" in styles
    assert "Inferences cannot authorize Home Assistant actions" in index
