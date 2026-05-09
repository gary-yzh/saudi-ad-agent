// Run page — brief → /api/run → render storyboard, assets, eval, and a
// pipeline timeline showing every node + every moderation hit / softening.
// Settings live server-side now; this page only reads /api/config/status to
// gate the Generate button and surface a "go to Settings" hint.

const SAMPLE_BRIEF =
  "Promote our premium Ajwa dates collection for the upcoming Ramadan campaign. " +
  "Target audience: Saudi families, ages 25-45, gifting for iftar gatherings. " +
  "Single 9:16 short-form video, ≤15 seconds, bilingual (Arabic VO + English overlay). " +
  "Objective: drive product page visits.";

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

// ---------- Status gating ----------------------------------------------------
async function refreshConfigBadge() {
  let status;
  try {
    const r = await fetch("/api/config/status");
    status = await r.json();
  } catch {
    status = { configured: false, missing: ["?"] };
  }
  const badge = $("config-badge");
  if (status.configured) {
    badge.textContent = "READY";
    badge.title = "All required keys configured.";
    badge.className = "mode-badge live";
    $("run-btn").disabled = false;
    $("run-gate").classList.add("hidden");
  } else {
    const miss = (status.missing || []).map((k) => k.replace(/^openai_/, "llm/")).join(", ");
    badge.textContent = `UNCONFIGURED · ${miss}`;
    badge.title = "Open Settings and fill in the missing keys.";
    badge.className = "mode-badge unconfigured";
    $("run-btn").disabled = true;
    $("run-gate").classList.remove("hidden");
  }
}

// ---------- Sample brief -----------------------------------------------------
$("load-sample").addEventListener("click", () => {
  $("brief").value = SAMPLE_BRIEF;
  $("brief").focus();
});

// ---------- Submit -----------------------------------------------------------
$("brief-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const brief = $("brief").value.trim();
  if (brief.length < 10) return;
  if ($("run-btn").disabled) return;

  showLoading();
  startStepsAnimation();

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief,
        locale: $("locale").value,
        target_audience: $("audience").value || "Saudi adults 25-45, parents, urban",
      }),
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        msg += ` — ${j.detail || JSON.stringify(j)}`;
      } catch {
        msg += ` — ${(await res.text()).slice(0, 800)}`;
      }
      throw new Error(msg);
    }
    const data = await res.json();
    completeSteps();
    setTimeout(() => render(data), 200);
  } catch (err) {
    showError(err.message || String(err));
  }
});

function showLoading() {
  $("placeholder").classList.add("hidden");
  $("result").classList.add("hidden");
  $("error").classList.add("hidden");
  $("loading").classList.remove("hidden");
  $("run-btn").disabled = true;
  $("run-btn").textContent = "Running…";
  $("loading-sub").textContent =
    "Calling Doubao. Image is fast; Seedance video typically takes 3–5 min, " +
    "but can queue up to 30 min on busy days. Don't close this tab.";
  document.querySelectorAll("#steps li").forEach((li) => {
    li.classList.remove("active", "done");
  });
}

function startStepsAnimation() {
  // Front-load the first three steps; tool_use stays "active" through the
  // long video-gen wait. Success handler marks all done.
  const seq = [
    { id: "rag", at: 0 },
    { id: "planner", at: 600 },
    { id: "guardrail", at: 4500 },
    { id: "tool_use", at: 6000 },
  ];
  let prevId = null;
  seq.forEach(({ id, at }) => {
    setTimeout(() => {
      if (prevId) {
        const prev = document.querySelector(`#steps li[data-step="${prevId}"]`);
        if (prev) {
          prev.classList.remove("active");
          prev.classList.add("done");
        }
      }
      const el = document.querySelector(`#steps li[data-step="${id}"]`);
      if (el) el.classList.add("active");
      prevId = id;
    }, at);
  });
}

function completeSteps() {
  document.querySelectorAll("#steps li").forEach((li) => {
    li.classList.remove("active");
    li.classList.add("done");
  });
}

// ---------- Render -----------------------------------------------------------
function render(data) {
  $("loading").classList.add("hidden");
  $("result").classList.remove("hidden");
  $("run-btn").disabled = false;
  $("run-btn").textContent = "Generate creative";
  refreshConfigBadge();

  const sb = data.storyboard || {};

  let runLine = `run · ${data.run_id || ""}`;
  if (data._llm_model) runLine += `  ·  LLM: ${data._llm_model}`;
  if (data._image_model) runLine += `  ·  ${data._image_model}`;
  if (data._video_model) runLine += `  ·  ${data._video_model}`;
  if (data._tts_resource_id) runLine += `  ·  ${data._tts_resource_id}`;
  $("run-id").textContent = runLine;

  const status = data.eval_status || "fail";
  const badge = $("status-badge");
  badge.textContent = status;
  badge.classList.toggle("pass", status === "pass");
  badge.classList.toggle("fail", status !== "pass");

  const ctrPct = (data.ctr_estimate || 0) * 100;
  $("ctr-value").textContent = `${ctrPct.toFixed(2)}%`;

  setText("sb-hook", sb.hook);
  setText("sb-body", sb.body);
  setText("sb-cta", sb.cta);
  setText("sb-visual", sb.visual_prompt);
  setText("sb-motion", sb.motion_prompt);
  // Voiceover may be RTL (Arabic) or LTR depending on locale
  const vo = sb.voiceover || "";
  const voEl = $("sb-vo");
  voEl.textContent = vo || "—";
  const isArabic = /[؀-ۿ]/.test(vo);
  voEl.classList.toggle("rtl", isArabic);
  if (isArabic) voEl.setAttribute("lang", "ar");
  else voEl.removeAttribute("lang");
  setText("sb-voice", sb.voice);

  // Asset previews + URL list
  setLink("image-url", data.image_url);
  setLink("video-url", data.video_url);
  setLink("audio-url", data.audio_url);
  $("image-preview").src = data.image_url || "";
  $("video-preview").src = data.video_url || "";
  $("audio-preview").src = data.audio_url || "";
  $("image-caption").textContent = "Image · " + (data._image_model || "Seedream");
  $("video-caption").textContent = "Video · " + (data._video_model || "Seedance");
  $("audio-caption").textContent = "Voiceover · " + (data._tts_resource_id || "TTS");

  // Partial-success banner
  const errors = data.errors || [];
  const banner = $("partial-banner");
  const missing = [];
  if (!data.image_url) missing.push("image");
  if (!data.video_url) missing.push("video");
  if (!data.audio_url) missing.push("audio");
  if (missing.length || errors.length) {
    banner.classList.remove("hidden");
    const causeList = errors.length
      ? `<ul>${errors.map((e) => `<li>${escapeHtml(String(e))}</li>`).join("")}</ul>`
      : "";
    banner.innerHTML =
      `<strong>Partial result.</strong> Missing: ${missing.join(", ") || "none"}.` +
      causeList;
  } else {
    banner.classList.add("hidden");
    banner.innerHTML = "";
  }

  // Pipeline timeline (intermediate states)
  renderPipelineTrace(data.log || []);

  // Eval block
  setText("eval-status", data.eval_status);
  setText("guardrail-status", data.guardrail_status);
  $("guardrail-rev").textContent = data.guardrail_revision_count ?? 0;
  const notesEl = $("eval-notes");
  notesEl.innerHTML = "";
  (data.eval_notes || []).forEach((n) => {
    const li = document.createElement("li");
    li.textContent = n;
    notesEl.appendChild(li);
  });
}

// Build the timeline from the per-node log dict that the graph emits
function renderPipelineTrace(logEntries) {
  const root = $("pipeline-trace");
  root.innerHTML = "";

  const items = [];
  for (const entry of logEntries) {
    const node = entry.node;
    if (node === "rag") {
      items.push({
        kind: "ok",
        label: "RAG",
        text: `Loaded ${entry.rules_loaded ?? 0} brand rules`,
      });
    } else if (node === "planner") {
      items.push({
        kind: "ok",
        label: `Planner${entry.revision ? ` · revision #${entry.revision}` : ""}`,
        text: entry.hook ? `Hook: "${entry.hook}"` : "Drafted storyboard",
      });
    } else if (node === "guardrail") {
      items.push({
        kind: entry.status === "pass" ? "ok" : "fail",
        label: `Guardrail · ${entry.status}`,
        text:
          (entry.violations && entry.violations.length)
            ? `Violations: ${entry.violations.join("; ")}`
            : "No violations",
      });
    } else if (node === "tool_use") {
      for (const c of entry.calls || []) {
        if (c.tool === "seedream") {
          if (c.status === "failed") {
            items.push({ kind: "fail", label: "Image (Seedream)", text: c.error });
          } else {
            items.push({
              kind: "ok",
              label: "Image (Seedream)",
              text: `${c.size || ""} · ${(c.latency_ms || 0).toLocaleString()}ms${c.attempts > 1 ? ` · ${c.attempts} attempts` : ""}`,
            });
          }
        } else if (c.tool === "seedance") {
          if (c.status === "failed") {
            items.push({ kind: "fail", label: "Video (Seedance)", text: c.error });
          } else {
            items.push({
              kind: "ok",
              label: "Video (Seedance)",
              text: `${c.duration_s || "?"}s · ratio ${c.ratio || "?"} · ${(c.latency_ms || 0).toLocaleString()}ms${c.task_id ? ` · task ${c.task_id}` : ""}`,
            });
          }
        } else if (c.tool === "tts") {
          if (c.status === "failed") {
            items.push({ kind: "fail", label: "Voiceover (TTS)", text: c.error });
          } else {
            items.push({
              kind: "ok",
              label: "Voiceover (TTS)",
              text: `${c.bytes ? c.bytes.toLocaleString() + ' B ' : ''}${c.format || ""} @ ${c.sample_rate || "?"}Hz · ${(c.latency_ms || 0).toLocaleString()}ms`,
            });
          }
        } else if (c.event === "moderation_hit") {
          items.push({
            kind: "warn",
            label: `Moderation hit · ${c.stage}${c.attempt > 0 ? ` · attempt ${c.attempt}` : ""}`,
            text: `${c.code}: ${c.message || ""}`,
          });
        } else if (c.event === "prompt_softened") {
          items.push({
            kind: "warn",
            label: `Prompt softened · ${c.stage} (attempt ${c.attempt})`,
            text: `→ ${c.new_prompt_head || ""}…`,
          });
        }
      }
    } else if (node === "eval") {
      items.push({
        kind: entry.status === "pass" ? "ok" : "fail",
        label: `Eval · ${entry.status}`,
        text: `CTR: ${(entry.ctr * 100).toFixed(2)}%`,
      });
    }
  }

  for (const it of items) {
    const li = document.createElement("li");
    li.className = `trace-item trace-${it.kind}`;
    li.innerHTML =
      `<span class="trace-label">${escapeHtml(it.label)}</span>` +
      `<span class="trace-text">${escapeHtml(it.text || "")}</span>`;
    root.appendChild(li);
  }
  if (!items.length) {
    root.innerHTML = '<li class="trace-item trace-ok"><span class="trace-text">(no entries)</span></li>';
  }
}

function setText(id, value) {
  $(id).textContent = value && String(value).length ? value : "—";
}

function setLink(id, url) {
  const el = $(id);
  if (!url) {
    el.textContent = "—";
    el.removeAttribute("href");
    return;
  }
  el.textContent = url;
  el.href = url;
}

function showError(msg) {
  $("loading").classList.add("hidden");
  $("result").classList.add("hidden");
  $("error").classList.remove("hidden");
  $("error-msg").textContent = msg;
  $("run-btn").textContent = "Generate creative";
  refreshConfigBadge();
}

// ---------- Init ------------------------------------------------------------
refreshConfigBadge();
