"""Read-only Internet Intelligence Broker API."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..internet_intelligence import (
    internet_snapshot,
    ingest_connected_feed,
    media_search,
    nutrition_search,
    research,
)


router = APIRouter(prefix="/api/internet", tags=["internet-intelligence"])


class ConnectedFeedIn(BaseModel):
    source: str = Field(min_length=2, max_length=120)
    observed_at: datetime | None = None
    ttl_minutes: int = Field(default=60, ge=5, le=1440)
    payload: dict[str, Any]


@router.get("")
def snapshot():
    return internet_snapshot()


@router.post("/refresh")
def refresh():
    return internet_snapshot(force=True)


@router.get("/research")
def research_query(q: str = Query(min_length=2, max_length=200)):
    try:
        return research(q)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/media")
def media_query(q: str = Query(min_length=2, max_length=160)):
    try:
        return media_search(q)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/nutrition")
def nutrition_query(q: str = Query(min_length=2, max_length=160)):
    try:
        return nutrition_search(q)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/feeds/{capability}")
def ingest_feed(capability: str, body: ConnectedFeedIn):
    try:
        return ingest_connected_feed(
            capability,
            source=body.source,
            observed_at=body.observed_at,
            ttl_minutes=body.ttl_minutes,
            payload=body.payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
