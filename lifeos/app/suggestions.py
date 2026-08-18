"""Meal suggestion logic: 5/15-minute options, sweet-potato preference,
avoided foods excluded unless overridden today."""
from datetime import date

from .db import conn

AVOID_TAGS = {"rice", "bread", "mashed_potatoes"}


def _today() -> str:
    return date.today().isoformat()


def todays_overrides(c) -> set:
    rows = c.execute(
        "SELECT meal FROM overrides WHERE date(ts)=? ", (_today(),)
    ).fetchall()
    return {r["meal"] for r in rows}


def suggest_meals(max_minutes: int = 15, limit: int = 3) -> list[dict]:
    with conn() as c:
        overridden = todays_overrides(c)
        rows = c.execute(
            "SELECT * FROM meals WHERE minutes<=? ORDER BY avoided ASC,"
            " (tags LIKE '%preferred%') DESC, protein_g DESC",
            (max_minutes,),
        ).fetchall()
        out = []
        for r in rows:
            if r["avoided"] and r["name"] not in overridden:
                continue
            out.append(dict(r))
            if len(out) >= limit:
                break
        return out


def high_protein_snacks(shortfall_g: float, limit: int = 2) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM meals WHERE tags LIKE '%snack%' AND avoided=0"
            " ORDER BY ABS(protein_g-?) ASC LIMIT ?",
            (shortfall_g, limit),
        ).fetchall()
        return [dict(r) for r in rows]
