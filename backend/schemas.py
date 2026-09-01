from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """One model endpoint to benchmark. API key is server-side only."""
    id: str
    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str
    model: str
    label: str | None = None  # optional human-readable label


class BenchmarkPrompt(BaseModel):
    id: str
    title: str
    prompt: str
    category: str = "general"
    expected_tools: list[str] = Field(default_factory=list)
    success_criteria: str = ""


class RunRequest(BaseModel):
    prompt_id: str
    prompt: str
    judge_model: ModelConfig | None = None  # optional dedicated judge
    models: list[ModelConfig]
    max_steps: int = 12
    timeout_s: int = 180


class TraceMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class Metrics(BaseModel):
    runtime_s: float = 0.0
    tool_calls: int = 0
    failed_tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class JudgeScores(BaseModel):
    correctness: float = 0.0
    tool_usage: float = 0.0
    autonomy: float = 0.0
    efficiency: float = 0.0
    notes: str = ""
    final_answer: str = ""


class ModelRun(BaseModel):
    run_id: str
    model_id: str
    model_label: str
    model_name: str
    base_url: str
    trace: list[dict[str, Any]] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    final_answer: str = ""
    judge: JudgeScores | None = None
    status: Literal["pending", "running", "completed", "failed", "timeout"] = "pending"
    error: str | None = None
    started_at: float = 0.0
    finished_at: float = 0.0


class Session(BaseModel):
    session_id: str
    created_at: float
    prompt_id: str
    prompt_title: str
    prompt: str
    runs: list[ModelRun] = Field(default_factory=list)


def success_criteria_for_judge(self) -> str:
    # success criteria is a property of the prompt, not the request,
    # but we keep the lookup here so the agent module can call one method.
    from .prompts import load_prompts
    for p in load_prompts():
        if p.get("id") == self.prompt_id:
            return p.get("success_criteria", "")
    return ""

RunRequest.success_criteria_for_judge = success_criteria_for_judge
