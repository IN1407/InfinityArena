"""FastAPI app for InfinityArena."""
from __future__ import annotations
import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .schemas import RunRequest, Session
from .prompts import load_prompts
from .agent import run_model, judge_run
from .storage import list_sessions, load_session, save_session, new_session_id, _safe_session_path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="InfinityArena", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- API: prompts ----------
@app.get("/api/prompts")
def api_prompts():
    # strip long prompts from the index for the dropdown; full prompt fetched on demand
    items = load_prompts()
    return [{
        "id": p["id"],
        "title": p["title"],
        "category": p.get("category", "general"),
        "expected_tools": p.get("expected_tools", []),
        "success_criteria": p.get("success_criteria", ""),
    } for p in items]


@app.get("/api/prompts/{prompt_id}")
def api_prompt(prompt_id: str):
    for p in load_prompts():
        if p["id"] == prompt_id:
            return p
    raise HTTPException(404, "prompt not found")


# ---------- API: benchmark run ----------
class RunResponse(BaseModel):
    session_id: str


@app.post("/api/run", response_model=RunResponse)
async def api_run(req: RunRequest):
    if not req.models:
        raise HTTPException(400, "at least one model is required")
    if not req.prompt.strip():
        raise HTTPException(400, "prompt is empty")

    session_id = new_session_id()
    titles = {p["id"]: p["title"] for p in load_prompts()}
    session = Session(
        session_id=session_id,
        created_at=time.time(),
        prompt_id=req.prompt_id,
        prompt_title=titles.get(req.prompt_id, "Custom"),
        prompt=req.prompt,
    )

    # spawn one task per model in parallel
    run_ids = [f"run_{session_id}_{i}" for i in range(len(req.models))]

    async def _one(idx: int):
        cfg = req.models[idx]
        rid = run_ids[idx]
        run = await run_model(req, cfg, rid)
        # score it
        run.judge = await judge_run(req.judge_model, req, run)
        return run

    runs = await asyncio.gather(*[_one(i) for i in range(len(req.models))], return_exceptions=False)
    session.runs = runs
    save_session(session)
    return RunResponse(session_id=session_id)


# ---------- API: sessions ----------
@app.get("/api/sessions")
def api_sessions():
    return list_sessions()


@app.get("/api/sessions/{session_id}")
def api_session(session_id: str):
    s = load_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s.model_dump()


# ---------- API: server-side-only key validation ----------
class KeyCheckRequest(BaseModel):
    base_url: str
    api_key: str
    model: str


class KeyCheckResponse(BaseModel):
    ok: bool
    detail: str = ""


@app.post("/api/check-key", response_model=KeyCheckResponse)
async def api_check_key(req: KeyCheckRequest):
    """Make a tiny /models-list call to verify the endpoint+key+model work.
    The key is never returned to the client and is only used in this server-side call."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # try a 1-token completion to be sure the model id is valid
            r = await client.post(
                req.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {req.api_key}"},
                json={
                    "model": req.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                },
            )
            if r.status_code == 200:
                return KeyCheckResponse(ok=True)
            return KeyCheckResponse(ok=False, detail=f"HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        return KeyCheckResponse(ok=False, detail=str(e))


# ---------- static frontend ----------
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/session/{session_id}")
def session_page(session_id: str):
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8765, reload=False)
