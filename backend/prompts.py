"""Built-in benchmark prompts. Add more here; the UI auto-loads them."""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path

_PROMPTS_PATH = Path(__file__).resolve().parent.parent / "data" / "prompts.json"

DEFAULT_PROMPTS: list[dict] = [
    {
        "id": "create-readme",
        "title": "Create a project README",
        "category": "file-editing",
        "expected_tools": ["readFile", "editFile", "command"],
        "success_criteria": (
            "A README.md is created or rewritten in the workspace with: a project "
            "title, a one-paragraph description, at least three sections (e.g. "
            "Installation, Usage, License), and a fenced code block."
        ),
        "prompt": (
            "Create a polished README.md in the current workspace for a small Python "
            "CLI tool called `infinitycli` that converts JSON to YAML. The README "
            "should include a project title, a one-paragraph description, an "
            "Installation section, a Usage section with at least one fenced code "
            "block, and a License section. Use readFile/editFile/command as needed."
        ),
    },
    {
        "id": "research-summary",
        "title": "Research a topic and summarize",
        "category": "web-research",
        "expected_tools": ["websearch", "webpg"],
        "success_criteria": (
            "Final answer cites at least 2 distinct URLs obtained via websearch/webpg, "
            "and gives a concise, accurate summary of the topic."
        ),
        "prompt": (
            "Use websearch and webpg to look up what \"Model Context Protocol (MCP)\" "
            "is. Cite at least two distinct URLs in your final answer and give a "
            "concise summary (under 200 words) suitable for a senior engineer."
        ),
    },
    {
        "id": "debug-pipeline",
        "title": "Diagnose a failing shell script",
        "category": "command-line",
        "expected_tools": ["command", "readFile", "editFile"],
        "success_criteria": (
            "A broken script in the workspace is diagnosed and fixed. The final "
            "answer includes the root cause and the corrected script content."
        ),
        "prompt": (
            "A file `buggy.sh` already exists in the workspace but fails. Inspect it "
            "with readFile, run it with command to see the error, fix the bug with "
            "editFile, and re-run it to confirm it works. Then explain the root "
            "cause in your final answer."
        ),
    },
    {
        "id": "fetch-and-parse",
        "title": "Fetch a URL and extract data",
        "category": "web-research",
        "expected_tools": ["webpg", "command"],
        "success_criteria": (
            "Successfully fetches a public page, extracts a specific piece of data, "
            "and reports it in the final answer with the source URL."
        ),
        "prompt": (
            "Use webpg to fetch https://example.com and report the title and main "
            "paragraph text. Then use command to save that information to "
            "`summary.txt` in the workspace. Print the file contents in your final "
            "answer."
        ),
    },
    {
        "id": "code-review",
        "title": "Review a small Python file",
        "category": "code-review",
        "expected_tools": ["readFile", "editFile"],
        "success_criteria": (
            "Reads the provided Python file, identifies at least 2 concrete issues, "
            "and produces a fixed version (either via editFile or shown in the final "
            "answer)."
        ),
        "prompt": (
            "There is a Python file `app.py` in the workspace. Read it, list at "
            "least two concrete issues (bugs, style, or security), and either fix "
            "them with editFile or paste the corrected file in your final answer."
        ),
    },
]


def _seed_workspace_files(prompts: list[dict]) -> None:
    """Create per-prompt workspace fixtures so models have something to work with."""
    ws_root = Path(__file__).resolve().parent.parent / "data" / "workspaces" / "_fixtures"
    ws_root.mkdir(parents=True, exist_ok=True)

    # buggy.sh — used by debug-pipeline
    (ws_root / "buggy.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "echo \"Starting...\"\n"
        "name = \"world\"            # bash does not allow spaces around =\n"
        "echo \"Hello $name!\"\n"
        "cd /does/not/exist         # intentional failure\n"
        "echo \"done\"\n"
    )

    # app.py — used by code-review
    (ws_root / "app.py").write_text(
        "import pickle, os\n"
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/load')\n"
        "def load():\n"
        "    data = request.args.get('data')\n"
        "    return pickle.loads(bytes.fromhex(data))   # unsafe deserialization\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    app.run(debug=True, host='0.0.0.0')        # debug=True in prod is risky\n"
    )


def load_prompts() -> list[dict]:
    if not _PROMPTS_PATH.exists():
        save_prompts(DEFAULT_PROMPTS)
        _seed_workspace_files(DEFAULT_PROMPTS)
    try:
        return json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        save_prompts(DEFAULT_PROMPTS)
        return list(DEFAULT_PROMPTS)


def save_prompts(prompts: list[dict]) -> None:
    _PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROMPTS_PATH.write_text(json.dumps(prompts, indent=2, ensure_ascii=False), encoding="utf-8")


def fixture_for(prompt_id: str) -> dict[str, str]:
    """Return {filename: contents} of files to seed into a run workspace
    for the given prompt id. Empty dict if none."""
    fixtures_dir = Path(__file__).resolve().parent.parent / "data" / "workspaces" / "_fixtures"
    out: dict[str, str] = {}
    if prompt_id == "debug-pipeline":
        f = fixtures_dir / "buggy.sh"
        if f.exists():
            out["buggy.sh"] = f.read_text(encoding="utf-8")
    elif prompt_id == "code-review":
        f = fixtures_dir / "app.py"
        if f.exists():
            out["app.py"] = f.read_text(encoding="utf-8")
    return out
