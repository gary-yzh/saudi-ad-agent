// Settings page — load / save the SQLite-backed config via /api/config.
//
// All field IDs in settings.html match the storage keys (`openai_api_key`,
// `ark_api_key`, …) so we can serialize the form straight into the POST body
// and splat the GET response straight into the inputs.

const REQUIRED = {
  llm: "openai_api_key",
  ark: "ark_api_key",
  tts: "tts_api_key",
};

// No checkbox fields after the watermark / generate_audio cleanup
const BOOL_FIELDS = new Set();
const INT_FIELDS = new Set([
  "video_duration",
  "tts_speech_rate",
  "tts_loudness_rate",
  "tts_emotion_scale",
]);

const $ = (id) => document.getElementById(id);
const form = () => $("settings-form");

function inputs() {
  return form().querySelectorAll("input[name], select[name]");
}

function readForm() {
  const out = {};
  inputs().forEach((el) => {
    const name = el.name;
    if (BOOL_FIELDS.has(name)) {
      out[name] = el.checked;
    } else if (INT_FIELDS.has(name)) {
      const raw = (el.value || "").trim();
      out[name] = raw === "" ? null : parseInt(raw, 10);
    } else {
      out[name] = (el.value || "").trim() || null;
    }
  });
  return out;
}

function fillForm(cfg) {
  inputs().forEach((el) => {
    const v = cfg[el.name];
    if (BOOL_FIELDS.has(el.name)) {
      el.checked = !!v;
    } else if (v == null) {
      el.value = "";
    } else {
      el.value = String(v);
    }
  });
  refreshStatusBadges();
}

function refreshStatusBadges() {
  const set = (id, ok) => {
    const el = $(id);
    el.textContent = ok ? "ready" : "missing";
    el.classList.toggle("ready", ok);
    el.classList.toggle("missing", !ok);
  };
  set("llm-status", !!$(REQUIRED.llm).value.trim());
  set("ark-status", !!$(REQUIRED.ark).value.trim());
  set("tts-status", !!$(REQUIRED.tts).value.trim());
}

function showToast(text, kind = "ok") {
  const t = $("save-toast");
  t.textContent = text;
  t.classList.remove("hidden", "ok", "err");
  t.classList.add(kind);
  setTimeout(() => t.classList.add("hidden"), 4000);
}

// ---------- Init ------------------------------------------------------------
(async function init() {
  // Wire reveal toggles for password fields
  document.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = $(btn.dataset.toggle);
      target.type = target.type === "password" ? "text" : "password";
    });
  });

  // Re-evaluate badges on every input
  inputs().forEach((el) => {
    el.addEventListener("input", refreshStatusBadges);
    el.addEventListener("change", refreshStatusBadges);
  });

  // Load saved config
  try {
    const r = await fetch("/api/config");
    const cfg = await r.json();
    fillForm(cfg);
  } catch (e) {
    showToast(`Couldn't load saved config: ${e.message || e}`, "err");
  }

  // Save
  form().addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = readForm();
    $("save-btn").disabled = true;
    $("save-btn").textContent = "Saving…";
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const text = await r.text();
        throw new Error(`HTTP ${r.status} — ${text.slice(0, 300)}`);
      }
      const j = await r.json();
      const missing = (j.status?.missing || []);
      if (missing.length) {
        showToast(`Saved. Still missing required keys: ${missing.join(", ")}.`, "err");
      } else {
        showToast("Saved. Ready to run.", "ok");
      }
    } catch (err) {
      showToast(`Save failed: ${err.message || err}`, "err");
    } finally {
      $("save-btn").disabled = false;
      $("save-btn").textContent = "Save settings";
    }
  });

  // Clear
  $("clear-btn").addEventListener("click", async () => {
    if (!confirm("Clear all saved settings? You'll need to re-enter the keys.")) return;
    inputs().forEach((el) => {
      if (BOOL_FIELDS.has(el.name)) el.checked = false;
      else el.value = "";
    });
    refreshStatusBadges();
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      showToast("Cleared.", "ok");
    } catch (err) {
      showToast(`Clear failed: ${err.message || err}`, "err");
    }
  });
})();
