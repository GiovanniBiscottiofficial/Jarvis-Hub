"""LifeOS — Vault Flow + Body Ops for the Jarvis Hub."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import hmac
import hashlib
import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import active_profile, conn, get_setting, init_db
from .chef import chef_summary
from .routers import (
    bodyops,
    budget,
    context,
    insights,
    pantry,
    profiles,
    vaultflow,
    webhooks,
)
from .routers.bodyops import protein_today, streak, water_today
from .conversation_bridge import stop_voice_events, watch_voice_events


@asynccontextmanager
async def lifespan(_app: FastAPI):
    voice_bridge_task = asyncio.create_task(watch_voice_events())
    try:
        yield
    finally:
        await stop_voice_events(voice_bridge_task)


app = FastAPI(title="LifeOS", version="0.2.0", lifespan=lifespan)


PUBLIC_PATHS = {"/api/auth", "/api/auth/home-assistant", "/healthz"}
SESSION_COOKIE = "lifeos_session"
SESSION_MAX_AGE = 3600


def _token_matches(supplied: str | None, expected: str) -> bool:
    if not supplied or not expected:
        return False
    supplied_bytes = supplied.encode()
    expected_bytes = expected.encode()
    return len(supplied_bytes) == len(expected_bytes) and hmac.compare_digest(
        supplied_bytes, expected_bytes
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _issue_session(subject: str, expected: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE)
    payload = _b64encode(
        json.dumps(
            {"sub": subject, "exp": int(expires.timestamp())},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    signature = _b64encode(
        hmac.new(expected.encode(), payload.encode(), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def _session_matches(supplied: str | None, expected: str) -> bool:
    if not supplied or not expected:
        return False
    try:
        payload, signature = supplied.split(".", 1)
        expected_signature = _b64encode(
            hmac.new(expected.encode(), payload.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected_signature):
            return False
        claims = json.loads(_b64decode(payload))
        return int(claims["exp"]) > int(datetime.now(timezone.utc).timestamp())
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _set_session_cookie(response: JSONResponse, session: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=SESSION_MAX_AGE,
        path="/",
    )


async def _verify_home_assistant_token(token: str) -> dict:
    """Validate a browser token against HA without retaining it."""
    if not token:
        raise ValueError("missing Home Assistant token")
    url = os.environ.get(
        "HOME_ASSISTANT_URL", "http://host.docker.internal:8123"
    ).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(f"{url}/api/auth/current_user", headers=headers)
        if response.status_code == 404:
            response = await client.get(f"{url}/api/", headers=headers)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {}
    return data if isinstance(data, dict) else {}



@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in PUBLIC_PATHS:
        expected = os.environ.get("LIFEOS_API_TOKEN", "").strip()
        authorization = request.headers.get("authorization", "")
        bearer = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else None
        )
        cookie = request.cookies.get(SESSION_COOKIE, "")
        authenticated = _token_matches(bearer, expected) or _session_matches(
            cookie, expected
        )
        if not expected or not authenticated:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthcheck():
    return {"ok": True}


@app.get("/api/health")
def api_healthcheck():
    return {"ok": True}


@app.post("/api/auth")
async def auth(request: Request):
    expected = os.environ.get("LIFEOS_API_TOKEN", "").strip()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid JSON"}, status_code=400)
    if not expected or not _token_matches(body.get("token"), expected):
        return JSONResponse({"detail": "invalid token"}, status_code=401)
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, _issue_session("lifeos-token", expected), request)
    return response


@app.post("/api/auth/home-assistant")
async def auth_home_assistant(request: Request):
    """Exchange an authenticated HA browser session for a short LifeOS session."""
    expected = os.environ.get("LIFEOS_API_TOKEN", "").strip()
    if not expected:
        return JSONResponse({"detail": "LifeOS authentication is not configured"}, status_code=503)
    try:
        body = await request.json()
        user = await _verify_home_assistant_token(str(body.get("token") or ""))
    except (ValueError, httpx.HTTPError):
        return JSONResponse({"detail": "invalid Home Assistant session"}, status_code=401)
    subject = str(user.get("id") or user.get("name") or "home-assistant-user")
    response = JSONResponse({"ok": True, "source": "home_assistant"})
    _set_session_cookie(response, _issue_session(subject, expected), request)
    return response

init_db()

app.include_router(bodyops.router)
app.include_router(budget.router)
app.include_router(vaultflow.router)
app.include_router(webhooks.router)
app.include_router(profiles.router)
app.include_router(pantry.router)
app.include_router(insights.router)
app.include_router(context.router)


@app.get("/api/today")
def today():
    """Morning workflow payload: suggestions, progress, vault snapshot, nudges."""
    with conn() as c:
        prof = active_profile(c)
        target = prof["protein_target_g"]
        protein = protein_today(c)
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=? AND profile_id=?",
            (date.today().isoformat(), prof["id"]),
        ).fetchone()
        vit_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=? AND profile_id=?",
            (date.today().isoformat(), prof["id"]),
        ).fetchone()
        accounts = [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]
        nudges = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM nudges WHERE resolved=0 ORDER BY ts DESC LIMIT 5"
            ).fetchall()
        ]
        return {
            "date": date.today().isoformat(),
            "profile": prof["name"],
            "step_target": prof["step_target"],
            "meal_suggestions": chef_summary(max_minutes=15)["suggestions"][:3],
            "protein": {"today_g": protein, "target_g": target},
            "steps_today": steps_row["count"] if steps_row else 0,
            "water": {
                "today": water_today(c),
                "target": int(get_setting("water_target_glasses") or 8),
            },
            "vitamins_taken": bool(vit_row and vit_row["taken"]),
            "streaks": {
                "vitamins": streak(c, "vitamins"),
                "steps": streak(c, "steps"),
            },
            "vault_total": sum(a["balance"] for a in accounts),
            "nudges": nudges,
        }

app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True),
    name="static",
)
