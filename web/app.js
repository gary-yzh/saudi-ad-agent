const SAMPLE_BRIEF =
  "Promote our premium Ajwa dates collection for the upcoming Ramadan campaign. " +
  "Target audience: Saudi families, ages 25-45, gifting for iftar gatherings. " +
  "Single 9:16 short-form video, ≤15 seconds, bilingual (Arabic VO + English overlay). " +
  "Objective: drive product page visits.";

// ----- Field registry --------------------------------------------------------
// Each entry: input id, localStorage key, post key, type ("string"|"bool"|"int")
const FIELDS = [
  // LLM
  { id: "api-key",       store: "v2.openai-key",       post: "api_key",                  type: "string", required: true,  reveal: "llm" },
  { id: "base-url",      store: "v2.openai-base-url",  post: "base_url",                 type: "string" },
  { id: "model-name",    store: "v2.openai-model",     post: "model",                    type: "string" },
  // Ark
  { id: "ark-key",       store: "v2.ark-key",          post: "ark_api_key",              type: "string", required: true,  reveal: "ark" },
  { id: "ark-base-url",  store: "v2.ark-base-url",     post: "ark_base_url",             type: "string" },
  { id: "image-model",   store: "v2.image-model",      post: "image_model",              type: "string" },
  { id: "image-size",    store: "v2.image-size",       post: "image_size",               type: "string" },
  { id: "image-watermark", store: "v2.image-watermark", post: "image_watermark",         type: "bool" },
  { id: "video-model",   store: "v2.video-model",      post: "video_model",              type: "string" },
  { id: "video-ratio",   store: "v2.video-ratio",      post: "video_ratio",              type: "string" },
  { id: "video-duration", store: "v2.video-duration",  post: "video_duration",           type: "int" },
  { id: "video-generate-audio", store: "v2.video-gen-audio", post: "video_generate_audio", type: "bool" },
  { id: "video-watermark", store: "v2.video-watermark", post: "video_watermark",         type: "bool" },
  // TTS
  { id: "tts-key",         store: "v2.tts-key",         post: "tts_api_key",             type: "string", required: true, reveal: "tts" },
  { id: "tts-url",         store: "v2.tts-url",         post: "tts_url",                 type: "string" },
  { id: "tts-resource-id", store: "v2.tts-resource",    post: "tts_resource_id",         type: "string" },
  { id: "tts-speaker",     store: "v2.tts-speaker",     post: "tts_speaker",             type: "string" },
  { id: "tts-format",      store: "v2.tts-format",      post: "tts_format",              type: "string" },
  { id: "tts-sample-rate", store: "v2.tts-sample-rate", post: "tts_sample_rate",         type: "int" },
  { id: "tts-speech-rate", store: "v2.tts-speech-rate", post: "tts_speech_rate",         type: "int" },
  { id: "tts-loudness-rate", store: "v2.tts-loudness", post: "tts_loudness_rate",        type: "int" },
  { id: "tts-emotion",     store: "v2.tts-emotion",     post: "tts_emotion",             type: "string" },
  { id: "tts-emotion-scale", store: "v2.tts-emotion-scale", post: "tts_emotion_scale",   type: "int" },
  { id: "tts-silence-duration", store: "v2.tts-silence", post: "tts_silence_duration",   type: "int" },
  { id: "tts-explicit-language", store: "v2.tts-lang", post: "tts_explicit_language",    type: "string" },
];

const $ = (id) => document.getElementById(id);

function readField(field) {
  const el = $(field.id);
  if (!el) return null;
  if (field.type === "bool") return el.checked;
  return (el.value ?? "").toString().trim();
}

function writeField(field, value) {
  const el = $(field.id);
  if (!el) return;
  if (field.type === "bool") el.checked = !!value;
  else el.value = value ?? "";
}

function persistField(field) {
  const v = readField(field);
  const empty = field.type === "bool" ? !v : !v;
  if (empty) localStorage.removeItem(field.store);
  else localStorage.setItem(field.store, field.type === "bool" ? "1" : String(v));
}

function loadField(field) {
  const raw = localStorage.getItem(field.store);
  if (raw == null) return;
  if (field.type === "bool") writeField(field, raw === "1");
  else writeField(field, raw);
}

function buildPayload(brief) {
  const payload = {
    brief,
    locale: $("locale").value,
    target_audience: $("audience").value || "Saudi adults 25-45, parents, urban",
  };
  for (const f of FIELDS) {
    const v = readField(f);
    if (f.type === "bool") {
      payload[f.post] = !!v;
    } else if (f.type === "int") {
      const n = v === "" ? null : parseInt(v, 10);
      payload[f.post] = Number.isFinite(n) ? n : null;
    } else {
      payload[f.post] = v ? v : null;
    }
  }
  return payload;
}

function tryHostname(url) {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

// ----- Status badges ---------------------------------------------------------
function refreshStatus() {
  const llmOk = !!readField(FIELDS.find((f) => f.id === "api-key"));
  const arkOk = !!readField(FIELDS.find((f) => f.id === "ark-key"));
  const ttsOk = !!readField(FIELDS.find((f) => f.id === "tts-key"));

  const set = (id, ok) => {
    const el = $(id);
    el.textContent = ok ? "ready" : "missing";
    el.classList.toggle("ready", ok);
    el.classList.toggle("missing", !ok);
  };
  set("llm-status", llmOk);
  set("ark-status", arkOk);
  set("tts-status", ttsOk);

  const allOk = llmOk && arkOk && ttsOk;
  $("run-btn").disabled = !allOk;
  $("run-gate").classList.toggle("hidden", allOk);

  const badge = $("mode-badge");
  if (allOk) {
    const llmHost = tryHostname(readField(FIELDS.find((f) => f.id === "base-url"))) || "openai";
    badge.textContent = `LIVE · ${llmHost} + Doubao`;
    badge.className = "mode-badge live";
    badge.title = "All three providers configured.";
  } else {
    const missing = [!llmOk && "LLM", !arkOk && "Ark", !ttsOk && "TTS"]
      .filter(Boolean)
      .join(", ");
    badge.textContent = `UNCONFIGURED · need ${missing}`;
    badge.className = "mode-badge unconfigured";
    badge.title = "Fill the highlighted sections.";
  }
}

// ----- Init ------------------------------------------------------------------
(function init() {
  // Load persisted values
  FIELDS.forEach(loadField);

  // Wire each field to persist + refresh status
  FIELDS.forEach((f) => {
    const el = $(f.id);
    if (!el) return;
    const evt = el.tagName === "SELECT" || f.type === "bool" ? "change" : "input";
    el.addEventListener(evt, () => {
      persistField(f);
      refreshStatus();
    });
  });

  // Open the section corresponding to the first missing required key
  const missingFor = FIELDS.filter((f) => f.required).find((f) => !readField(f));
  const reveal = missingFor?.reveal;
  if (reveal === "llm") $("llm-section").open = true;
  else if (reveal === "ark") $("ark-section").open = true;
  else if (reveal === "tts") $("tts-section").open = true;
  else {
    $("llm-section").open = true;
    $("ark-section").open = true;
    $("tts-section").open = true;
  }

  // Toggles for password fields
  const wireToggle = (btn, input) => {
    $(btn).addEventListener("click", () => {
      const el = $(input);
      el.type = el.type === "password" ? "text" : "password";
    });
  };
  wireToggle("toggle-key", "api-key");
  wireToggle("toggle-ark-key", "ark-key");
  wireToggle("toggle-tts-key", "tts-key");

  // Sample brief
  $("load-sample").addEventListener("click", () => {
    $("brief").value = SAMPLE_BRIEF;
    $("brief").focus();
  });

  // Clear all
  $("clear-all").addEventListener("click", () => {
    if (!confirm("Clear all saved keys and settings?")) return;
    FIELDS.forEach((f) => {
      if (f.type === "bool") writeField(f, false);
      else writeField(f, "");
      localStorage.removeItem(f.store);
    });
    refreshStatus();
  });

  refreshStatus();
})();

// ----- Submit ----------------------------------------------------------------
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
      body: JSON.stringify(buildPayload(brief)),
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
  // Front-load the first three steps; tool_use stays "active" for the long
  // video-gen wait. The success handler marks all done.
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

// ----- Render ----------------------------------------------------------------
function render(data) {
  $("loading").classList.add("hidden");
  $("result").classList.remove("hidden");
  refreshStatus(); // re-enable button

  const sb = data.storyboard || {};

  let runLine = `run · ${data.run_id || ""}`;
  if (data._model) runLine += `  ·  LLM: ${data._model}`;
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
  setText("sb-vo", sb.voiceover);
  setText("sb-voice", sb.voice);

  setLink("image-url", data.image_url);
  setLink("video-url", data.video_url);
  setLink("audio-url", data.audio_url);

  $("image-preview").src = data.image_url || "";
  $("video-preview").src = data.video_url || "";
  $("audio-preview").src = data.audio_url || "";

  // Partial-success banner if any tool step was skipped or blocked
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

  $("image-caption").textContent = "Image · " + (data._image_model || "Seedream");
  $("video-caption").textContent = "Video · " + (data._video_model || "Seedance");
  $("audio-caption").textContent = "Voiceover · " + (data._tts_resource_id || "TTS");

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

function setText(id, value) {
  $(id).textContent = value && String(value).length ? value : "—";
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
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
  refreshStatus();
}
