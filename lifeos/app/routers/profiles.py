"""Household profiles: per-person protein/step/calorie targets."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import active_profile, conn, set_setting

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileIn(BaseModel):
    name: str
    protein_target_g: float = 100
    step_target: int = 8000
    calorie_target: int = 2000


class TargetsIn(BaseModel):
    protein_target_g: float | None = None
    step_target: int | None = None
    calorie_target: int | None = None


@router.get("")
def list_profiles():
    with conn() as c:
        active_id = active_profile(c)["id"]
        return [
            dict(r) | {"active": r["id"] == active_id}
            for r in c.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        ]


@router.post("")
def add_profile(body: ProfileIn):
    with conn() as c:
        c.execute(
            "INSERT INTO profiles(name,protein_target_g,step_target,"
            "calorie_target) VALUES(?,?,?,?)",
            (body.name, body.protein_target_g, body.step_target,
             body.calorie_target),
        )
        return {"ok": True}


@router.post("/{profile_id}/activate")
def activate(profile_id: int):
    with conn() as c:
        row = c.execute(
            "SELECT id FROM profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "profile not found")
    set_setting("active_profile", str(profile_id))
    return {"ok": True}


@router.put("/{profile_id}/targets")
def set_targets(profile_id: int, body: TargetsIn):
    with conn() as c:
        row = c.execute(
            "SELECT * FROM profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "profile not found")
        c.execute(
            "UPDATE profiles SET protein_target_g=?, step_target=?,"
            " calorie_target=? WHERE id=?",
            (
                body.protein_target_g
                if body.protein_target_g is not None
                else row["protein_target_g"],
                body.step_target
                if body.step_target is not None
                else row["step_target"],
                body.calorie_target
                if body.calorie_target is not None
                else row["calorie_target"],
                profile_id,
            ),
        )
        return {"ok": True}
