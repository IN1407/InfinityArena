# InfinityArena

Benchmark multiple OpenAI-compatible LLM endpoints on agentic coding tasks.

A local FastAPI app with a dark, modern web UI. Configure any number of model
endpoints (any service that speaks `/v1/chat/completions` with tool calling —
OpenAI, vLLM, llama.cpp server, Ollama's OpenAI shim, etc.), run the same task
on all of them in parallel, inspect each run's full trace, and view results in
a sortable leaderboard scored by an LLM judge.

## Features

- **Parallel multi-model runs** with per-run isolated tool implementations
- **Tools exposed to the model**: `command`, `readFile`, `editFile`,
  `websearch`, `webpg` — each model gets its own sandboxed workspace
- **Full trace recording** — every model message, tool call, tool result,
  timing, and token usage
- **Metrics**: total runtime, tool calls, failed tool calls, prompt /
  completion / total tokens
- **LLM judge** that scores every run on four axes (0–10):
  - correctness, tool usage, autonomy, efficiency
  - falls back to a deterministic heuristic if no judge is configured
- **Sortable leaderboard** and a **per-run detail view** with the complete
  trace and a compact tool-log timeline
- **Local JSON session storage** in `./sessions/`
- **Five built-in benchmark prompts** covering file editing, web research,
  shell debugging, URL fetching, and code review
- **Server-side API keys** — keys are sent to the backend, used for the
  benchmark, and never returned to the browser

## Layout

```
backend/
  main.py        FastAPI app + static-file serving
  schemas.py     Pydantic models (ModelConfig, RunRequest, Session, …)
  tools.py       Per-run ToolContext (command, readFile, editFile, websearch, webpg)
  agent.py       Agent loop, anti-loop guard, LLM judge, heuristic judge
  prompts.py     Built-in benchmark prompts + workspace fixtures
  storage.py     Session persistence (JSON files)
frontend/
  index.html     Single-page UI
  styles.css     Dark theme
  app.js         Vanilla JS — no build step
sessions/        Created on first run; one .json per benchmark session
data/workspaces/ Per-run isolated workspaces (auto-created)
run.py           Convenience launcher
requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:8765> in your browser.

## Use

1. Pick a built-in prompt (or write your own in the text area).
2. Add one row per model: display name, base URL, model id, API key.
3. (Optional) Configure an LLM judge in the third card.
4. Click **Run benchmark on all models**. Models run in parallel.
5. View the leaderboard, sort by any column, click **open** on a row to
   see the full trace, judge scores, and final answer.

## API endpoints

| Method | Path                          | Purpose                          |
| ------ | ----------------------------- | -------------------------------- |
| GET    | `/api/prompts`                | List built-in prompts (summary)  |
| GET    | `/api/prompts/{id}`           | Get full prompt text + criteria  |
| POST   | `/api/run`                    | Run all models on a prompt       |
| GET    | `/api/sessions`               | List saved sessions              |
| GET    | `/api/sessions/{id}`          | Full session + every run + trace |
| POST   | `/api/check-key`              | Test an endpoint+key+model combo |
| GET    | `/`                           | Web UI                           |

## Privacy

API keys submitted through the UI are POSTed to `/api/run` and used only
inside the FastAPI process. The frontend never stores them, never sends
them anywhere else, and never receives them back in API responses. The
session JSON files persisted to disk also do **not** include API keys.
