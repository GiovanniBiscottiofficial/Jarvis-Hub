#!/usr/bin/env python3
"""Safely tune the Home Assistant Ollama subentry used by Jarvis."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def update_jarvis_subentry(
    payload: dict[str, Any],
    *,
    prompt: str,
    model: str,
    max_history: int,
    num_ctx: int,
) -> str:
    """Update only the Jarvis Ollama conversation subentry and return its id."""
    entries = payload.get("data", {}).get("entries", [])
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("domain") != "ollama":
            continue
        for subentry in entry.get("subentries", []):
            data = subentry.get("data", {})
            title = str(subentry.get("title", ""))
            if data.get("model") == model or "jarvis" in title.lower():
                candidates.append(subentry)
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one Jarvis Ollama subentry, found {len(candidates)}"
        )
    subentry = candidates[0]
    data = subentry.setdefault("data", {})
    data.update(
        {
            "model": model,
            "prompt": prompt.strip(),
            "max_history": float(max_history),
            "num_ctx": float(num_ctx),
            "think": False,
        }
    )
    # Device execution remains with Home Assistant's fast local intents.
    data["llm_hass_api"] = []
    return str(subentry.get("subentry_id") or subentry.get("id") or "unknown")


def atomic_write_with_backup(path: Path, payload: dict[str, Any]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.jarvis-backup-{stamp}")
    shutil.copy2(path, backup)
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:1.5b")
    parser.add_argument("--max-history", type=int, default=4)
    parser.add_argument("--num-ctx", type=int, default=2048)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 4 <= args.max_history <= 20:
        parser.error("--max-history must be between 4 and 20")
    if not 2048 <= args.num_ctx <= 8192:
        parser.error("--num-ctx must be between 2048 and 8192")
    payload = json.loads(args.config.read_text(encoding="utf-8"))
    prompt = args.prompt.read_text(encoding="utf-8")
    subentry_id = update_jarvis_subentry(
        payload,
        prompt=prompt,
        model=args.model,
        max_history=args.max_history,
        num_ctx=args.num_ctx,
    )
    if not args.apply:
        print(
            f"dry run: would update {subentry_id} with model={args.model}, "
            f"history={args.max_history}, context={args.num_ctx}"
        )
        return 0
    backup = atomic_write_with_backup(args.config, payload)
    print(
        f"updated {subentry_id}; backup={backup.name}; model={args.model}; "
        f"history={args.max_history}; context={args.num_ctx}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
