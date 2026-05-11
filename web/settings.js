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
    } else if (v == null || v === "") {
      _applyDefault(el);
    } else {
      el.value = String(v);
    }
  });
  refreshStatusBadges();
}

// Apply the "no saved value" default to a single field. Centralised so
// fillForm and the Clear button stay in lockstep — Clear should leave
// the form looking exactly like a first-time visit, not blanker.
//
// Why placeholder-as-real-value for text/number inputs:
//   Greyed placeholder text reads as "empty" to most users — they
//   can't tell apart "the system default is gpt-4o-mini" from "you
//   haven't entered anything yet". Promoting the placeholder to a
//   real, selectable, editable value (same affordance as Apple's
//   System Settings → defaults shown in black, not ghost grey) makes
//   the configuration legible: what you see is what will run.
//
// Password fields are excluded — their placeholders are `sk-...` /
// `...` template hints, not real values.
function _applyDefault(el) {
  if (el.tagName === "SELECT") {
    // Select with an [selected] attribute → that option; else first.
    el.selectedIndex = Math.max(0, _firstSelectedIndex(el));
  } else if (el.type !== "password" && el.placeholder) {
    el.value = el.placeholder;
  } else {
    el.value = "";
  }
}

function _firstSelectedIndex(selectEl) {
  // Find the option with the [selected] attribute, or fall through
  // to 0. Lets HTML markup declare the default cleanly with
  // <option value="X" selected>X</option>.
  for (let i = 0; i < selectEl.options.length; i++) {
    if (selectEl.options[i].defaultSelected) return i;
  }
  return 0;
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
  // + New session — same affordance as Console. Opens a clean session in a
  // new tab; the current Settings tab stays untouched.
  const newSessionBtn = $("new-session-btn");
  if (newSessionBtn) {
    newSessionBtn.addEventListener("click", () => {
      window.open("/", "_blank", "noopener");
    });
  }

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

  // Quick-fill preset chips. Each chip carries a JSON map in data-presets:
  //   { "<input-id>": "<value>", "<input-id>": "<value>", ... }
  // Click → fill every named field at once + pulse each one so the user
  // sees what changed. Lets a single chip fill coupled fields like Base
  // URL + Model together (a Doubao chip should set both, not just one).
  document.querySelectorAll(".api-preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      let presets;
      try {
        presets = JSON.parse(chip.dataset.presets || "{}");
      } catch {
        return;
      }
      Object.entries(presets).forEach(([id, value]) => {
        const target = $(id);
        if (!target) return;
        target.value = value;
        target.dispatchEvent(new Event("input", { bubbles: true }));
        target.classList.remove("input-flash");
        void target.offsetWidth;  // force reflow so the animation re-fires
        target.classList.add("input-flash");
      });
    });
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
      if (BOOL_FIELDS.has(el.name)) {
        el.checked = false;
      } else {
        // Same default-application as a first-time visit — Clear
        // should restore the visible "system default" state, not
        // leave the user staring at empty inputs.
        _applyDefault(el);
      }
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
