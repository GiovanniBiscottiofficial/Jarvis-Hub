"""Jarvis context, event, proposal, and policy-controlled action API."""
from typing import Any
import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..context_engine import (
    ACTION_REGISTRY,
    action_audit,
    command_center_payload,
    current_context,
    dismiss_proposal,
    evaluate_behaviors,
    execute_action,
    ingest_event,
    list_proposals,
    simulate_behavior,
)
from ..db import conn
from ..conversation_bridge import conversation_status

router = APIRouter(prefix="/api", tags=["context"])


class EventIn(BaseModel):
    source: str = "home_assistant"
    event_type: str = "state_changed"
    entity_id: str | None = None
    state: str | None = None
    previous_state: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class ProposalEvaluationIn(BaseModel):
    behavior: str | None = None


class ActionIn(BaseModel):
    proposal_id: int | None = None
    confirmed: bool = False
    dry_run: bool = True
    requested_by: str = "command_center"
    data: dict[str, Any] = Field(default_factory=dict)


class SimulationIn(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


class DismissalIn(BaseModel):
    requested_by: str = "command_center"


@router.get("/context")
def get_context(event_limit: int = Query(default=20, ge=1, le=100)):
    payload = current_context(event_limit)
    payload["conversation"] = conversation_status()
    return payload


@router.get("/command-center")
def get_command_center(event_limit: int = Query(default=40, ge=1, le=100)):
    payload = command_center_payload(event_limit)
    payload.setdefault("context", {})["conversation"] = conversation_status()
    return payload


@router.get("/events")
def get_events(limit: int = Query(default=50, ge=1, le=200)):
    with conn() as c:
        events = [
            dict(row)
            for row in c.execute(
                "SELECT id,ts,source,event_type,entity_id,state,previous_state,"
                " attributes_json,correlation_id FROM context_events"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    for event in events:
        try:
            event["attributes"] = json.loads(event.pop("attributes_json"))
        except json.JSONDecodeError:
            event["attributes"] = event.pop("attributes_json")
    return events


@router.post("/events", status_code=201)
def post_event(body: EventIn):
    return ingest_event(body.model_dump())


@router.get("/proposals")
def get_proposals(status: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    return list_proposals(status, limit)


@router.post("/proposals")
def evaluate_proposals(body: ProposalEvaluationIn):
    allowed = {None, "arrival", "departure", "nightly"}
    if body.behavior not in allowed:
        raise HTTPException(400, "behavior must be arrival, departure, or nightly")
    ids = evaluate_behaviors(behavior=body.behavior)
    return {"ok": True, "created": ids, "proposals": list_proposals("pending")}


@router.post("/proposals/{proposal_id}/dismiss")
def dismiss_action_proposal(proposal_id: int, body: DismissalIn):
    if not dismiss_proposal(proposal_id, body.requested_by):
        raise HTTPException(409, "proposal is missing or no longer pending")
    return {"ok": True, "proposal_id": proposal_id, "status": "dismissed"}


@router.post("/simulations/{behavior}")
def run_behavior_simulation(behavior: str, body: SimulationIn):
    try:
        return simulate_behavior(behavior, body.overrides)
    except ValueError:
        raise HTTPException(404, "unknown behavior") from None


@router.get("/actions")
def get_actions():
    return ACTION_REGISTRY


@router.get("/actions/audit")
def get_action_audit(limit: int = Query(default=50, ge=1, le=200)):
    return action_audit(limit)


@router.post("/actions/{action_id}")
def post_action(action_id: str, body: ActionIn):
    try:
        return execute_action(
            action_id,
            body.proposal_id,
            body.confirmed,
            body.dry_run,
            body.requested_by,
            body.data,
        )
    except KeyError:
        raise HTTPException(404, "unknown action") from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
