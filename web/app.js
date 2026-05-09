// Multi-step ad-creation flow.
// State machine:
//   chat               (user types brief, LLM may ask clarifying Qs)
//   storyboard_draft   (assistant proposed a storyboard; user can confirm or refine)
//   images_running     (Seedream calls fanned out, polling)
//   images_done        (user picks shots)
//   video_running      (Seedance call running, polling)
//   video_done         (local mp4 playable)
//
// Session id lives in localStorage so a page reload resumes the same flow.

const SAMPLE_BRIEF =
  "Promote our premium Ajwa dates collection for the upcoming Ramadan campaign. " +
  "Target audience: Saudi families, ages 25-45, gifting for iftar gatherings. " +
  "Single 9:16 short-form video, ≤15 seconds, bilingual (Arabic VO + English overlay). " +
  "Objective: drive product page visits.";

const STORE_KEY = "saa.session_id";
const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

let SESSION_ID = null;
let imagePollHandle = null;
let videoPollHandle = null;

// ---------- Boot ------------------------------------------------------------
(async function init() {
  await refreshConfigBadge();

  $("load-sample").addEventListener("click", () => {
    $("chat-input").value = SAMPLE_BRIEF;
    $("chat-input").dispatchEvent(new Event("input"));
    $("chat-input").focus();
  });

  $("chat-input").addEventListener("input", () => {
    $("chat-send").disabled = $("chat-input").value.trim().length === 0;
  });

  $("chat-form").addEventListener("submit", onSendMessage);
  $("confirm-storyboard-btn").addEventListener("click", onConfirmStoryboard);
  $("generate-video-btn").addEventListener("click", onGenerateVideo);
  $("new-session-btn").addEventListener("click", onNewSession);

  // Brand-manual upload widget
  $("brand-rag-file").addEventListener("change", onBrandManualPicked);
  $("brand-rag-remove").addEventListener("click", onBrandManualRemove);
  $("brand-rag-replace").addEventListener("click", () => $("brand-rag-file").click());

  // Brand-logo upload widget
  $("brand-logo-file").addEventListener("change", onBrandLogoPicked);
  $("brand-logo-remove").addEventListener("click", onBrandLogoRemove);
  $("brand-logo-replace").addEventListener("click", () => $("brand-logo-file").click());

  const stored = localStorage.getItem(STORE_KEY);
  if (stored) {
    SESSION_ID = stored;
    try {
      const view = await fetch(`/api/sessions/${SESSION_ID}`).then((r) => {
        if (!r.ok) throw new Error("404");
        return r.json();
      });
      restoreView(view);
    } catch {
      // session no longer exists server-side
      localStorage.removeItem(STORE_KEY);
      SESSION_ID = null;
    }
  }
})();

// ---------- Config status ---------------------------------------------------
async function refreshConfigBadge() {
  let status;
  try {
    status = await fetch("/api/config/status").then((r) => r.json());
  } catch {
    status = { configured: false, missing: ["?"] };
  }
  const badge = $("config-badge");
  if (status.configured) {
    badge.textContent = "READY";
    badge.title = "All keys configured.";
    badge.className = "mode-badge live";
  } else {
    const miss = (status.missing || []).map((k) => k.replace(/^openai_/, "llm/")).join(", ");
    badge.textContent = `UNCONFIGURED · ${miss}`;
    badge.title = "Open Settings and fill in the missing keys.";
    badge.className = "mode-badge unconfigured";
  }
  return status.configured;
}

// ---------- Session lifecycle ----------------------------------------------
async function ensureSession() {
  if (SESSION_ID) return SESSION_ID;
  const r = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locale: $("locale").value }),
  });
  const j = await r.json();
  SESSION_ID = j.id;
  localStorage.setItem(STORE_KEY, SESSION_ID);
  return SESSION_ID;
}

async function onNewSession() {
  if (!confirm("Start a fresh session? The current chat / images / video will stay on disk but be hidden.")) return;
  // Stop any active pollers
  if (imagePollHandle) { clearInterval(imagePollHandle); imagePollHandle = null; }
  if (videoPollHandle) { clearInterval(videoPollHandle); videoPollHandle = null; }
  SESSION_ID = null;
  localStorage.removeItem(STORE_KEY);
  // Reset UI
  $("chat-log").innerHTML = "";
  $("chat-empty").classList.remove("hidden");
  $("storyboard-panel").classList.add("hidden");
  $("images-panel").classList.add("hidden");
  $("video-panel").classList.add("hidden");
  $("chat-input").value = "";
  $("chat-send").disabled = true;
  showBrandManualEmpty();
  showBrandLogoEmpty();
}

// ---------- Restore from server-side state ---------------------------------
function restoreView(view) {
  const { session, messages, shot_images, video, brand_manual, brand_logo } = view;
  if (messages.length) $("chat-empty").classList.add("hidden");

  // Render last consistency warnings if the most recent assistant message
  // had any (they live in the message payload).
  let lastWarnings = null;
  for (const m of messages) {
    renderChatMessage(m.role, m.content, m.payload);
    if (m.role === "assistant" && m.payload?.brand_consistency_warnings?.length) {
      lastWarnings = m.payload.brand_consistency_warnings;
    }
  }

  if (brand_manual && brand_manual.filename) showBrandManualLoaded(brand_manual);
  else showBrandManualEmpty();

  if (brand_logo && brand_logo.filename) showBrandLogoLoaded(brand_logo);
  else showBrandLogoEmpty();

  if (session.storyboard) {
    showStoryboard(session.storyboard, /* enableConfirm */ session.state === "storyboard_draft");
    if (lastWarnings) showConsistencyWarnings(lastWarnings);
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
      renderChatMessage("assistant", reply.question);
    } else if (reply.action === "storyboard") {
      const intro = reply.summary || "Here's a draft storyboard.";
      renderChatMessage("assistant", intro, { action: "storyboard" });
      showStoryboard(reply.storyboard, /* enableConfirm */ true);
      showConsistencyWarnings(reply.brand_consistency_warnings || []);
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
  li.innerHTML =
    `<span class="chat-role">${role === "user" ? "You" : "Agent"}</span>` +
    `<div class="chat-bubble">${escapeHtml(content)}</div>`;
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
function showStoryboard(sb, enableConfirm) {
  _storyboardCache = sb || null;  // refresh cache so polling renders fresh shot metadata
  $("storyboard-panel").classList.remove("hidden");
  $("sb-hook").textContent = sb.hook || "—";
  $("sb-cta").textContent = sb.cta || "—";

  const vo = sb.voiceover || "";
  const voEl = $("sb-vo");
  voEl.textContent = vo || "—";
  const isArabic = /[؀-ۿ]/.test(vo);
  voEl.classList.toggle("rtl", isArabic);
  if (isArabic) voEl.setAttribute("lang", "ar");
  else voEl.removeAttribute("lang");

  const shotsEl = $("sb-shots");
  shotsEl.innerHTML = "";
  for (const shot of sb.shots || []) {
    const li = document.createElement("li");
    li.className = "sb-shot";
    li.innerHTML =
      `<header><span class="sb-shot-id">#${shot.id}</span><span class="sb-shot-dur">${shot.duration_s ?? "?"}s</span></header>` +
      `<p class="sb-shot-scene">${escapeHtml(shot.scene || "")}</p>` +
      `<details><summary class="muted small">visual / motion prompt</summary>` +
      `<p class="sb-shot-prompt"><strong>Visual:</strong> ${escapeHtml(shot.visual_prompt || "")}</p>` +
      `<p class="sb-shot-prompt"><strong>Motion:</strong> ${escapeHtml(shot.motion_prompt || "")}</p>` +
      `</details>`;
    shotsEl.appendChild(li);
  }

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
    showStoryboard(sb, false);
    showImageGrid(sb.shots || [], view.shot_images);
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
    ? `<img src="${escapeHtml(st.url)}?t=${st.updated_at || ''}" alt="shot ${shot.id}" referrerpolicy="no-referrer">`
    : failed
    ? `<div class="image-failed">⚠ ${escapeHtml(st.error || "failed")}</div>`
    : `<div class="image-spinner"><div class="spinner"></div><span>${st.status}…</span></div>`;

  const failureClass = failed && /sensitive|moderation/i.test(st.error || "") ? " moderation-fail" : "";

  // Per-image controls: refine input (when ok), retry button (when failed)
  const controls = succeeded
    ? `<form class="shot-refine" data-shot-id="${shot.id}">` +
      `<input type="text" class="shot-refine-input" placeholder="Tweak this shot — e.g. 'darker background, no people'" maxlength="500">` +
      `<button type="submit" class="shot-refine-send" title="Re-generate this shot">↻</button>` +
      `</form>`
    : failed
    ? `<div class="shot-retry"><button type="button" class="shot-retry-btn" data-shot-id="${shot.id}">↻ Retry</button></div>`
    : `<div class="shot-controls-placeholder muted small">Generating…</div>`;

  return (
    `<label class="image-check"><input type="checkbox" ${checked} ${disabled}><span></span></label>` +
    `<div class="image-media${failureClass}">${media}</div>` +
    `<div class="image-meta">` +
    `<span class="image-id">#${shot.id} · ${shot.duration_s ?? "?"}s</span>` +
    `<span class="image-scene">${escapeHtml(shot.scene || "")}</span>` +
    `</div>` +
    controls
  );
}

function wireImageCardEvents(card) {
  const cb = card.querySelector("input[type=checkbox]");
  if (cb) cb.addEventListener("change", updateSelectionCount);
  const refineForm = card.querySelector(".shot-refine");
  if (refineForm) refineForm.addEventListener("submit", onShotRefine);
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
      }
    } catch (e) {
      console.warn("image poll error", e);
    }
  }, 3000);
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
      if (v.status === "succeeded" || v.status === "failed") {
        clearInterval(videoPollHandle);
        videoPollHandle = null;
      }
    } catch (e) {
      console.warn("video poll error", e);
    }
  }, 5000);
}

// ---------- Brand manual upload (RAG) --------------------------------------
function showBrandManualEmpty() {
  $("brand-rag-empty").classList.remove("hidden");
  $("brand-rag-loaded").classList.add("hidden");
  $("brand-rag-uploading").classList.add("hidden");
  $("brand-rag-error").classList.add("hidden");
  $("brand-rag-file").value = "";
}

function showBrandManualLoaded(manual) {
  $("brand-rag-empty").classList.add("hidden");
  $("brand-rag-loaded").classList.remove("hidden");
  $("brand-rag-uploading").classList.add("hidden");
  $("brand-rag-error").classList.add("hidden");
  $("brand-rag-name").textContent = manual.filename || "manual.pdf";
  const mb = ((manual.bytes || 0) / 1024 / 1024).toFixed(2);
  $("brand-rag-stats").textContent = `${manual.pages} pages · ${mb} MB · uploaded ${manual.created_at || ""}`;
}

function showBrandManualUploading() {
  $("brand-rag-empty").classList.add("hidden");
  $("brand-rag-loaded").classList.add("hidden");
  $("brand-rag-uploading").classList.remove("hidden");
  $("brand-rag-error").classList.add("hidden");
}

function showBrandManualError(msg) {
  $("brand-rag-error").classList.remove("hidden");
  $("brand-rag-error").textContent = msg;
}

async function onBrandManualPicked(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showBrandManualError("Only .pdf files are accepted.");
    return;
  }
  await ensureSession();
  showBrandManualUploading();

  const fd = new FormData();
  fd.append("file", file, file.name);

  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/brand-manual`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    const manual = { ...j.manual, created_at: new Date().toISOString().slice(0, 19).replace("T", " ") };
    showBrandManualLoaded(manual);
  } catch (err) {
    showBrandManualEmpty();
    showBrandManualError(`Upload failed: ${err.message || err}`);
  }
}

async function onBrandManualRemove() {
  if (!SESSION_ID) return;
  if (!confirm("Remove the uploaded brand manual? Storyboard generation will fall back to the bundled demo manual.")) return;
  try {
    await fetch(`/api/sessions/${SESSION_ID}/brand-manual`, { method: "DELETE" });
  } catch (e) {
    console.warn("delete failed", e);
  }
  showBrandManualEmpty();
}

// ---------- Brand logo upload ----------------------------------------------
function showBrandLogoEmpty() {
  $("brand-logo-empty").classList.remove("hidden");
  $("brand-logo-loaded").classList.add("hidden");
  $("brand-logo-error").classList.add("hidden");
  $("brand-logo-file").value = "";
}

function showBrandLogoLoaded(logo) {
  $("brand-logo-empty").classList.add("hidden");
  $("brand-logo-loaded").classList.remove("hidden");
  $("brand-logo-error").classList.add("hidden");
  $("brand-logo-name").textContent = logo.filename || "logo";
  const kb = ((logo.bytes || 0) / 1024).toFixed(1);
  const dim = (logo.width && logo.height) ? `${logo.width}×${logo.height}` : "";
  $("brand-logo-stats").textContent = [dim, `${kb} KB`].filter(Boolean).join(" · ");
}

function showBrandLogoError(msg) {
  $("brand-logo-error").classList.remove("hidden");
  $("brand-logo-error").textContent = msg;
}

async function onBrandLogoPicked(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  if (!/\.(png|jpe?g|webp)$/i.test(file.name)) {
    showBrandLogoError("Only PNG / JPG / WEBP images are accepted.");
    return;
  }
  await ensureSession();
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/brand-logo`, {
      method: "POST",
      body: fd,
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    const j = await r.json();
    showBrandLogoLoaded(j.logo);
  } catch (err) {
    showBrandLogoEmpty();
    showBrandLogoError(`Upload failed: ${err.message || err}`);
  }
}

async function onBrandLogoRemove() {
  if (!SESSION_ID) return;
  if (!confirm("Remove the uploaded logo? Future stills won't be composited.")) return;
  try {
    await fetch(`/api/sessions/${SESSION_ID}/brand-logo`, { method: "DELETE" });
  } catch (e) {
    console.warn("delete failed", e);
  }
  showBrandLogoEmpty();
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
  const btn = e.currentTarget;
  const shotId = Number(btn.dataset.shotId);
  if (!shotId || !SESSION_ID) return;
  btn.disabled = true;
  btn.textContent = "↻ retrying…";
  try {
    const r = await fetch(`/api/sessions/${SESSION_ID}/shots/${shotId}/retry`, {
      method: "POST",
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${r.status}`);
    }
    // Restart polling so the card updates as the retry progresses
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
    btn.disabled = false;
    btn.textContent = "↻ Retry";
    alert(`Retry failed: ${err.message || err}`);
  }
}
