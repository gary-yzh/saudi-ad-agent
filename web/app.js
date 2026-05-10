// Multi-step ad-creation flow.
// State machine:
//   chat               (user types brief, LLM may ask clarifying Qs)
//   storyboard_draft   (assistant proposed a storyboard; user can confirm or refine)
//   images_running     (Seedream calls fanned out, polling)
//   images_done        (user picks shots)
//   video_running      (Seedance call running, polling)
//   video_done         (local mp4 playable)
//
// Session id lives in sessionStorage (per-tab) so a page reload resumes the
// same flow but '+ New session' opens a clean tab without disturbing the
// original.

// Sample brief, sectioned. Picks a low-moderation-risk product (premium
// coffee, packaging-focused, no people in frame) so first-time users can
// run the whole pipeline end-to-end without hitting Doubao's culturally-
// sensitive image / video moderation. The four-section layout doubles as
// an implicit template — users replacing it with their own brief get a
// suggested structure (product+audience / specs / visuals / objective).
const SAMPLE_BRIEF = `1. Product & Target Audience
Promote our "Arabia Roast" premium single-origin coffee bean collection — medium roast, hand-picked, foil-sealed bag — for working professionals across the GCC, ages 25–45.

2. Video Specifications
Single 9:16 short-form video, ≤15 seconds, English voiceover.

3. Visual Requirements
Focus on the product packaging and brewing close-ups; no people in frame, no specific cultural settings.

4. Marketing Objective
Drive product page visits.`;

const STORE_KEY = "saa.session_id";
const $ = (id) => document.getElementById(id);

// Per-tab session storage. sessionStorage scopes to a tab, which is exactly
// what we want so each '+ New session' tab is independent without disturbing
// the original. One-time migration moves any old localStorage value over so
// returning users don't lose their in-progress session on the first reload
// after this change.
function loadSessionId() {
  const fromLocal = localStorage.getItem(STORE_KEY);
  if (fromLocal && !sessionStorage.getItem(STORE_KEY)) {
    sessionStorage.setItem(STORE_KEY, fromLocal);
  }
  // Always clear localStorage — the new contract is per-tab.
  if (fromLocal) localStorage.removeItem(STORE_KEY);
  return sessionStorage.getItem(STORE_KEY);
}
function saveSessionId(id) {
  if (id) sessionStorage.setItem(STORE_KEY, id);
  else sessionStorage.removeItem(STORE_KEY);
}
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

let SESSION_ID = null;
let imagePollHandle = null;
let videoPollHandle = null;

// Brand logo state, mirrored client-side. The logo chip lives inside the
// dynamically-rendered last storyboard shot; we read this on every render so
// it shows up in the right state even after a re-draft swaps the last shot.
let _brandLogoInfo = null;

// Stepper state machine — must be declared BEFORE the init() IIFE that
// calls setStepperFromState (the function is hoisted, but a `const` it
// references would otherwise be in the TDZ at IIFE-run time and throw,
// killing every listener binding below it).
const STEP_ORDER = ["brief", "storyboard", "stills", "video"];

// ---------- Boot ------------------------------------------------------------
//
// Init in two phases. Phase 1 is fully synchronous so a network hiccup or a
// missing element can never starve the rest of the listeners — past
// versions had `await refreshConfigBadge()` blocking the load-sample
// binding behind it, which is why the button silently no-op'd for some
// users.

(function init() {
  setStepperFromState("chat");

  // ---- Phase 1: synchronous listeners ----

  // Sample brief — load into textarea and focus.
  $("load-sample").addEventListener("click", () => {
    const ti = $("chat-input");
    ti.value = SAMPLE_BRIEF;
    ti.dispatchEvent(new Event("input"));
    ti.focus();
  });

  // Chat input typing & Enter-to-send (Shift+Enter for newline).
  $("chat-input").addEventListener("input", () => {
    $("chat-send").disabled = $("chat-input").value.trim().length === 0;
    autosizeChatInput();
  });
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!$("chat-send").disabled) $("chat-form").requestSubmit();
    }
  });

  // Forms / panel buttons.
  $("chat-form").addEventListener("submit", onSendMessage);
  $("confirm-storyboard-btn").addEventListener("click", onConfirmStoryboard);
  $("redraft-storyboard-btn").addEventListener("click", onRedraftStoryboard);
  $("generate-video-btn").addEventListener("click", onGenerateVideo);
  $("new-session-btn").addEventListener("click", onNewSession);

  // Brand manual chip is static (lives in the brief panel) — bind directly.
  wireAssetChip({
    chipId: "pdf-chip",
    inputId: "brand-rag-file",
    removeId: "pdf-chip-remove",
    onPick: onBrandManualPicked,
    onRemove: onBrandManualRemove,
  });

  // Logo chip is dynamic (rendered into the last storyboard shot). The
  // hidden file input is static, so bind its change once; clicks on the
  // chip + the inner × are handled by document-level delegation below.
  $("brand-logo-file").addEventListener("change", onBrandLogoPicked);

  // Defensive document-level fallback for the sample button — if anything
  // above ever throws, this still works. Also handles the click on a
  // broken-image area (img.onerror flips .image-broken on; the whole strip
  // is clickable for one-click regenerate).
  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    if (t.id === "load-sample" || t.closest("#load-sample")) {
      const ti = $("chat-input");
      if (ti && !ti.value) {
        ti.value = SAMPLE_BRIEF;
        ti.dispatchEvent(new Event("input"));
        ti.focus();
      }
      return;
    }
    const brokenMedia = t.closest(".image-media.image-broken");
    if (brokenMedia) {
      const card = brokenMedia.closest(".image-card");
      const shotId = Number(card?.dataset.shotId);
      if (shotId) triggerShotRetry(shotId, null);
      return;
    }
    // Logo chip lives inside the last storyboard shot and is re-rendered on
    // every showStoryboard() call, so handle its clicks via delegation:
    //   • inner × → remove the uploaded logo
    //   • anywhere else on the chip → open the file picker
    const logoRemove = t.closest("#logo-chip-remove");
    if (logoRemove) {
      e.preventDefault();
      e.stopPropagation();
      onBrandLogoRemove();
      return;
    }
    const logoChip = t.closest("#logo-chip");
    if (logoChip) {
      $("brand-logo-file").click();
      return;
    }
  });

  // ---- Phase 2: async work (fire-and-forget; never blocks listeners) ----

  refreshConfigBadge().catch((err) => console.warn("config status failed:", err));
  // Also derive a preview of the voiceover language from the saved TTS
  // speaker so the user sees the indicator before they even start chatting.
  previewVoiceoverLocale().catch(() => {});

  const stored = loadSessionId();
  if (stored) {
    SESSION_ID = stored;
    fetch(`/api/sessions/${SESSION_ID}`)
      .then((r) => (r.ok ? r.json() : Promise.reject("404")))
      .then(restoreView)
      .catch(() => {
        saveSessionId(null);
        SESSION_ID = null;
      });
  }
})();

// Grow the chat textarea to fit its content (capped) so a freshly loaded
// sample brief doesn't get clipped behind the default 4-row height.
function autosizeChatInput() {
  const ti = $("chat-input");
  if (!ti) return;
  ti.style.height = "auto";
  const cap = 320; // px
  ti.style.height = Math.min(ti.scrollHeight, cap) + "px";
}

function wireAssetChip({ chipId, inputId, removeId, onPick, onRemove }) {
  const chip = $(chipId);
  const input = $(inputId);
  const removeEl = $(removeId);
  if (!chip || !input) return;

  chip.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    // Don't open file picker when the user is clicking the inner remove ×
    if (t && (t === removeEl || (removeEl && removeEl.contains(t)))) return;
    input.click();
  });
  if (removeEl) {
    removeEl.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      onRemove();
    });
  }
  input.addEventListener("change", onPick);
}

// ---------- Config status ---------------------------------------------------
//
// Apple-style: indicator is silent when everything works. Only appears when
// the user needs to do something. Toggles a CSS class on the Settings link
// itself — a small red ::before dot shows up when config is incomplete.
// No separate badge element competing with the link for click area.
async function refreshConfigBadge() {
  let status;
  try {
    status = await fetch("/api/config/status").then((r) => r.json());
  } catch {
    status = { configured: false, missing: ["?"] };
  }
  const link = $("settings-link");
  if (!link) return !!status.configured;
  if (status.configured) {
    link.classList.remove("needs-setup");
    link.removeAttribute("title");
  } else {
    link.classList.add("needs-setup");
    const missing = status.missing || [];
    link.title = missing.length
      ? `Setup needed: ${missing.join(", ")}.`
      : "Setup needed — click to configure.";
  }
  return !!status.configured;
}

// ---------- Stepper (left rail) --------------------------------------------
// STEP_ORDER is declared at the top of the file because init() needs it.

function setStepperFromState(state) {
  // Map session.state → which step is currently active
  const stateMap = {
    chat: "brief",
    storyboard_draft: "storyboard",
    storyboard_confirmed: "stills",
    images_running: "stills",
    images_done: "stills",
    video_running: "video",
    video_done: "video",
  };
  const current = stateMap[state] || "brief";
  const currentIdx = STEP_ORDER.indexOf(current);
  const allDone = state === "video_done";
  document.querySelectorAll("#stepper .step").forEach((el) => {
    const i = STEP_ORDER.indexOf(el.dataset.step);
    el.classList.remove("step-pending", "step-current", "step-done");
    if (allDone || i < currentIdx) el.classList.add("step-done");
    else if (i === currentIdx) el.classList.add("step-current");
    else el.classList.add("step-pending");
  });
}

// Cache-bust helper that respects existing query strings.
function withCacheBust(url, key) {
  if (!url) return "";
  if (key == null) return url;
  const safe = encodeURIComponent(String(key));
  return url + (url.includes("?") ? "&" : "?") + "_t=" + safe;
}

// ---------- Session lifecycle ----------------------------------------------
async function ensureSession() {
  if (SESSION_ID) return SESSION_ID;
  // Locale is auto-derived server-side from the configured TTS speaker.
  const r = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const j = await r.json();
  SESSION_ID = j.id;
  saveSessionId(SESSION_ID);
  // Show the voiceover-info strip with the locale the server picked.
  if (j.session?.locale) showVoiceoverInfo(j.session.locale);
  return SESSION_ID;
}

// Pretty-print an IETF locale into the small read-only indicator above the
// chat form so the user knows what language their voiceover will be in.
const LOCALE_LABELS = {
  "en-US": "English (US)",
  "en-SA": "English (Saudi)",
  "ar-SA": "Arabic (Saudi)",
  "ar-AE": "Arabic (UAE)",
  "zh-CN": "Chinese",
  "ja-JP": "Japanese",
  "ko-KR": "Korean",
  "es-MX": "Spanish",
  "pt-BR": "Portuguese",
  "id-ID": "Indonesian",
};
function showVoiceoverInfo(locale) {
  const el = $("voiceover-info");
  const label = $("voiceover-info-lang");
  if (!el || !label || !locale) return;
  label.textContent = LOCALE_LABELS[locale] || locale;
  el.hidden = false;
}

// Mirror of backend `_locale_from_speaker` so we can show the voiceover
// language indicator before the user creates a session.
function localeFromSpeakerClient(speaker) {
  if (!speaker) return "en-US";
  const s = String(speaker).toLowerCase();
  if (s.startsWith("zh_") || s.startsWith("zh-")) return "zh-CN";
  if (s.startsWith("ja_") || s.startsWith("ja-")) return "ja-JP";
  if (s.startsWith("ko_") || s.startsWith("ko-")) return "ko-KR";
  if (s.startsWith("ar_") || s.startsWith("ar-")) return "ar-SA";
  if (s.startsWith("es_") || s.startsWith("es-")) return "es-MX";
  if (s.startsWith("pt_") || s.startsWith("pt-")) return "pt-BR";
  if (s.startsWith("id_") || s.startsWith("id-")) return "id-ID";
  return "en-US";
}

async function previewVoiceoverLocale() {
  // Don't override a session-bound locale that's already been shown.
  if (SESSION_ID) return;
  try {
    const cfg = await fetch("/api/config").then((r) => r.json());
    const locale = localeFromSpeakerClient(cfg?.tts_speaker);
    showVoiceoverInfo(locale);
  } catch {
    /* no config yet — skip silently */
  }
}

// Open a fresh tab. sessionStorage is per-tab, so the new tab starts with
// nothing in storage and will create its own session on the first chat
// turn. The current tab is left untouched.
function onNewSession() {
  window.open("/", "_blank", "noopener");
}

// ---------- Restore from server-side state ---------------------------------
function restoreView(view) {
  const { session, messages, shot_images, video, brand_manual, brand_logo } = view;
  setStepperFromState(session.state);
  if (session.locale) showVoiceoverInfo(session.locale);
  if (messages.length) $("chat-empty").classList.add("hidden");

  // Render last consistency warnings + last eval result. Both live in
  // assistant message payloads so reloading a session picks them up.
  let lastWarnings = null;
  let lastEval = null;
  for (const m of messages) {
    renderChatMessage(m.role, m.content, m.payload);
    if (m.role === "assistant" && m.payload?.brand_consistency_warnings?.length) {
      lastWarnings = m.payload.brand_consistency_warnings;
    }
    if (m.role === "assistant" && m.payload?.eval) {
      lastEval = m.payload.eval;
    }
  }

  if (brand_manual && brand_manual.filename) showBrandManualLoaded(brand_manual);
  else showBrandManualEmpty();

  if (brand_logo && brand_logo.filename) showBrandLogoLoaded(brand_logo);
  else showBrandLogoEmpty();

  if (session.storyboard) {
    showStoryboard(session.storyboard, /* enableConfirm */ session.state === "storyboard_draft");
    if (lastWarnings) showConsistencyWarnings(lastWarnings);
    if (lastEval) showEval(lastEval);
    // After reload, if a storyboard already exists, the chat input should
    // reflect the "refine, or confirm below" mode — not the initial-brief
    // placeholder. Without this, users coming back to a session see a
    // placeholder that says "Type your brief" which is no longer accurate.
    $("chat-input").placeholder =
      "Refine the storyboard, or click Confirm below to generate stills.";
  }
  if (shot_images.length) {
    showImageGrid(session.storyboard?.shots || [], shot_images);
    if (session.state === "images_running") startImagePolling();
  }
  if (video) {
    showVideoPanel(video);
    if (video.status === "queued" || video.status === "running") startVideoPolling();
  }
}

// ---------- Chat ------------------------------------------------------------
async function onSendMessage(e) {
  e.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  if (!(await refreshConfigBadge())) {
    alert("Configure API keys in /settings first.");
    return;
  }

  await ensureSession();

  $("chat-empty").classList.add("hidden");
  renderChatMessage("user", text);
  $("chat-input").value = "";
  $("chat-send").disabled = true;

  // Render a placeholder assistant message we'll replace
  const pendingId = renderChatPending();

  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });

    // Guard rejection — server returns 422 with structured violations
    if (r.status === 422) {
      const j = await r.json().catch(() => ({}));
      const detail = j.detail || {};
      removeChatPending(pendingId);
      renderGuardRejection(detail);
      return;
    }

    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    const reply = data.reply || {};

    removeChatPending(pendingId);
    if (reply.action === "ask") {
      renderChatMessage("assistant", reply.question, { action: "ask" });
      setStepperFromState("chat");
      // Adapt the input hint so the user knows the agent is waiting on them.
      $("chat-input").placeholder =
        "Reply to the agent's question. Press Enter to send · Shift+Enter for a new line.";
    } else if (reply.action === "storyboard") {
      const intro = reply.summary || "Here's a draft storyboard.";
      renderChatMessage("assistant", intro, { action: "storyboard" });
      // If stills already exist for an old storyboard, signal that they're
      // stale and relabel the Confirm button so the user knows to re-run.
      const stillsExist = !!document.querySelector("#image-grid .image-card");
      showStoryboard(reply.storyboard, /* enableConfirm */ true, { stale: stillsExist });
      showConsistencyWarnings(reply.brand_consistency_warnings || []);
      if (reply.eval) showEval(reply.eval);
      setStepperFromState("storyboard_draft");

      // Smooth-scroll the user's eye to the new storyboard panel — without
      // this, they'd see "Storyboard ready ↓" in the chat but might not
      // notice the panel appearing below the fold.
      setTimeout(() => {
        $("storyboard-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      }, 250);

      // Adapt the input hint: from here, typing means "refine", and there's
      // also a Confirm button below to commit.
      $("chat-input").placeholder =
        "Refine the storyboard, or click Confirm below to generate stills.";
    }
  } catch (err) {
    removeChatPending(pendingId);
    renderChatMessage("assistant", `⚠ ${err.message || err}`, { kind: "error" });
  } finally {
    $("chat-send").disabled = $("chat-input").value.trim().length === 0;
  }
}

function renderGuardRejection(detail) {
  const hardBan = (detail.violations || []).filter((v) => v.category === "hard_ban");
  const sensitive = (detail.violations || []).filter((v) => v.category === "muslim_sensitive");
  const li = document.createElement("li");
  li.className = "chat-msg chat-assistant chat-guard";
  let body =
    `<strong>Content guard rejected your message.</strong> ` +
    escapeHtml(detail.message || "Please rephrase to remove the flagged content.");
  if (hardBan.length) {
    body +=
      `<div class="guard-section guard-hard"><span class="guard-tag">prohibited</span><ul>` +
      hardBan.map((v) => `<li><code>${escapeHtml(v.term)}</code> — ${escapeHtml(v.message)}</li>`).join("") +
      `</ul></div>`;
  }
  if (sensitive.length) {
    body +=
      `<div class="guard-section guard-sensitive"><span class="guard-tag">muslim-sensitive</span><ul>` +
      sensitive.map((v) => `<li><code>${escapeHtml(v.term)}</code> — ${escapeHtml(v.message)}</li>`).join("") +
      `</ul></div>`;
  }
  li.innerHTML =
    `<span class="chat-role">Guard</span>` +
    `<div class="chat-bubble guard-bubble">${body}</div>`;
  $("chat-log").appendChild(li);
  li.scrollIntoView({ behavior: "smooth", block: "end" });
}

// Render the Eval result (CTR + brand-safety) into the storyboard panel.
// Called from restoreView (on session reload) and onSendMessage (when a
// fresh storyboard arrives in the reply payload).
function showEval(ev) {
  if (!ev) return;
  const ctrEl = $("sb-ctr");
  if (ctrEl) ctrEl.textContent = ev.ctr_estimate_pct || "—";
  const statusEl = $("sb-eval-status");
  if (statusEl) {
    const status = ev.eval_status || "—";
    statusEl.textContent = status;
    statusEl.className = `api-status ${status === "pass" ? "ready" : "missing"}`;
    // Title shows the heuristic + CTR notes on hover so users can see WHY.
    const notes = (ev.eval_notes || []).join("\n• ");
    statusEl.title = notes ? `• ${notes}` : status;
  }
}

function showConsistencyWarnings(warnings) {
  const el = $("sb-consistency");
  if (!warnings || !warnings.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML =
    `<strong>⚠ Brand-manual consistency warnings (${warnings.length})</strong>` +
    `<ul>` +
    warnings.map((w) =>
      `<li><span class="warning-rule">${escapeHtml(w.rule || "")}</span> — ${escapeHtml(w.issue || "")}</li>`
    ).join("") +
    `</ul>` +
    `<p class="muted small">Reply in the chat asking the planner to fix these, or proceed if you're OK with them.</p>`;
}

function renderChatMessage(role, content, payload = null) {
  const li = document.createElement("li");
  li.className = `chat-msg chat-${role}` + (payload?.kind === "error" ? " chat-error" : "");

  // Action chip on assistant messages — tells the user whether the agent
  // wants more info or has produced something. Without it, users couldn't
  // tell from the chat alone whether to keep typing or check the
  // storyboard panel below.
  let chip = "";
  if (role === "assistant") {
    if (payload?.action === "storyboard") {
      chip = `<span class="chat-action-chip chat-action-ready" title="The draft storyboard is ready in the next panel below.">✓ Storyboard ready ↓</span>`;
    } else if (payload?.action === "ask") {
      chip = `<span class="chat-action-chip chat-action-ask" title="The agent needs more detail before it can produce a storyboard. Reply in the chat box below.">? Needs your reply</span>`;
    }
  }

  li.innerHTML =
    `<span class="chat-role">${role === "user" ? "You" : "Agent"}</span>` +
    `<div class="chat-bubble">${escapeHtml(content)}${chip}</div>`;
  $("chat-log").appendChild(li);
  li.scrollIntoView({ behavior: "smooth", block: "end" });
  return li;
}

function renderChatPending() {
  const id = "pending-" + Date.now();
  const li = document.createElement("li");
  li.className = "chat-msg chat-assistant chat-pending";
  li.id = id;
  li.innerHTML = `<span class="chat-role">Agent</span><div class="chat-bubble"><span class="dots"><i></i><i></i><i></i></span></div>`;
  $("chat-log").appendChild(li);
  li.scrollIntoView({ behavior: "smooth", block: "end" });
  return id;
}

function removeChatPending(id) {
  const el = $(id);
  if (el) el.remove();
}

// ---------- Storyboard preview ---------------------------------------------
// Confirm-state remembered between renders so re-renders triggered by
// brand-logo state changes (upload / remove) don't drop the user back to
// "draft — awaiting confirm" unexpectedly.
let _signoffEnableConfirm = false;

function showStoryboard(sb, enableConfirm, opts = {}) {
  _storyboardCache = sb || null;  // refresh cache so polling renders fresh shot metadata
  _signoffEnableConfirm = !!enableConfirm;
  $("storyboard-panel").classList.remove("hidden");

  // Stale warning + button relabel when an existing stills grid no longer
  // reflects the latest storyboard.
  const stale = !!opts.stale;
  $("sb-stale").classList.toggle("hidden", !stale);
  const confirmBtn = $("confirm-storyboard-btn");
  if (confirmBtn) {
    confirmBtn.textContent = stale
      ? "Re-generate stills"
      : "Confirm and generate stills";
  }
  $("sb-hook").textContent = sb.hook || "—";
  $("sb-cta").textContent = sb.cta || "—";

  const vo = sb.voiceover || "";
  const voEl = $("sb-vo");
  voEl.textContent = vo || "—";
  const isArabic = /[؀-ۿ]/.test(vo);
  voEl.classList.toggle("rtl", isArabic);
  if (isArabic) voEl.setAttribute("lang", "ar");
  else voEl.removeAttribute("lang");

  const shots = sb.shots || [];
  const shotsEl = $("sb-shots");
  shotsEl.innerHTML = "";
  shots.forEach((shot, i) => {
    const li = document.createElement("li");
    li.className = "sb-shot";
    const isSignOff = i === shots.length - 1;
    const signOffTag = isSignOff
      ? `<span class="sb-signoff-tag" title="Sign-off frame — the brand logo, if uploaded, lands here only.">SIGN-OFF</span>`
      : "";
    li.innerHTML =
      `<header><span class="sb-shot-id">#${shot.id}</span>` +
      `<span class="sb-shot-dur">${shot.duration_s ?? "?"}s${signOffTag}</span></header>` +
      `<p class="sb-shot-scene">${escapeHtml(shot.scene || "")}</p>` +
      `<details><summary class="muted small">visual / motion prompt</summary>` +
      `<p class="sb-shot-prompt"><strong>Visual:</strong> ${escapeHtml(shot.visual_prompt || "")}</p>` +
      `<p class="sb-shot-prompt"><strong>Motion:</strong> ${escapeHtml(shot.motion_prompt || "")}</p>` +
      `</details>` +
      (isSignOff ? renderSignOffLogoSlot() : "");
    if (isSignOff) li.classList.add("sb-shot-signoff");
    shotsEl.appendChild(li);
  });

  $("confirm-storyboard-btn").disabled = !enableConfirm;
  const stage = $("sb-stage");
  if (enableConfirm) {
    stage.textContent = "draft — awaiting confirm";
    stage.className = "api-status missing";
  } else {
    stage.textContent = "confirmed";
    stage.className = "api-status ready";
  }
}

// Logo chip lives at the bottom of the sign-off (last) shot card. State
// (empty vs loaded) is read from the module-level _brandLogoInfo, so a
// re-draft of the storyboard preserves whatever the user already uploaded.
function renderSignOffLogoSlot() {
  const loaded = _brandLogoInfo && _brandLogoInfo.filename;
  if (loaded) {
    return (
      `<div class="sb-shot-logo-slot">` +
      `<button type="button" class="asset-chip is-loaded sb-logo-chip" id="logo-chip" data-asset="logo"` +
      ` title="Brand logo will be composited onto the bottom-right of this sign-off frame.">` +
      `<span class="asset-chip-icon">🏷</span>` +
      `<span class="asset-chip-loaded-wrap">` +
      `<span class="asset-chip-check">✓</span>` +
      `<span class="asset-chip-name" id="logo-chip-name">${escapeHtml(_brandLogoInfo.filename)}</span>` +
      `<span class="asset-chip-remove" id="logo-chip-remove" role="button" aria-label="Remove logo">×</span>` +
      `</span>` +
      `</button>` +
      `</div>`
    );
  }
  return (
    `<div class="sb-shot-logo-slot">` +
    `<button type="button" class="asset-chip is-empty sb-logo-chip" id="logo-chip" data-asset="logo"` +
    ` title="Optional. Upload a brand logo (PNG/JPG/WEBP) — it will be composited onto the bottom-right of this sign-off frame only.">` +
    `<span class="asset-chip-icon">🏷</span>` +
    `<span class="asset-chip-label asset-chip-empty-label">+ Add brand logo (optional)</span>` +
    `</button>` +
    `</div>`
  );
}

// ---------- Re-draft storyboard --------------------------------------------
//
// One-click: replays a fixed redraft request through the chat path, so the
// LLM gets it as a normal user turn (history-aware) and the existing
// chat_turn / showStoryboard / consistency-check pipeline kicks in.
async function onRedraftStoryboard() {
  if (!SESSION_ID) return;
  const ti = $("chat-input");
  ti.value =
    "Please draft a different storyboard from the same brief — vary the " +
    "hook angle, scene composition or pacing while keeping the brand " +
    "constraints. Same shot count is fine.";
  ti.dispatchEvent(new Event("input"));
  $("chat-form").requestSubmit();
}

// ---------- Confirm + image gen --------------------------------------------
async function onConfirmStoryboard() {
  if (!SESSION_ID) return;
  $("confirm-storyboard-btn").disabled = true;
  $("confirm-storyboard-btn").textContent = "Queueing…";
  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/storyboard/confirm`, { method: "POST" });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const view = await r.json();
    const sb = view.session.storyboard;
    showStoryboard(sb, false, { stale: false });
    showImageGrid(sb.shots || [], view.shot_images);
    setStepperFromState("storyboard_confirmed");
    startImagePolling();
  } catch (err) {
    alert(`Couldn't start image generation: ${err.message || err}`);
    $("confirm-storyboard-btn").disabled = false;
  } finally {
    $("confirm-storyboard-btn").textContent = "Confirm and generate stills";
  }
}

function showImageGrid(shots, statuses) {
  $("images-panel").classList.remove("hidden");
  const grid = $("image-grid");
  grid.innerHTML = "";
  const byShot = new Map(statuses.map((s) => [s.shot_id, s]));
  for (const shot of shots) {
    const st = byShot.get(shot.id) || { status: "queued" };
    const card = document.createElement("li");
    card.className = `image-card image-${st.status}`;
    card.dataset.shotId = String(shot.id);
    card.dataset.status = st.status;
    card.innerHTML = renderImageCardInner(shot, st);
    grid.appendChild(card);
    wireImageCardEvents(card);
  }
  updateSelectionCount();
  updateImagesProgress(statuses);
}

function renderImageCardInner(shot, st) {
  const succeeded = st.status === "succeeded";
  const failed = st.status === "failed";
  const checked = succeeded ? "checked" : "";
  const disabled = succeeded ? "" : "disabled";
  const media = succeeded
    ? `<img src="${escapeHtml(withCacheBust(st.url, st.updated_at))}" alt="shot ${shot.id}" referrerpolicy="no-referrer" loading="lazy" onerror="this.closest('.image-media').classList.add('image-broken'); this.remove();">`
    : failed
    ? `<div class="image-failed">⚠ ${escapeHtml(st.error || "failed")}</div>`
    : `<div class="image-spinner"><div class="spinner"></div><span>${st.status}…</span></div>`;

  const failureClass = failed && /sensitive|moderation/i.test(st.error || "") ? " moderation-fail" : "";

  // Per-image controls: refine box (when ok), retry button (when failed).
  // The Apply button starts disabled and only enables once the user has
  // typed at least one non-whitespace character — same affordance as
  // the main chat Send button, prevents empty-prompt API calls.
  const controls = succeeded
    ? `<form class="shot-refine" data-shot-id="${shot.id}">` +
      `<input type="text" class="shot-refine-input" placeholder="Tweak this shot — e.g. darker background, no people" maxlength="500">` +
      `<button type="submit" class="shot-refine-send" disabled>Apply</button>` +
      `</form>`
    : failed
    ? `<div class="shot-retry"><button type="button" class="shot-retry-btn" data-shot-id="${shot.id}">↻ Retry</button></div>`
    : `<div class="shot-controls-placeholder muted small">Generating…</div>`;

  // Show the SCENE one-liner (human-readable) but expose the actual
  // VISUAL_PROMPT (what Seedream received) on hover. Lets the user spot
  // when the planner's prompt drifted from the scene description — e.g.
  // scene says "man in thobe" but the prompt got generalized to "person"
  // or got overridden by brand-manual modesty defaults.
  const promptForHover = shot.visual_prompt || shot.scene || "";
  return (
    `<label class="image-check"><input type="checkbox" ${checked} ${disabled}><span></span></label>` +
    `<div class="image-media${failureClass}">${media}</div>` +
    `<div class="image-meta">` +
    `<span class="image-id">#${shot.id} · ${shot.duration_s ?? "?"}s</span>` +
    `<span class="image-scene" title="Sent to Seedream:\n\n${escapeHtml(promptForHover)}">${escapeHtml(shot.scene || "")}</span>` +
    `</div>` +
    controls
  );
}

function wireImageCardEvents(card) {
  const cb = card.querySelector("input[type=checkbox]");
  if (cb) cb.addEventListener("change", updateSelectionCount);
  const refineForm = card.querySelector(".shot-refine");
  if (refineForm) {
    refineForm.addEventListener("submit", onShotRefine);
    // Apply button gates on non-whitespace input — same pattern as Send.
    const refineInput = refineForm.querySelector(".shot-refine-input");
    const refineSend = refineForm.querySelector(".shot-refine-send");
    if (refineInput && refineSend) {
      refineInput.addEventListener("input", () => {
        refineSend.disabled = refineInput.value.trim().length === 0;
      });
    }
  }
  const retryBtn = card.querySelector(".shot-retry-btn");
  if (retryBtn) retryBtn.addEventListener("click", onShotRetry);
}

function updateSelectionCount() {
  const checks = document.querySelectorAll("#image-grid input[type=checkbox]:checked");
  $("selection-count").textContent = `${checks.length} selected`;
  $("generate-video-btn").disabled = checks.length === 0;
}

function updateImagesProgress(statuses) {
  const done = statuses.filter((s) => s.status === "succeeded" || s.status === "failed").length;
  $("images-progress").textContent = `${done} / ${statuses.length}`;
}

function startImagePolling() {
  if (imagePollHandle) clearInterval(imagePollHandle);
  imagePollHandle = setInterval(async () => {
    try {
      const r = await fetch(`/api/sessions/${SESSION_ID}/images`);
      const data = await r.json();
      // Patch each card in place. Re-render even if status didn't change
      // when the row's updated_at advanced — that catches refine completions
      // (running → running → succeeded with a brand-new url).
      for (const st of data.shots) {
        const card = document.querySelector(`.image-card[data-shot-id="${st.shot_id}"]`);
        if (!card) continue;
        const sameStatus = card.dataset.status === st.status;
        const sameUpdated = card.dataset.updatedAt === st.updated_at;
        if (sameStatus && sameUpdated) continue;
        card.dataset.status = st.status;
        card.dataset.updatedAt = st.updated_at || "";
        card.className = `image-card image-${st.status}`;
        const sb = await fetchCachedStoryboard();
        const shot = (sb?.shots || []).find((s) => s.id === st.shot_id) || { id: st.shot_id };
        card.innerHTML = renderImageCardInner(shot, st);
        wireImageCardEvents(card);
      }
      updateSelectionCount();
      updateImagesProgress(data.shots);
      if (data.all_done) {
        clearInterval(imagePollHandle);
        imagePollHandle = null;
        setStepperFromState("images_done");
      }
    } catch (e) {
      console.warn("image poll error", e);
    }
  }, 1500);  // Was 3000 — tighter polling so fresh stills appear ~2x faster.
}

let _storyboardCache = null;
async function fetchCachedStoryboard() {
  if (_storyboardCache) return _storyboardCache;
  const r = await fetch(`/api/sessions/${SESSION_ID}`);
  const view = await r.json();
  _storyboardCache = view.session?.storyboard || null;
  return _storyboardCache;
}

// ---------- Video gen + playback -------------------------------------------
async function onGenerateVideo() {
  const checks = Array.from(document.querySelectorAll("#image-grid input[type=checkbox]:checked"));
  const selected = checks
    .map((cb) => Number(cb.closest(".image-card").dataset.shotId))
    .filter((n) => Number.isFinite(n));
  if (!selected.length) return;

  $("generate-video-btn").disabled = true;
  $("generate-video-btn").textContent = "Submitting…";

  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/video`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_shot_ids: selected }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const view = await r.json();
    showVideoPanel(view.video || { status: "queued" });
    setStepperFromState("video_running");
    startVideoPolling();
  } catch (err) {
    alert(`Couldn't start video gen: ${err.message || err}`);
    $("generate-video-btn").disabled = false;
  } finally {
    $("generate-video-btn").textContent = "Generate video from selection";
  }
}

function showVideoPanel(v) {
  $("video-panel").classList.remove("hidden");
  $("video-loading").classList.add("hidden");
  $("video-ready").classList.add("hidden");
  $("video-error").classList.add("hidden");

  const stage = $("video-stage");
  stage.textContent = v.status || "—";
  stage.className =
    "api-status " +
    (v.status === "succeeded" ? "ready" : v.status === "failed" ? "missing" : "missing");

  if (v.status === "succeeded" && v.local_url) {
    $("video-ready").classList.remove("hidden");
    $("video-preview").src = v.local_url;
    $("video-local-url").href = v.local_url;
    $("video-local-url").textContent = v.local_url;
    const meta = v.metadata_json ? JSON.parse(v.metadata_json) : null;
    if (meta) {
      const pieces = [];
      if (meta.bytes) pieces.push(`${(meta.bytes / 1024 / 1024).toFixed(2)} MB`);
      if (meta.duration_s) pieces.push(`${meta.duration_s}s`);
      if (meta.ratio) pieces.push(meta.ratio);
      if (meta.model) pieces.push(meta.model);
      $("video-stats").textContent = pieces.join(" · ");
    }
  } else if (v.status === "failed") {
    $("video-error").classList.remove("hidden");
    $("video-error-msg").textContent = v.error || "(no error message)";
  } else {
    $("video-loading").classList.remove("hidden");
  }
}

function startVideoPolling() {
  if (videoPollHandle) clearInterval(videoPollHandle);
  videoPollHandle = setInterval(async () => {
    try {
      const r = await fetch(`/api/sessions/${SESSION_ID}/video`);
      const v = await r.json();
      showVideoPanel(v);
      if (v.status === "succeeded") {
        clearInterval(videoPollHandle);
        videoPollHandle = null;
        setStepperFromState("video_done");
      } else if (v.status === "failed") {
        clearInterval(videoPollHandle);
        videoPollHandle = null;
        setStepperFromState("images_done");
      }
    } catch (e) {
      console.warn("video poll error", e);
    }
  }, 3000);  // Was 5000 — tighter polling so video status surfaces faster.
}

// ---------- Asset chips (logo + brand manual) ------------------------------
//
// One shared model for both chips. State is encoded by classes on the chip:
//   .is-empty   — picker open on click
//   .is-loaded  — shows ✓ filename ×, click reopens picker
//
// Errors surface in the shared `#brief-asset-error` slot below the panel head.

function setChipState(chipId, nameId, state, info) {
  const chip = $(chipId);
  if (!chip) return;
  const emptyLabel = chip.querySelector(".asset-chip-empty-label");
  const loadedWrap = chip.querySelector(".asset-chip-loaded-wrap");
  chip.classList.remove("is-empty", "is-loaded");
  if (state === "loaded" && info) {
    chip.classList.add("is-loaded");
    if (emptyLabel) emptyLabel.classList.add("hidden");
    if (loadedWrap) loadedWrap.classList.remove("hidden");
    const nameEl = $(nameId);
    if (nameEl) nameEl.textContent = info.filename || "(file)";
  } else {
    chip.classList.add("is-empty");
    if (emptyLabel) emptyLabel.classList.remove("hidden");
    if (loadedWrap) loadedWrap.classList.add("hidden");
  }
}

function showBriefAssetError(msg) {
  const el = $("brief-asset-error");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 6000);
}

function showBrandLogoEmpty() {
  _brandLogoInfo = null;
  // Re-render the storyboard if it's open so the chip in the sign-off
  // shot reflects the new state. setChipState is a no-op when there's
  // no chip in the DOM yet.
  setChipState("logo-chip", "logo-chip-name", "empty");
  const inp = $("brand-logo-file");
  if (inp) inp.value = "";
  if (_storyboardCache) showStoryboard(_storyboardCache, _signoffEnableConfirm);
}
function showBrandLogoLoaded(info) {
  _brandLogoInfo = info || null;
  setChipState("logo-chip", "logo-chip-name", "loaded", info);
  if (_storyboardCache) showStoryboard(_storyboardCache, _signoffEnableConfirm);
}
function showBrandManualEmpty()     { setChipState("pdf-chip",  "pdf-chip-name",  "empty"); $("brand-rag-file").value = ""; }
function showBrandManualLoaded(m)   { setChipState("pdf-chip",  "pdf-chip-name",  "loaded", m); }

// ---- Brand logo upload ----
async function onBrandLogoPicked(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  if (!/\.(png|jpe?g|webp)$/i.test(file.name)) {
    showBriefAssetError("Logo must be PNG / JPG / WEBP.");
    e.target.value = "";
    return;
  }
  await ensureSession();
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/brand-logo`, { method: "POST", body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    showBrandLogoLoaded(j.logo);
  } catch (err) {
    showBrandLogoEmpty();
    showBriefAssetError(`Logo upload failed: ${err.message || err}`);
  }
}

async function onBrandLogoRemove() {
  if (!SESSION_ID) { showBrandLogoEmpty(); return; }
  if (!confirm("Remove the uploaded logo?")) return;
  try {
    await fetch(`/api/sessions/${SESSION_ID}/brand-logo`, { method: "DELETE" });
  } catch (e) { console.warn("logo delete failed", e); }
  showBrandLogoEmpty();
}

// ---- Brand manual upload ----
async function onBrandManualPicked(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showBriefAssetError("Brand manual must be a PDF.");
    e.target.value = "";
    return;
  }
  await ensureSession();
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/brand-manual`, { method: "POST", body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    showBrandManualLoaded(j.manual);
  } catch (err) {
    showBrandManualEmpty();
    showBriefAssetError(`Brand manual upload failed: ${err.message || err}`);
  }
}

async function onBrandManualRemove() {
  if (!SESSION_ID) { showBrandManualEmpty(); return; }
  if (!confirm("Remove the uploaded brand manual? Storyboard generation will fall back to the bundled demo manual.")) return;
  try {
    await fetch(`/api/sessions/${SESSION_ID}/brand-manual`, { method: "DELETE" });
  } catch (e) { console.warn("manual delete failed", e); }
  showBrandManualEmpty();
}

// ---------- Per-shot refine + retry ----------------------------------------
async function onShotRefine(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const shotId = Number(form.dataset.shotId);
  const input = form.querySelector(".shot-refine-input");
  const instruction = (input.value || "").trim();
  if (!instruction || !SESSION_ID) return;

  // Optimistic UI: clear input, mark card as running, restart polling
  input.value = "";
  const card = document.querySelector(`.image-card[data-shot-id="${shotId}"]`);
  if (card) {
    card.dataset.status = "running";
    card.className = "image-card image-running";
    const sb = await fetchCachedStoryboard();
    const shot = (sb?.shots || []).find((s) => s.id === shotId) || { id: shotId };
    card.innerHTML = renderImageCardInner(shot, { status: "running" });
    wireImageCardEvents(card);
  }

  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/shots/${shotId}/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    startImagePolling();
  } catch (err) {
    alert(`Refine failed: ${err.message || err}`);
  }
}

async function onShotRetry(e) {
  const btn = e?.currentTarget instanceof HTMLElement ? e.currentTarget : null;
  const shotId = Number((btn?.dataset || e?.currentTarget?.dataset || {}).shotId);
  await triggerShotRetry(shotId, btn);
}

async function triggerShotRetry(shotId, btn) {
  if (!shotId || !SESSION_ID) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "↻ retrying…";
  }
  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/shots/${shotId}/retry`, {
      method: "POST",
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    // Repaint the card immediately so the user sees state change.
    const card = document.querySelector(`.image-card[data-shot-id="${shotId}"]`);
    if (card) {
      card.dataset.status = "running";
      card.className = "image-card image-running";
      const sb = await fetchCachedStoryboard();
      const shot = (sb?.shots || []).find((s) => s.id === shotId) || { id: shotId };
      card.innerHTML = renderImageCardInner(shot, { status: "running" });
      wireImageCardEvents(card);
    }
    startImagePolling();
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "↻ Retry";
    }
    alert(`Retry failed: ${err.message || err}`);
  }
}
