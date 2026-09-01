/* InfinityArena frontend. No external deps. */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const API = "";  // same origin

  // ---------- navigation ----------
  const views = ["configure", "leaderboard", "sessions", "detail"];
  function showView(name) {
    views.forEach((v) => {
      const el = $("#view-" + v);
      if (el) el.classList.toggle("active", v === name);
    });
    $$(".navbtn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    if (name === "leaderboard") loadLeaderboard();
    if (name === "sessions") loadSessions();
  }
  $$(".navbtn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $("#backBtn").addEventListener("click", () => showView("leaderboard"));

  // ---------- toast ----------
  function toast(msg, isErr = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("error", isErr);
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 3500);
  }

  // ---------- prompts ----------
  async function loadPrompts() {
    const r = await fetch(API + "/api/prompts");
    const items = await r.json();
    const sel = $("#promptSelect");
    sel.innerHTML = items.map((p) => `<option value="${p.id}">${p.title}</option>`).join("");
    sel.onchange = () => fillPrompt(items.find((p) => p.id === sel.value));
    fillPrompt(items[0]);
  }
  async function fillPrompt(meta) {
    if (!meta) return;
    const r = await fetch(API + "/api/prompts/" + meta.id);
    const p = await r.json();
    $("#promptMeta").textContent =
      `${p.category} · expected tools: ${(p.expected_tools || []).join(", ") || "—"} · ` +
      `criteria: ${p.success_criteria || "—"}`;
    $("#promptText").value = p.prompt;
    window._currentPromptId = p.id;
  }
  $("#reloadPrompts").addEventListener("click", loadPrompts);

  // ---------- model rows ----------
  let modelSeq = 0;
  const modelsEl = $("#models");
  function addModel(prefill = {}) {
    const i = modelSeq++;
    const row = document.createElement("div");
    row.className = "model-row";
    row.dataset.idx = i;
    row.innerHTML = `
      <input class="m-name" placeholder="Display name" value="${prefill.name || ("model-" + (i + 1))}" />
      <input class="m-base" placeholder="base URL (e.g. https://api.openai.com/v1)" value="${prefill.base_url || "https://api.openai.com/v1"}" />
      <input class="m-model" placeholder="model id (e.g. gpt-4o-mini)" value="${prefill.model || ""}" />
      <input class="m-key" type="password" placeholder="api key (sk-...)" value="${prefill.api_key || ""}" />
      <input class="m-label" placeholder="optional label" value="${prefill.label || ""}" />
      <span class="x" title="remove">✕</span>
    `;
    row.querySelector(".x").addEventListener("click", () => row.remove());
    modelsEl.appendChild(row);
  }
  $("#addModel").addEventListener("click", () => addModel());
  // start with one empty model
  addModel();

  // ---------- run ----------
  $("#runBtn").addEventListener("click", async () => {
    const prompt = $("#promptText").value.trim();
    if (!prompt) return toast("Prompt is empty", true);
    const models = $$("#models .model-row").map((row) => {
      const get = (k) => row.querySelector(".m-" + k).value.trim();
      return {
        id: "m_" + row.dataset.idx,
        name: get("name") || ("model-" + row.dataset.idx),
        base_url: get("base"),
        model: get("model"),
        api_key: get("key"),
        label: get("label") || null,
      };
    }).filter((m) => m.base_url && m.model && m.api_key);
    if (!models.length) return toast("Add at least one fully-configured model", true);

    const judgeName = $("#judgeName").value.trim();
    const judge = judgeName ? {
      id: "judge",
      name: judgeName,
      base_url: $("#judgeBase").value.trim() || "https://api.openai.com/v1",
      model: $("#judgeModel").value.trim(),
      api_key: $("#judgeKey").value.trim(),
    } : null;
    if (judge && (!judge.model || !judge.api_key)) {
      return toast("Judge needs a model id and api key (or clear the name)", true);
    }

    const status = $("#runStatus");
    status.textContent = "Running…";
    $("#runBtn").disabled = true;
    try {
      const r = await fetch(API + "/api/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          prompt_id: window._currentPromptId || "custom",
          prompt,
          judge_model: judge,
          models,
          max_steps: 12,
          timeout_s: 180,
        }),
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t);
      }
      const { session_id } = await r.json();
      toast("Benchmark complete: " + session_id);
      window._lastSession = session_id;
      showView("leaderboard");
    } catch (e) {
      toast("Run failed: " + e.message, true);
    } finally {
      status.textContent = "";
      $("#runBtn").disabled = false;
    }
  });

  // ---------- leaderboard ----------
  let lbSort = { key: "avg", dir: "desc" };
  async function loadLeaderboard() {
    const r = await fetch(API + "/api/sessions");
    const sessions = await r.json();
    const rows = [];
    for (const s of sessions) {
      const full = await fetch(API + "/api/sessions/" + s.session_id).then((r) => r.json());
      for (const run of full.runs || []) {
        const j = run.judge || {};
        const avg = ((j.correctness || 0) + (j.tool_usage || 0) + (j.autonomy || 0) + (j.efficiency || 0)) / 4;
        rows.push({
          session_id: s.session_id,
          run_id: run.run_id,
          model: run.model_label || run.model_name,
          prompt: s.prompt_title,
          correctness: j.correctness || 0,
          tool_usage: j.tool_usage || 0,
          autonomy: j.autonomy || 0,
          efficiency: j.efficiency || 0,
          avg,
          runtime: run.metrics?.runtime_s || 0,
          tools: run.metrics?.tool_calls || 0,
          failed: run.metrics?.failed_tool_calls || 0,
          status: run.status,
        });
      }
    }
    $("#lbEmpty").style.display = rows.length ? "none" : "block";
    $("#lbTable").style.display = rows.length ? "" : "none";
    renderLeaderboard(rows);
  }
  function renderLeaderboard(rows) {
    const k = lbSort.key, dir = lbSort.dir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      const av = a[k], bv = b[k];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
    const body = $("#lbBody");
    body.innerHTML = rows.map((r, i) => `
      <tr data-session="${r.session_id}" data-run="${r.run_id}">
        <td>${i + 1}</td>
        <td>${esc(r.model)}</td>
        <td>${esc(r.prompt)}</td>
        <td>${r.correctness.toFixed(1)}</td>
        <td>${r.tool_usage.toFixed(1)}</td>
        <td>${r.autonomy.toFixed(1)}</td>
        <td>${r.efficiency.toFixed(1)}</td>
        <td><strong>${r.avg.toFixed(2)}</strong></td>
        <td>${r.runtime.toFixed(1)}s</td>
        <td>${r.tools}</td>
        <td>${r.failed}</td>
        <td><span class="badge ${r.status}">${r.status}</span></td>
        <td><button class="open">open</button></td>
      </tr>
    `).join("");
    body.querySelectorAll("button.open").forEach((b) => {
      b.addEventListener("click", (e) => {
        const tr = e.target.closest("tr");
        openRun(tr.dataset.session, tr.dataset.run);
      });
    });
    $$("#lbTable th").forEach((th) => {
      th.classList.toggle("sorted-asc", th.dataset.sort === k && lbSort.dir === "asc");
      th.classList.toggle("sorted-desc", th.dataset.sort === k && lbSort.dir === "desc");
    });
  }
  $$("#lbTable th").forEach((th) => {
    if (!th.dataset.sort) return;
    th.addEventListener("click", () => {
      if (lbSort.key === th.dataset.sort) lbSort.dir = lbSort.dir === "asc" ? "desc" : "asc";
      else { lbSort.key = th.dataset.sort; lbSort.dir = "desc"; }
      loadLeaderboard();
    });
  });

  // ---------- run detail ----------
  async function openRun(sessionId, runId) {
    const r = await fetch(API + "/api/sessions/" + sessionId);
    const s = await r.json();
    const run = (s.runs || []).find((x) => x.run_id === runId);
    if (!run) return toast("Run not found", true);
    $("#detailTitle").textContent = `${run.model_label || run.model_name} — ${s.prompt_title}`;
    $("#detailMeta").textContent = `run ${run.run_id} · ${new Date(s.created_at * 1000).toLocaleString()}`;
    const m = run.metrics || {};
    $("#detailStats").innerHTML = `
      <div class="stat"><div class="k">Runtime</div><div class="v">${(m.runtime_s || 0).toFixed(2)}s</div></div>
      <div class="stat"><div class="k">Tool calls</div><div class="v">${m.tool_calls || 0}</div></div>
      <div class="stat"><div class="k">Failed</div><div class="v">${m.failed_tool_calls || 0}</div></div>
      <div class="stat"><div class="k">Tokens</div><div class="v">${m.total_tokens || "—"}</div></div>
    `;
    const j = run.judge || {};
    $("#detailJudge").innerHTML = `
      <div class="stat"><div class="k">Correctness</div><div class="v">${(j.correctness || 0).toFixed(1)}</div></div>
      <div class="stat"><div class="k">Tool usage</div><div class="v">${(j.tool_usage || 0).toFixed(1)}</div></div>
      <div class="stat"><div class="k">Autonomy</div><div class="v">${(j.autonomy || 0).toFixed(1)}</div></div>
      <div class="stat"><div class="k">Efficiency</div><div class="v">${(j.efficiency || 0).toFixed(1)}</div></div>
      <div class="notes">${esc(j.notes || "")}</div>
    `;
    $("#detailFinal").textContent = j.final_answer || run.final_answer || "(no final answer)";
    const trace = $("#detailTrace");
    trace.innerHTML = "";
    for (const entry of (run.trace || [])) {
      const div = document.createElement("div");
      div.className = "entry " + (entry.role || "");
      let html = `<div class="role">${entry.role || "?"}</div>`;
      if (entry.role === "assistant") {
        if (entry.content) html += `<pre>${esc(entry.content)}</pre>`;
        if (entry.tool_calls) {
          for (const tc of entry.tool_calls) {
            html += `<div class="tool-call">▸ ${esc(tc.function.name)}(${esc(tc.function.arguments)})</div>`;
          }
        }
      } else if (entry.role === "tool") {
        html += `<div class="tool-call">↳ ${esc(entry.name)} (${esc(entry.tool_call_id || "")})</div>`;
        html += `<pre>${esc(entry.content || "")}</pre>`;
      } else if (entry.role === "_meta") {
        // attach compact tool log below
        const log = entry.tool_log || [];
        html = `<div class="role">tool log (${log.length})</div>`;
        for (const ev of log) {
          html += `<div class="tool-call">${ev.ok ? "✓" : "✗"} ${esc(ev.tool)}(${esc(JSON.stringify(ev.args).slice(0, 80))})</div>`;
          if (ev.error) html += `<pre>  error: ${esc(ev.error)}</pre>`;
          else if (ev.output_excerpt) html += `<pre>  ${esc(ev.output_excerpt.slice(0, 240))}</pre>`;
        }
      } else {
        html += `<pre>${esc(entry.content || "")}</pre>`;
      }
      div.innerHTML = html;
      trace.appendChild(div);
    }
    showView("detail");
  }

  // ---------- sessions ----------
  async function loadSessions() {
    const r = await fetch(API + "/api/sessions");
    const sessions = await r.json();
    const body = $("#sessBody");
    body.innerHTML = sessions.map((s) => `
      <tr>
        <td>${new Date(s.created_at * 1000).toLocaleString()}</td>
        <td>${esc(s.prompt_title)}</td>
        <td>${s.num_models}</td>
        <td>${s.avg_score.toFixed(2)}</td>
        <td><button data-s="${s.session_id}">open leaderboard</button></td>
      </tr>
    `).join("") || `<tr><td colspan="5" class="muted">No sessions yet.</td></tr>`;
    body.querySelectorAll("button[data-s]").forEach((b) => {
      b.addEventListener("click", () => showView("leaderboard"));
    });
  }

  // ---------- utils ----------
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // boot
  loadPrompts();
})();
