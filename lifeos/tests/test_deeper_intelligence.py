from copy import deepcopy
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.context_engine import command_center_payload
from app.db import conn
from app.intelligence import build_intelligence, simulate_intelligence, temporal_patterns
from app.main import app
from app.routers.insights import compose_briefing

REPO_ROOT = Path(__file__).resolve().parents[2]


def _snapshot():
    return {
        "house_mode": "normal",
        "telemetry": {
            "link_state": "online",
            "last_event_at": "2026-08-26 18:00:00",
        },
        "occupancy": {"occupied": True, "people_home": ["giovanni"]},
        "security": {
            "secure": True,
            "open_perimeter": [],
            "active_hazards": [],
        },
        "sanctuary": {"mode": "Home Base", "manual_hold": False},
        "perception": {
            "link_state": "online",
            "room_occupied": True,
            "confidence": 0.91,
            "last_observation_at": "2026-08-26 18:00:00",
        },
        "voice": {"ready": True},
        "lifeos": {
            "body": {
                "protein_g": 85,
                "protein_target_g": 100,
                "vitamins_taken": True,
            },
            "vault": {"bills_due_soon": [], "left_after_due_bills": 500},
            "food": {
                "pantry_item_count": 12,
                "out_of_stock": [],
                "low_stock": [],
                "market_list": [],
            },
        },
    }


def test_nominal_picture_has_no_invented_urgency():
    result = build_intelligence(
        _snapshot(), now=datetime(2026, 8, 26, 18, 30)
    )

    assert result["status"] == "nominal"
    assert result["summary"]["urgent"] == 0
    assert result["headline"] == "No cross-domain conflicts detected"
    assert result["policy"]["inferences_authorize_actions"] is False


def test_away_mode_and_presence_create_explainable_conflict():
    snapshot = _snapshot()
    snapshot["sanctuary"]["mode"] = "Away"

    result = build_intelligence(
        snapshot, now=datetime(2026, 8, 26, 18, 30)
    )

    conflict = next(
        item for item in result["conflicts"]
        if item["id"] == "away_presence_conflict"
    )
    recommendation = next(
        item for item in result["recommendations"]
        if item["id"] == "resolve_away_presence"
    )
    assert conflict["severity"] == "high"
    assert {item["source"] for item in conflict["evidence"]} >= {
        "sanctuary", "home_assistant", "x1_vision"
    }
    assert recommendation["authority"]["can_execute"] is False


def test_body_food_and_finance_are_prioritized_together():
    snapshot = _snapshot()
    snapshot["lifeos"]["body"]["protein_g"] = 35
    snapshot["lifeos"]["food"].update({
        "pantry_item_count": 4,
        "out_of_stock": ["tuna"],
        "low_stock": ["protein shakes"],
    })
    snapshot["lifeos"]["vault"].update({
        "bills_due_soon": [{"name": "Spectrum", "amount": 93.95}],
        "left_after_due_bills": -42.50,
    })

    result = build_intelligence(
        snapshot, now=datetime(2026, 8, 26, 19, 0)
    )

    assert [item["id"] for item in result["recommendations"]][:2] == [
        "review_bill_runway", "plan_protein_dinner"
    ]
    finance = result["recommendations"][0]
    assert finance["action_id"] == "finance.simulate_cashflow"
    assert finance["authority"]["mode"] == "advisory_only"


def test_stale_telemetry_lowers_confidence_and_blocks_authority_claims():
    snapshot = _snapshot()
    snapshot["telemetry"]["link_state"] = "stale"

    result = build_intelligence(snapshot)

    assert result["status"] == "degraded"
    assert result["data_quality"]["fresh"] is False
    assert result["confidence"] <= 0.35
    assert result["policy"]["stale_data_can_authorize_actions"] is False
    assert result["recommendations"][0]["id"] == "restore_context_link"


def test_temporal_engine_requires_evidence_and_flags_a_real_deviation():
    snapshot = _snapshot()
    snapshot["recent_events"] = [
        {"ts": "2026-08-20 07:30:00", "entity_id": "person.giovanni", "state": "not_home", "previous_state": "home"},
        {"ts": "2026-08-21 07:35:00", "entity_id": "person.giovanni", "state": "not_home", "previous_state": "home"},
        {"ts": "2026-08-22 07:40:00", "entity_id": "person.giovanni", "state": "not_home", "previous_state": "home"},
        {"ts": "2026-08-26 12:30:00", "entity_id": "person.giovanni", "state": "not_home", "previous_state": "home"},
    ]

    temporal = temporal_patterns(snapshot, now=datetime(2026, 8, 26, 12, 31))

    assert temporal["summary"] == {"candidates": 1, "established": 1, "deviations": 1}
    pattern = temporal["patterns"][0]
    assert pattern["kind"] == "departure"
    assert pattern["sample_count"] == 4
    assert pattern["usual_window"] == {"start": "07:08", "end": "08:08"}
    assert temporal["deviations"][0]["minutes_from_usual"] == 292
    assert temporal["policy"]["patterns_authorize_actions"] is False


def test_two_observations_remain_a_candidate_not_a_learned_routine():
    snapshot = _snapshot()
    snapshot["recent_events"] = [
        {"ts": "2026-08-20 17:30:00", "entity_id": "person.giovanni", "state": "home", "previous_state": "not_home"},
        {"ts": "2026-08-21 17:40:00", "entity_id": "person.giovanni", "state": "home", "previous_state": "not_home"},
    ]

    temporal = temporal_patterns(snapshot)

    assert temporal["patterns"][0]["established"] is False
    assert temporal["deviations"] == []


def test_counterfactual_explains_changes_without_mutating_input():
    snapshot = _snapshot()
    before = deepcopy(snapshot)

    result = simulate_intelligence(
        snapshot,
        {
            "sanctuary_mode": "Away",
            "occupied": True,
            "people_home": ["giovanni"],
            "visual_presence": True,
            "visual_confidence": 0.9,
        },
        now=datetime(2026, 8, 26, 18, 30),
    )

    assert snapshot == before
    assert "resolve_away_presence" in result["changes"]["added_recommendations"]
    assert result["assessment"]["conflicts"][0]["id"] == "away_presence_conflict"
    assert result["predicted_actions"] == []
    assert result["house_state_mutated"] is False
    assert result["database_mutated"] is False
    assert result["action_execution"] is False


def test_counterfactual_rejects_unbounded_override_fields():
    try:
        simulate_intelligence(_snapshot(), {"execute_service": "lock.unlock"})
    except ValueError as error:
        assert "unsupported intelligence override" in str(error)
    else:
        raise AssertionError("unsafe override was accepted")


def test_learning_guides_ranking_without_authorizing_actions():
    learning = {
        "preferences": [
            {"status": "confirmed", "domain": "food", "value": "tuna"},
            {"status": "candidate", "domain": "home", "value": "dim lights"},
        ]
    }
    result = build_intelligence(_snapshot(), learning=learning)

    signal = next(
        item for item in result["signals"]
        if item["id"] == "confirmed_guidance_loaded"
    )
    assert "1 confirmed preference" in signal["detail"]
    assert result["policy"]["confirmed_preferences_are_guidance_only"] is True


def test_intelligence_api_is_read_only_and_command_center_includes_it(fresh_db):
    client = TestClient(app, headers={"Authorization": "Bearer test-token"})
    with conn() as c:
        before = {
            "events": c.execute("SELECT COUNT(*) n FROM context_events").fetchone()["n"],
            "proposals": c.execute("SELECT COUNT(*) n FROM action_proposals").fetchone()["n"],
            "audit": c.execute("SELECT COUNT(*) n FROM action_audit").fetchone()["n"],
        }

    response = client.get("/api/intelligence")
    assert response.status_code == 200
    assert response.json()["policy"]["read_only"] is True
    assert "intelligence" in command_center_payload()
    simulation = client.post("/api/simulations/intelligence", json={"overrides": {}})
    assert simulation.status_code == 200
    assert simulation.json()["behavior"] == "intelligence"
    assert simulation.json()["predicted_actions"] == []
    assert simulation.json()["database_mutated"] is False

    with conn() as c:
        after = {
            "events": c.execute("SELECT COUNT(*) n FROM context_events").fetchone()["n"],
            "proposals": c.execute("SELECT COUNT(*) n FROM action_proposals").fetchone()["n"],
            "audit": c.execute("SELECT COUNT(*) n FROM action_audit").fetchone()["n"],
        }
    assert after == before


def test_decision_support_ui_is_safe_responsive_and_exposes_policy():
    index = (REPO_ROOT / "lifeos/app/static/index.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "lifeos/app/static/app.js").read_text(encoding="utf-8")
    styles = (REPO_ROOT / "lifeos/app/static/style.css").read_text(encoding="utf-8")

    for element_id in (
        "intelligence-state",
        "intelligence-headline",
        "intelligence-confidence",
        "intelligence-list",
        "intelligence-summary",
        "intelligence-policy",
    ):
        assert f'id="{element_id}"' in index
        assert f'"{element_id}"' in script
    assert 'id="intelligence-review"' in index
    assert 'data-behavior="intelligence"' in index
    renderer = script.split("function renderIntelligence", 1)[1].split(
        "function renderAudit", 1
    )[0]
    assert "innerHTML" not in renderer
    assert "ADVISORY ONLY · NO EXECUTION AUTHORITY" in index
    assert ".command-intelligence { grid-area: intelligence;" in styles
    assert ".intelligence-copy small { overflow: visible; white-space: normal; }" in styles


def test_spoken_briefing_can_include_one_nonduplicative_decision_support_line():
    result = compose_briefing(
        {
            "name": "Giovanni",
            "affirmation": "You are prepared for today.",
            "weather": None,
            "protein": 100,
            "protein_target": 100,
            "vitamins_taken": True,
            "vitamin_streak": 2,
            "meal_name": None,
            "bills": [],
            "bills_total": 0,
            "leftover": 500,
            "spent_week": 0,
            "safe_to_spend": 100,
            "audit_health": "balanced",
            "workouts": [],
            "next_pay": {"label": "Paycheck 1", "days_away": 5, "date": "2026-08-31"},
            "decision_support": (
                "Decision support: Confirm occupancy before Away actions. "
                "Trusted presence evidence disagrees with Away mode."
            ),
        },
        hour=8,
        day_ordinal=10,
    )

    assert [section["key"] for section in result["sections"]].count("decision_support") == 1
    assert result["speech"].count("Decision support:") == 1
