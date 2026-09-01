"""
Per-run tool implementations. Each model run gets its own ToolContext
so file edits, command output, and search history are isolated between
parallel runs. Tools exposed to the LLM: command, readFile, editFile,
websearch, webpg.
"""
from __future__ import annotations
import os
import json
import time
import uuid
import shutil
import subprocess
import asyncio
from pathlib import Path
from typing import Any

import httpx


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "data" / "workspaces"


class ToolContext:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.workspace = WORKSPACE_ROOT / run_id
        self.workspace.mkdir(parents=True, exist_ok=True)
        # seed a README so the model has something to read
        (self.workspace / "README.md").write_text(
            f"# Workspace for run {run_id}\n\nUse `command`, `readFile`, "
            "`editFile`, `websearch`, and `webpg` to complete the task.\n"
        )
        # file -> (sha before edit). lets the model inspect diffs itself
        self._edit_history: list[dict[str, Any]] = []
        # record of every tool invocation for the judge & trace
        self.tool_log: list[dict[str, Any]] = []

    # --- helpers -----------------------------------------------------------
    def _record(self, name: str, args: dict[str, Any], ok: bool, output: str, error: str | None = None):
        self.tool_log.append({
            "ts": time.time(),
            "tool": name,
            "args": args,
            "ok": ok,
            "error": error,
            "output_excerpt": output[:1200],
        })

    def _safe_path(self, rel: str) -> Path:
        p = (self.workspace / rel).resolve()
        ws = self.workspace.resolve()
        if not str(p).startswith(str(ws)):
            raise ValueError(f"path escapes workspace: {rel}")
        return p

    # --- tool implementations ---------------------------------------------
    async def command(self, cmd: str, cwd: str | None = None, timeout_s: int = 30) -> dict[str, Any]:
        args = {"cmd": cmd, "cwd": cwd, "timeout_s": timeout_s}
        try:
            work_dir = self._safe_path(cwd) if cwd else self.workspace
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                stdout_b, stderr_b = await proc.communicate()
                out = (stdout_b or b"").decode("utf-8", "replace")
                err = (stderr_b or b"").decode("utf-8", "replace") + f"\n[timeout after {timeout_s}s]"
                self._record("command", args, False, out + err, "timeout")
                return {"ok": False, "stdout": out, "stderr": err, "exit_code": -1}
            out = (stdout_b or b"").decode("utf-8", "replace")
            err = (stderr_b or b"").decode("utf-8", "replace")
            ok = proc.returncode == 0
            self._record("command", args, ok, out + err, None if ok else err or f"exit {proc.returncode}")
            return {"ok": ok, "stdout": out, "stderr": err, "exit_code": proc.returncode}
        except Exception as e:
            self._record("command", args, False, "", str(e))
            return {"ok": False, "stdout": "", "stderr": str(e), "exit_code": -1}

    async def read_file(self, path: str) -> dict[str, Any]:
        args = {"path": path}
        try:
            p = self._safe_path(path)
            if not p.exists():
                self._record("readFile", args, False, "", "file not found")
                return {"ok": False, "error": f"file not found: {path}"}
            text = p.read_text(encoding="utf-8", errors="replace")
            self._record("readFile", args, True, text)
            return {"ok": True, "content": text, "size": len(text)}
        except Exception as e:
            self._record("readFile", args, False, "", str(e))
            return {"ok": False, "error": str(e)}

    async def edit_file(self, path: str, old_string: str, new_string: str) -> dict[str, Any]:
        args = {"path": path, "old_string": old_string, "new_string": new_string}
        try:
            p = self._safe_path(path)
            if not p.exists():
                self._record("editFile", args, False, "", "file not found")
                return {"ok": False, "error": f"file not found: {path}"}
            original = p.read_text(encoding="utf-8", errors="replace")
            if old_string not in original:
                self._record("editFile", args, False, "", "old_string not found in file")
                return {"ok": False, "error": "old_string not found in file"}
            updated = original.replace(old_string, new_string, 1)
            p.write_text(updated, encoding="utf-8")
            self._edit_history.append({"path": path, "ts": time.time()})
            self._record("editFile", args, True, f"replaced {len(old_string)} bytes")
            return {"ok": True, "path": path, "new_size": len(updated)}
        except Exception as e:
            self._record("editFile", args, False, "", str(e))
            return {"ok": False, "error": str(e)}

    async def web_search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """Lightweight in-process search: queries DuckDuckGo's HTML endpoint
        and parses out the first N result titles + urls. No API key needed.
        Falls back to a Wikipedia REST summary if DDG is blocked."""
        args = {"query": query, "max_results": max_results}
        try:
            results: list[dict[str, str]] = []
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                         headers={"User-Agent": "InfinityArena/1.0"}) as client:
                try:
                    r = await client.get(
                        "https://duckduckgo.com/html/",
                        params={"q": query, "kl": "us-en"},
                    )
                    if r.status_code == 200 and "result" in r.text.lower():
                        # very small parse — enough for benchmarks
                        import re
                        for m in re.finditer(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                             r.text, flags=re.S):
                            url = m.group(1)
                            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                            results.append({"title": title, "url": url})
                            if len(results) >= max_results:
                                break
                except Exception:
                    pass

                if not results:
                    try:
                        r2 = await client.get(
                            "https://en.wikipedia.org/api/rest_v1/page/summary/" + query.replace(" ", "_")
                        )
                        if r2.status_code == 200:
                            j = r2.json()
                            results.append({
                                "title": j.get("title", query),
                                "url": j.get("content_urls", {}).get("desktop", {}).get("page", ""),
                                "snippet": j.get("extract", ""),
                            })
                    except Exception:
                        pass

            if not results:
                self._record("websearch", args, False, "", "no results")
                return {"ok": False, "error": "no results returned", "results": []}
            out = json.dumps(results, ensure_ascii=False)
            self._record("websearch", args, True, out)
            return {"ok": True, "results": results}
        except Exception as e:
            self._record("websearch", args, False, "", str(e))
            return {"ok": False, "error": str(e)}

    async def web_page(self, url: str, max_chars: int = 8000) -> dict[str, Any]:
        args = {"url": url, "max_chars": max_chars}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers={"User-Agent": "InfinityArena/1.0"}) as client:
                r = await client.get(url)
                text = r.text
                # strip scripts/styles
                import re
                text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
                text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > max_chars:
                    text = text[:max_chars] + "..."
                self._record("webpg", args, True, text)
                return {"ok": True, "status": r.status_code, "content": text, "url": str(r.url)}
        except Exception as e:
            self._record("webpg", args, False, "", str(e))
            return {"ok": False, "error": str(e)}

    # JSON schema for the chat-completion tools list
    @staticmethod
    def schemas() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "command",
                    "description": "Run a shell command in the run's isolated workspace. Returns stdout, stderr, exit code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string", "description": "Shell command to execute"},
                            "cwd": {"type": "string", "description": "Relative path inside workspace (optional)"},
                            "timeout_s": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                        },
                        "required": ["cmd"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "readFile",
                    "description": "Read a UTF-8 text file from the run's isolated workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "editFile",
                    "description": "Replace the first occurrence of old_string with new_string in a workspace file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "websearch",
                    "description": "Search the public web. Returns titles and URLs (and a snippet when available).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "webpg",
                    "description": "Fetch a web page and return its visible text (HTML stripped).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "max_chars": {"type": "integer", "default": 8000},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]
