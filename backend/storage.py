"""Persist benchmark sessions as JSON files in /sessions."""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .schemas import Session, ModelRun

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_session_path(session_id: str) -> Path:
    # only allow safe ids
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid session id")
    return SESSIONS_DIR / f"{safe}.json"


def list_sessions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # tiny summary for the index
        runs = j.get("runs", [])
        scored = [r for r in runs if r.get("judge")]
        avg = 0.0
        if scored:
            totals = []
            for r in scored:
                jd = r["judge"]
                totals.append((jd.get("correctness", 0) + jd.get("tool_usage", 0)
                               + jd.get("autonomy", 0) + jd.get("efficiency", 0)) / 4.0)
            avg = round(sum(totals) / len(totals), 2)
        out.append({
            "session_id": j.get("session_id"),
            "created_at": j.get("created_at"),
            "prompt_id": j.get("prompt_id"),
            "prompt_title": j.get("prompt_title"),
            "num_models": len(runs),
            "avg_score": avg,
        })
    return out


def load_session(session_id: str) -> Session | None:
    p = _safe_session_path(session_id)
    if not p.exists():
        return None
    return Session.model_validate_json(p.read_text(encoding="utf-8"))


def save_session(session: Session) -> None:
    p = _safe_session_path(session.session_id)
    p.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def new_session_id() -> str:
    return f"ses_{int(time.time())}_{uuid.uuid4().hex[:8]}"
