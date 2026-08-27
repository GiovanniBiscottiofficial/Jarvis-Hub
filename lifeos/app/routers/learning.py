"""Transparent, local-first preference learning for Jarvis.

Observations can raise candidates, but inferred candidates never authorize a
Home Assistant action. Giovanni must explicitly confirm a preference before it
is treated as durable guidance, and every decision is reversible and audited.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import active_profile, conn

router = APIRouter(prefix="/api/learning", tags=["learning"])

SIGNAL_POLARITY = {
    "liked": 1.0,
    "disliked": -1.0,
    "chosen": 0.65,
    "skipped": -0.5,
    "stated": 1.0,
    "corrected": -1.0,
}
SIGNAL_WEIGHT = {
    "liked": 1.0,
    "disliked": 1.0,
    "chosen": 0.55,
    "skipped": 0.45,
    "stated": 1.35,
    "corrected": 1.35,
}
ALLOWED_SOURCES = {"lifeos_ui", "voice", "chef", "body_ops", "api"}


class FeedbackIn(BaseModel):
    domain: str = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=240)
    signal: Literal["liked", "disliked", "chosen", "skipped", "stated", "corrected"]
    source: str = Field(default="lifeos_ui", max_length=40)
    context: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


class DecisionIn(BaseModel):
    decision: Literal["confirm", "reject", "reconsider", "forget"]
    reason: str = Field(default="", max_length=240)


def _clean_label(value: str, max_length: int) -> str:
    cleaned = " ".join(str(value).split()).strip()
    if not cleaned or len(cleaned) > max_length:
        raise ValueError("invalid learning label")
    return cleaned


def _clean_domain(value: str) -> str:
    domain = re.sub(r"[^a-z0-9_-]+", "_", value.lower()).strip("_")
    if not domain or len(domain) > 40:
        raise ValueError("invalid learning domain")
    return domain


def _preference_row(c, preference_id: int) -> dict[str, Any] | None:
    row = c.execute(
        "SELECT * FROM learned_preferences WHERE id=?", (preference_id,)
    ).fetchone()
    return dict(row) if row else None


def record_learning_observation(
    c,
    *,
    profile_id: int,
    domain: str,
    subject: str,
    value: str,
    signal: str,
    source: str,
    context: dict[str, Any] | None = None,
    auto_confirm: bool = False,
) -> dict[str, Any]:
    """Record evidence and recompute one explainable preference candidate."""
    if signal not in SIGNAL_POLARITY:
        raise ValueError("unsupported learning signal")
    domain = _clean_domain(domain)
    subject = _clean_label(subject, 80)
    value = _clean_label(value, 240)
    source = source if source in ALLOWED_SOURCES else "api"
    context_json = json.dumps(context or {}, separators=(",", ":"), sort_keys=True)
    c.execute(
        "INSERT INTO learning_observations(profile_id,domain,subject,value,signal,"
        "polarity,weight,source,context_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            profile_id,
            domain,
            subject,
            value,
            signal,
            SIGNAL_POLARITY[signal],
            SIGNAL_WEIGHT[signal],
            source,
            context_json,
        ),
    )
    evidence = c.execute(
        "SELECT polarity,weight FROM learning_observations WHERE profile_id=?"
        " AND domain=? AND subject=? AND value=? AND active=1",
        (profile_id, domain, subject, value),
    ).fetchall()
    score = sum(float(row["polarity"]) * float(row["weight"]) for row in evidence)
    total_weight = sum(abs(float(row["weight"])) for row in evidence) or 1.0
    agreement = abs(score) / total_weight
    depth = min(1.0, len(evidence) / 4)
    confidence = round(min(0.98, 0.25 + 0.4 * agreement + 0.3 * depth), 2)
    sentiment = "prefer" if score >= 0 else "avoid"
    existing = c.execute(
        "SELECT * FROM learned_preferences WHERE profile_id=? AND domain=?"
        " AND subject=? AND value=?",
        (profile_id, domain, subject, value),
    ).fetchone()
    prior_status = existing["status"] if existing else "candidate"
    if auto_confirm:
        status = "confirmed"
    elif existing and prior_status == "confirmed" and existing["sentiment"] != sentiment:
        # Contradictory evidence can challenge confirmed guidance, but it cannot
        # silently reverse Giovanni's decision.
        status = "candidate"
    elif prior_status in {"confirmed", "rejected"}:
        status = prior_status
    else:
        status = "candidate"
    reason = (
        f"{len(evidence)} explicit signal{'s' if len(evidence) != 1 else ''}; "
        f"{round(agreement * 100)}% agreement supports {sentiment}."
    )
    c.execute(
        "INSERT INTO learned_preferences(profile_id,domain,subject,value,sentiment,"
        "status,confidence,evidence_count,reason) VALUES(?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(profile_id,domain,subject,value) DO UPDATE SET"
        " sentiment=excluded.sentiment,status=excluded.status,"
        " confidence=excluded.confidence,evidence_count=excluded.evidence_count,"
        " reason=excluded.reason,updated_at=datetime('now','localtime')",
        (
            profile_id,
            domain,
            subject,
            value,
            sentiment,
            status,
            confidence,
            len(evidence),
            reason,
        ),
    )
    preference = c.execute(
        "SELECT * FROM learned_preferences WHERE profile_id=? AND domain=?"
        " AND subject=? AND value=?",
        (profile_id, domain, subject, value),
    ).fetchone()
    preference_dict = dict(preference)
    c.execute(
        "INSERT INTO learning_audit(profile_id,preference_id,action,before_json,"
        "after_json,reason) VALUES(?,?,?,?,?,?)",
        (
            profile_id,
            preference["id"],
            "observed_and_confirmed" if auto_confirm else "observed",
            json.dumps(dict(existing)) if existing else "{}",
            json.dumps(preference_dict),
            f"{source}:{signal}",
        ),
    )
    return preference_dict


def forget_learning_value(
    c, *, profile_id: int, domain: str, subject: str, value: str, reason: str
) -> None:
    row = c.execute(
        "SELECT * FROM learned_preferences WHERE profile_id=? AND domain=?"
        " AND subject=? AND value=?",
        (profile_id, domain, subject, value),
    ).fetchone()
    if not row:
        return
    before = dict(row)
    c.execute(
        "UPDATE learned_preferences SET status='forgotten',"
        " updated_at=datetime('now','localtime') WHERE id=?",
        (row["id"],),
    )
    c.execute(
        "UPDATE learning_observations SET active=0 WHERE profile_id=? AND domain=?"
        " AND subject=? AND value=?",
        (profile_id, domain, subject, value),
    )
    after = _preference_row(c, row["id"])
    c.execute(
        "INSERT INTO learning_audit(profile_id,preference_id,action,before_json,"
        "after_json,reason) VALUES(?,?,?,?,?,?)",
        (profile_id, row["id"], "forgotten", json.dumps(before), json.dumps(after), reason),
    )


def learning_snapshot(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with conn() as c:
        profile = active_profile(c)
        preferences = [
            dict(row)
            for row in c.execute(
                "SELECT * FROM learned_preferences WHERE profile_id=?"
                " AND status!='forgotten' ORDER BY"
                " CASE status WHEN 'candidate' THEN 0 WHEN 'confirmed' THEN 1 ELSE 2 END,"
                " confidence DESC,updated_at DESC LIMIT ?",
                (profile["id"], limit),
            ).fetchall()
        ]
        observations = [
            dict(row)
            for row in c.execute(
                "SELECT id,ts,domain,subject,value,signal,source FROM learning_observations"
                " WHERE profile_id=? AND active=1 ORDER BY id DESC LIMIT 12",
                (profile["id"],),
            ).fetchall()
        ]
        audit = [
            dict(row)
            for row in c.execute(
                "SELECT id,ts,preference_id,action,reason FROM learning_audit"
                " WHERE profile_id=? ORDER BY id DESC LIMIT 12",
                (profile["id"],),
            ).fetchall()
        ]
    counts = {status: 0 for status in ("candidate", "confirmed", "rejected")}
    for preference in preferences:
        if preference["status"] in counts:
            counts[preference["status"]] += 1
    return {
        "profile": profile["name"],
        "summary": {**counts, "observations": len(observations)},
        "preferences": preferences,
        "recent_observations": observations,
        "audit": audit,
        "policy": {
            "local_only": True,
            "explicit_evidence_only": True,
            "inferences_authorize_actions": False,
            "confirmed_preferences_are_guidance": True,
            "reversible": True,
        },
    }


@router.get("")
def get_learning(limit: int = Query(default=50, ge=1, le=100)):
    return learning_snapshot(limit)


@router.post("/feedback")
def submit_feedback(body: FeedbackIn):
    try:
        with conn() as c:
            profile = active_profile(c)
            preference = record_learning_observation(
                c,
                profile_id=profile["id"],
                domain=body.domain,
                subject=body.subject,
                value=body.value,
                signal=body.signal,
                source=body.source,
                context=body.context,
                auto_confirm=body.confirm,
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "preference": preference, "acted_on": False}


@router.post("/preferences/{preference_id}/decision")
def decide_preference(preference_id: int, body: DecisionIn):
    with conn() as c:
        profile = active_profile(c)
        row = c.execute(
            "SELECT * FROM learned_preferences WHERE id=? AND profile_id=?",
            (preference_id, profile["id"]),
        ).fetchone()
        if not row or row["status"] == "forgotten":
            raise HTTPException(404, "learning preference not found")
        before = dict(row)
        if body.decision == "forget":
            forget_learning_value(
                c,
                profile_id=profile["id"],
                domain=row["domain"],
                subject=row["subject"],
                value=row["value"],
                reason=body.reason or "Forgotten by Giovanni in LifeOS.",
            )
            return {"ok": True, "decision": "forget", "preference_id": preference_id}
        status = {
            "confirm": "confirmed",
            "reject": "rejected",
            "reconsider": "candidate",
        }[body.decision]
        c.execute(
            "UPDATE learned_preferences SET status=?,"
            " updated_at=datetime('now','localtime') WHERE id=?",
            (status, preference_id),
        )
        after = _preference_row(c, preference_id)
        c.execute(
            "INSERT INTO learning_audit(profile_id,preference_id,action,before_json,"
            "after_json,reason) VALUES(?,?,?,?,?,?)",
            (
                profile["id"],
                preference_id,
                status,
                json.dumps(before),
                json.dumps(after),
                body.reason or f"{status.title()} by Giovanni in LifeOS.",
            ),
        )
    return {"ok": True, "decision": body.decision, "preference": after}
