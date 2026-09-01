"""
Agent loop + LLM judge. The agent loop is OpenAI-compatible (works with
any endpoint that speaks the /v1/chat/completions tool-calling protocol,
including local servers like vLLM, llama.cpp, Ollama's OpenAI shim, etc.).
"""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from typing import Any

import httpx
from openai import AsyncOpenAI

from .tools import ToolContext
from .schemas import ModelConfig, ModelRun, Metrics, JudgeScores, RunRequest, TraceMessage


SYSTEM_PROMPT = (
    "You are an autonomous coding agent evaluated in the InfinityArena benchmark. "
    "You have access to the following tools to complete the user's task: command "
    "(run a shell command in an isolated workspace), readFile, editFile, "
    "websearch, and webpg. Be concise, plan briefly, then act. When the task is "
    "fully complete, respond with a short final answer and no further tool calls."
)


def _client(cfg: ModelConfig) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=60.0)


def _normalize_tool_calls(msg: Any) -> list[dict[str, Any]]:
    """Convert openai's tool_call objects to plain dicts that round-trip as JSON."""
    out: list[dict[str, Any]] = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        out.append({
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        })
    return out


def _record_usage(usage_obj: Any, metrics: Metrics) -> None:
    if usage_obj is None:
        return
    pt = getattr(usage_obj, "prompt_tokens", 0) or 0
    ct = getattr(usage_obj, "completion_tokens", 0) or 0
    tt = getattr(usage_obj, "total_tokens", 0) or 0
    if not tt and (pt or ct):
        tt = pt + ct
    metrics.prompt_tokens += pt
    metrics.completion_tokens += ct
    metrics.total_tokens += tt


async def _dispatch_tool(ctx: ToolContext, name: str, raw_args: str) -> dict[str, Any]:
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"invalid JSON args: {raw_args[:200]}"}
    fn = getattr(ctx, name, None)
    if fn is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return await fn(**args)
    except TypeError as e:
        return {"ok": False, "error": f"bad tool args: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"tool error: {e}"}


def _last_n_assistant_signatures(messages: list[dict[str, Any]], n: int = 3) -> list[tuple[str, str]]:
    """Return the last n (tool_name, tool_args_json) pairs emitted by the assistant,
    in order, used by the anti-loop guard."""
    sigs: list[tuple[str, str]] = []
    for m in messages:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                sigs.append((fn.get("name", ""), fn.get("arguments", "")))
    return sigs[-n:]


async def run_model(req: RunRequest, model_cfg: ModelConfig, run_id: str) -> ModelRun:
    """Drive one model through the agent loop. Returns a populated ModelRun."""
    run = ModelRun(
        run_id=run_id,
        model_id=model_cfg.id,
        model_label=model_cfg.label or model_cfg.name,
        model_name=model_cfg.model,
        base_url=model_cfg.base_url,
        started_at=time.time(),
        status="running",
    )

    ctx = ToolContext(run_id=run_id)
    # seed fixtures if any
    from .prompts import fixture_for
    for fname, contents in fixture_for(req.prompt_id).items():
        (ctx.workspace / fname).write_text(contents, encoding="utf-8")

    client = _client(model_cfg)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": req.prompt},
    ]
    run.trace.append({"role": "system", "content": SYSTEM_PROMPT})
    run.trace.append({"role": "user", "content": req.prompt})

    final_answer = ""
    t0 = time.time()
    aborted = False
    try:
        for step in range(req.max_steps):
            try:
                resp = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model_cfg.model,
                        messages=messages,
                        tools=ToolContext.schemas(),
                        tool_choice="auto",
                        temperature=0.2,
                    ),
                    timeout=req.timeout_s,
                )
            except asyncio.TimeoutError:
                run.status = "timeout"
                run.error = f"model call timed out after {req.timeout_s}s"
                break
            except Exception as e:
                run.status = "failed"
                run.error = f"model call error: {e}"
                break

            choice = resp.choices[0]
            msg = choice.message
            _record_usage(getattr(resp, "usage", None), run.metrics)

            tcs = _normalize_tool_calls(msg)
            run.trace.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": tcs or None,
            })
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": tcs or None,
            })

            if not tcs:
                final_answer = (msg.content or "").strip()
                run.status = "completed"
                break

            # execute each tool call sequentially within this run
            for tc in tcs:
                # anti-loop guard: if the last 3 assistant tool-call signatures match
                # the current one, treat the model as stuck and end the run cleanly.
                current_sig = (tc["function"]["name"], tc["function"]["arguments"])
                recent = _last_n_assistant_signatures(messages[:-1], n=3)  # exclude the one we just added
                if len(recent) >= 2 and recent[-1] == current_sig and recent[-2] == current_sig:
                    note = f"anti-loop: tool call {current_sig[0]} repeated 3+ times, ending run"
                    run.trace.append({"role": "system", "content": note})
                    final_answer = note
                    run.status = "completed"
                    aborted = True
                    break

                run.metrics.tool_calls += 1
                result = await _dispatch_tool(ctx, tc["function"]["name"], tc["function"]["arguments"])
                if not result.get("ok", False):
                    run.metrics.failed_tool_calls += 1
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": json.dumps(result, ensure_ascii=False)[:8000],
                }
                run.trace.append(tool_msg)
                messages.append(tool_msg)
            if aborted:
                break
        else:
            # max_steps hit without a final answer
            run.status = "completed"
    finally:
        run.finished_at = time.time()
        run.metrics.runtime_s = round(run.finished_at - t0, 3)
        run.final_answer = final_answer
        # attach tool log from context for the judge / trace view
        run.trace.append({"role": "_meta", "tool_log": ctx.tool_log})
        # best-effort: if no final_answer, use the last assistant content
        if not run.final_answer:
            for entry in reversed(run.trace):
                if entry.get("role") == "assistant" and entry.get("content"):
                    run.final_answer = entry["content"]
                    break

    return run


JUDGE_SYSTEM = (
    "You are an impartial judge evaluating an autonomous coding agent's run on "
    "a benchmark task. You are given the original task, the success criteria, "
    "and the full trace (assistant messages + tool calls + tool results + "
    "tool_log). Score the run on four axes, each 0-10 (integers preferred):\n"
    "  - correctness: did it accomplish the task and produce a correct answer?\n"
    "  - tool_usage: did it pick the right tools and pass sensible arguments?\n"
    "  - autonomy: did it drive the task to completion without obvious stalls "
    "    or looping, and recover from errors?\n"
    "  - efficiency: did it reach the answer with reasonable tool-call count "
    "    and runtime (fewer wasted calls = higher score)?\n"
    "Return ONLY a single JSON object with keys: correctness, tool_usage, "
    "autonomy, efficiency, notes, final_answer. `notes` is one short paragraph. "
    "`final_answer` is the agent's best final answer, copied/extracted from the "
    "trace (empty string if none)."
)


async def judge_run(judge_cfg: ModelConfig, req: RunRequest, run: ModelRun) -> JudgeScores:
    """Ask an LLM judge to score the run. Falls back to a heuristic score
    if the judge call fails or judge is omitted."""
    fallback = _heuristic_judge(req, run)
    if judge_cfg is None:
        return fallback
    try:
        client = _client(judge_cfg)
        # build a compact view of the trace for the judge
        compact_trace = []
        for entry in run.trace:
            if entry.get("role") == "_meta":
                continue
            compact_trace.append(entry)
            if len(compact_trace) > 60:
                compact_trace.append({"role": "_note", "content": "... (trace truncated) ..."})
                break

        user_msg = (
            f"## Task\n{req.prompt}\n\n"
            f"## Success criteria\n{req.success_criteria_for_judge()}\n\n"
            f"## Trace (JSON)\n```json\n{json.dumps(compact_trace, ensure_ascii=False)[:60000]}\n```\n\n"
            f"## Metrics\n{json.dumps(run.metrics.model_dump())}\n\n"
            "Return only the JSON scoring object."
        )
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=judge_cfg.model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            ),
            timeout=90,
        )
        content = (resp.choices[0].message.content or "").strip()
        # try strict json, then first {...} block
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1:
                return fallback
            data = json.loads(content[start:end + 1])

        def _clamp(x: Any) -> float:
            try:
                v = float(x)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, min(10.0, v))

        return JudgeScores(
            correctness=_clamp(data.get("correctness", 0)),
            tool_usage=_clamp(data.get("tool_usage", 0)),
            autonomy=_clamp(data.get("autonomy", 0)),
            efficiency=_clamp(data.get("efficiency", 0)),
            notes=str(data.get("notes", ""))[:2000],
            final_answer=str(data.get("final_answer", run.final_answer))[:4000],
        )
    except Exception as e:
        fallback.notes = (fallback.notes + f" | judge error: {e}").strip()
        return fallback


def _heuristic_judge(req: RunRequest, run: ModelRun) -> JudgeScores:
    """Cheap, deterministic score when no LLM judge is available."""
    failed = run.metrics.failed_tool_calls
    total = run.metrics.tool_calls
    runtime = run.metrics.runtime_s
    has_answer = bool(run.final_answer.strip())
    status_ok = run.status in ("completed",)

    # correctness: reward a non-empty final answer and a successful status
    correctness = 5.0 if has_answer else 1.0
    if status_ok and has_answer:
        correctness = 7.0
    if failed == 0 and total > 0:
        correctness += 0.5
    correctness = min(10.0, correctness)

    # tool_usage: did it actually use tools?
    tool_usage = 0.0
    if total >= 1:
        tool_usage = 6.0
        if failed == 0:
            tool_usage = 8.0
        if total >= 3 and failed == 0:
            tool_usage = 9.0
    # bonus if expected tools appear in the trace
    from .prompts import load_prompts
    expected = next((p.get("expected_tools", []) for p in load_prompts() if p.get("id") == req.prompt_id), [])
    used = {e.get("name") for e in run.trace if e.get("role") == "tool"}
    if expected and any(t in used for t in expected):
        tool_usage = min(10.0, tool_usage + 1.0)

    # autonomy: failed calls lower this
    autonomy = 8.0
    autonomy -= min(4.0, failed * 1.5)
    if run.status in ("failed", "timeout"):
        autonomy -= 2.0
    autonomy = max(0.0, min(10.0, autonomy))

    # efficiency: penalize many calls / long runtime
    efficiency = 9.0
    efficiency -= min(4.0, max(0, total - 5) * 0.4)
    efficiency -= min(3.0, max(0.0, (runtime - 30) / 30))
    if total == 0 and not has_answer:
        efficiency = 1.0
    efficiency = max(0.0, min(10.0, efficiency))

    return JudgeScores(
        correctness=round(correctness, 2),
        tool_usage=round(tool_usage, 2),
        autonomy=round(autonomy, 2),
        efficiency=round(efficiency, 2),
        notes="Heuristic judge (no LLM judge configured or judge call failed).",
        final_answer=run.final_answer,
    )
