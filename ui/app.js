// Lafz window: renders the app state (long-polled from the local server) and sends actions back.
"use strict";
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = {
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2.5"/><path d="M5 15V6a1 1 0 0 1 1-1h9"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="m6 12.5 4 4 8-9"/></svg>',
  bad: '<svg viewBox="0 0 24 24"><path d="M12 7v6M12 16.5v.5"/></svg>',
  wait: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="1"/><circle cx="7" cy="12" r="1"/><circle cx="17" cy="12" r="1"/></svg>',
  wave: '<svg viewBox="0 0 24 24"><path d="M4 12h2M8 8v8M12 5v14M16 8v8M20 12h-2"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M19 12a7 7 0 1 1-2.05-4.95"/><path d="M19 4.5V8h-3.5"/></svg>',
};

let S = null;              // latest state from the app
const seen = {};           // JSON of what each section last rendered, to skip unchanged work

async function api(action) {
  await fetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json", "X-Lafz-Token": TOKEN },
                               body: JSON.stringify(action) });
}
const set = (key, value) => api({ type: "set", key, value });

async function loop() {
  let version = 0;
  for (;;) {
    try {
      const r = await fetch(`/api/state?since=${version}`, { headers: { "X-Lafz-Token": TOKEN } });
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      version = data.version;
      S = data.state;
      render();
    } catch (e) {
      await new Promise((ok) => setTimeout(ok, 1000)); // app restarting: try again
    }
  }
}

function changed(name, value) {
  const j = JSON.stringify(value);
  if (seen[name] === j) return false;
  seen[name] = j;
  return true;
}

// ---------- navigation ----------
function go(page) {
  page = ["home", "history", "dictionary", "settings", "setup"].includes(page) ? page : "home";
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  if (location.hash !== `#${page}`) history.replaceState(null, "", `${location.search}#${page}`);
  $("main").scrollTop = 0;
}
window.lafzGo = go;
window.addEventListener("hashchange", () => go(location.hash.slice(1)));

// ---------- render ----------
function render() {
  if (!S) return;
  document.body.classList.toggle("windows", S.platform === "windows");
  renderLive();
  renderHero();
  if (changed("stats", S.stats)) renderStats();
  if (changed("latest", S.latest) && !setField($("latest"), S.latest)) delete seen.latest;  // retry after editing
  if (changed("style", [S.settings.style, S.options.styles])) renderStyles();
  if (changed("quick", [S.settings.auto_paste, S.settings.voice_commands, S.settings.learn])) renderQuickToggles();
  if (changed("history", S.history)) { renderRecent(); renderHistory(); }
  if (changed("dict", [S.settings.dictionary, S.dictionary])) { setField($("dictionary"), S.settings.dictionary); $("dict-count").textContent = `${S.dictionary.words} words · ${S.dictionary.fixes} fixes`; }
  if (changed("learned", S.learned)) renderLearned();
  if (changed("settings", [S.settings, S.options])) renderSettings();
  if (changed("setup", S.setup)) renderSetup();
  if (changed("compare", S.compare)) renderCompare();
  if (S.toast && changed("toast", S.toast.id) && Date.now() / 1000 - S.toast.id < 5) toast(S.toast.text);  // not stale ones
}

// Update a text box from the app, unless the user has typed into it since it was last set (never lose their edits).
// Focus alone isn't enough to block: a box clicked earlier keeps focus and would never show new dictations.
const lastSet = {};
function setField(el, value) {
  const edited = el.id in lastSet && el.value !== lastSet[el.id];
  if (edited && document.activeElement === el) return false;
  el.value = lastSet[el.id] = value ?? "";
  return true;
}

function hotkeyName() {
  return (S.options.hotkeys[S.settings.hotkey] || "").split(" (")[0];
}

function renderLive() {
  const live = $("live");
  live.classList.toggle("rec", S.mode === "listening" || S.mode === "command");
  live.classList.toggle("busy", S.mode === "transcribing" || S.mode === "cleaning");
  $("live-text").textContent = S.status;
  $("setup-badge").hidden = S.setup_ok;
}

let level = 0;
function renderHero() {
  const mode = S.mode === "listening" && S.locked ? "locked" : S.mode;
  const orb = $("orb");
  orb.className = `orb ${mode}`;
  const titles = {
    idle: S.setup_ok ? "Ready when you are" : "Almost ready",
    listening: "Listening…", locked: "Hands-free", command: "Command mode",
    transcribing: "Transcribing…", cleaning: "Polishing…",
  };
  const subs = {
    idle: !S.setup_ok ? "Finish the one-time setup so Lafz can hear you and paste for you."
      : S.model_status === "sleeping" ? "Resting to save memory. It wakes up the moment you speak."
      : S.model_status === "loading" ? "Loading the speech model…"
      : "Hold your hotkey, speak naturally, let go. Your words appear where your cursor is.",
    listening: "Let go of the hotkey when you're done.", locked: "Tap the hotkey once to finish.",
    command: "Say what to do with your selection, or what to write.",
    transcribing: "Turning your voice into text, on this computer.", cleaning: "Removing fillers and fixing punctuation.",
  };
  if (changed("hero", [mode, S.setup_ok, S.model_status])) {
    $("hero-title").textContent = titles[mode] || titles.idle;
    $("hero-sub").textContent = subs[mode] || subs.idle;
  }
  $("preview").textContent = S.preview ? `“${S.preview.slice(-140)}”` : "";
  if (changed("keys", [S.settings.hotkey, S.options.undo])) {
    const hk = `<kbd>${esc(hotkeyName())}</kbd>`;
    $("keys").innerHTML = `<span>${hk} Hold to talk</span><span>Double-tap for hands-free</span>` +
      `<span><kbd>⇧ Shift</kbd> + ${hk} Command mode</span><span><kbd>${esc(S.options.undo)}</kbd> Undo paste</span>`;
    $("undo-key").textContent = S.options.undo;
  }
  level = S.level;
}
(function animateOrb() { // smooth the 10 Hz level updates into 60 fps motion
  const orb = $("orb");
  const cur = parseFloat(orb.style.getPropertyValue("--s") || 0);
  const next = cur + (Math.min(1, level * 1.6) - cur) * 0.18;
  orb.style.setProperty("--s", next.toFixed(3));
  requestAnimationFrame(animateOrb);
})();

function renderStats() {
  const st = S.stats, n = (v) => Number(v).toLocaleString();
  $("st-words").textContent = n(st.words_today);
  $("st-dict").textContent = `${n(st.dictations_today)} dictation${st.dictations_today === 1 ? "" : "s"} today`;
  $("st-saved").innerHTML = `${n(st.minutes_saved)}<small>min</small>`;
  $("st-total").textContent = n(st.total_words);
}

function renderStyles() {
  const notes = { raw: "Exact words", clean: "Fillers removed", formal: "Polite rewrite", bullets: "As a list" };
  $("styles").innerHTML = Object.entries(S.options.styles).map(([k, v]) =>
    `<button class="${S.settings.style === k ? "on" : ""}" data-style="${k}">${esc(v.split(" (")[0])}<small>${notes[k] || ""}</small></button>`).join("");
}

function toggleHTML(key, title, desc) {
  return `<div class="toggle" data-toggle="${key}"><div class="t-text"><div class="t-title">${esc(title)}</div>` +
    (desc ? `<div class="t-desc">${esc(desc)}</div>` : "") + `</div><span class="switch ${S.settings[key] ? "on" : ""}"></span></div>`;
}

function renderQuickToggles() {
  $("quick-toggles").innerHTML = toggleHTML("auto_paste", "Auto-paste", "Type the result into the app you're in") +
    toggleHTML("voice_commands", "Voice commands", "“new line”, “new paragraph”, “bullet point”") +
    toggleHTML("learn", "Learn from my corrections", "Stays on this computer");
}

function itemHTML(h, actions = true) {
  const t = new Date(h.time * 1000);
  const time = t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `<div class="item"><div><div class="item-meta"><span>${time}</span>` +
    (h.app ? `<span class="chip">${esc(h.app)}</span>` : "") +
    (h.command ? `<span class="chip cmd" title="${esc(h.command)}">Command</span>` : "") +
    `</div><div class="item-text">${esc(h.text)}</div></div>` +
    `<div class="item-actions"><button class="icon-btn" title="Copy" data-copy="${h.i}">${icon.copy}</button>` +
    (actions ? `<button class="icon-btn" title="Delete" data-delete="${h.time}">${icon.trash}</button>` : "") + `</div></div>`;
}

function empty(text) {
  return `<div class="empty">${icon.wave}<div>${esc(text)}</div></div>`;
}

function renderRecent() {
  const items = S.history.slice(0, 4);
  $("recent").innerHTML = items.length ? items.map((h) => itemHTML(h, false)).join("") : empty("Your dictations will show up here.");
}

function renderHistory() {
  const q = $("search").value.trim().toLowerCase();
  const items = S.history.filter((h) => !q || h.text.toLowerCase().includes(q) || (h.app || "").toLowerCase().includes(q));
  if (!items.length) { $("history-list").innerHTML = empty(q ? "No matches." : "Nothing yet. Hold your hotkey and start talking."); return; }
  const dayName = (ts) => {
    const d = new Date(ts * 1000), today = new Date(); const y = new Date(); y.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
  };
  let html = "", last = "";
  for (const h of items) {
    const day = dayName(h.time);
    if (day !== last) { if (last) html += "</div></div>"; html += `<div class="day">${day}</div><div class="shell"><div class="core list">`; last = day; }
    html += itemHTML(h);
  }
  $("history-list").innerHTML = html + "</div></div>";
}

function renderLearned() {
  const { fixes, words } = S.learned;
  let html = "";
  if (fixes.length) html += `<div class="subhead">Automatic fixes</div><div class="chips">` + fixes.map(([w, r]) =>
    `<span class="lchip">${esc(w)} <span class="arrow">→</span> <b>${esc(r)}</b><button title="Forget" data-forget-fix="${esc(w)}" data-right="${esc(r)}">${icon.x}</button></span>`).join("") + "</div>";
  if (words.length) html += `<div class="subhead">Spelling hints</div><div class="chips">` + words.map((w) =>
    `<span class="lchip">${esc(w)}<button title="Forget" data-forget-word="${esc(w)}">${icon.x}</button></span>`).join("") + "</div>";
  $("learned").innerHTML = html || empty("Nothing learned yet. Keep dictating and fix mistakes as usual.");
}

function selectHTML(key, label, options, extra = "") {
  const opts = Object.entries(options).map(([v, n]) => `<option value="${esc(v)}" ${S.settings[key] === v ? "selected" : ""}>${esc(n)}</option>`).join("");
  return `<div class="setting"><label>${label}</label><div class="with-btn"><select class="field" data-select="${key}">${opts}</select>${extra}</div></div>`;
}

function renderSettings() {
  const o = S.options;
  const mics = { "": "System default" };
  o.mics.forEach((m) => (mics[m] = m));
  if (S.settings.mic && !mics[S.settings.mic]) mics[S.settings.mic] = `${S.settings.mic} (not connected)`;
  $("set-dictation").innerHTML = `<div class="card-head"><h3>Dictation</h3></div>` +
    selectHTML("hotkey", "Hotkey", o.hotkeys) + selectHTML("language", "Language", o.languages) +
    selectHTML("model", "Speech model", Object.fromEntries(Object.entries(o.models).map(([k, n]) => [k, n.split(" (")[0]])), `<button class="ghost small" id="open-compare" title="Compare models on your recent recordings">Compare</button>`) +
    selectHTML("mic", "Microphone", mics, `<button class="icon-btn" data-action="refresh" title="Refresh microphones">${icon.refresh}</button>`);
  $("set-behaviour").innerHTML = `<div class="card-head"><h3>Behaviour</h3></div><div class="toggles">` +
    toggleHTML("auto_paste", "Auto-paste", "Type the result into the app you were using") +
    toggleHTML("restore_clipboard", "Keep my clipboard", "Put back what you had copied after pasting") +
    toggleHTML("live_preview", "Live preview", "Show words while you're still speaking") +
    toggleHTML("voice_commands", "Voice commands", "“new line”, “new paragraph”, “bullet point”") +
    toggleHTML("self_corrections", "Fix self-corrections", "“at 3, no wait, 4 pm” becomes “at 4 pm”") +
    toggleHTML("sounds", "Sounds", "A soft tick when recording starts and finishes") + "</div>";
  $("set-privacy").innerHTML = `<div class="card-head"><h3>Privacy &amp; memory</h3></div><div class="toggles">` +
    toggleHTML("learn", "Learn from my corrections", "Fixes and names, stored only on this computer") +
    toggleHTML("keep_recordings", "Keep last 10 recordings", "Only for comparing models. Off deletes them") +
    toggleHTML("memory_saver", "Memory saver", "Rest the speech model after 10 idle minutes") + "</div>";
  $("set-system").innerHTML = `<div class="card-head"><h3>System</h3></div><div class="toggles">` +
    toggleHTML("start_at_login", "Start at login", "") + toggleHTML("start_minimized", "Start in the background", "Open without showing this window") +
    (S.platform === "mac" ? toggleHTML("show_in_dock", "Show in Dock", "Off: menu bar only") : "") + "</div>";
  setField($("rules"), S.settings.app_rules);
  $("about").textContent = `Lafz ${S.version} · MIT License · Everything runs on this computer.`;
  $("restart-btn").hidden = S.platform !== "mac";
}

function renderSetup() {
  const req = S.setup.filter((i) => i.required);
  const done = req.filter((i) => i.ok === true).length;
  $("setup-lead").textContent = S.setup_ok ? "All set. Lafz is ready to listen." :
    `${done} of ${req.length} required steps done. ${S.platform === "mac" ? "After changing a permission, click Restart Lafz." : ""}`;
  $("setup-list").innerHTML = S.setup.map((i) => {
    const cls = i.ok === true ? "ok" : i.ok === false ? (i.required ? "bad" : "wait") : "wait";
    return `<div class="check"><span class="status-icon ${cls}">${icon[cls === "ok" ? "ok" : cls === "bad" ? "bad" : "wait"]}</span>` +
      `<div><div class="check-name">${esc(i.name)}${i.required ? "" : ' <span class="chip">optional</span>'}</div><div class="check-detail">${esc(i.detail)}</div></div>` +
      (i.button ? `<button class="pill" data-setup="${i.id}"><span>${esc(i.button)}</span><i class="pill-icon"><svg viewBox="0 0 24 24"><path d="M7 17 17 7M9 7h8v8"/></svg></i></button>` : "<span></span>") + "</div>";
  }).join("");
}

function renderCompare() {
  const c = S.compare;
  $("compare-status").innerHTML = (c.running ? '<span class="spinner"></span>' : "") + esc(c.status || "");
  const res = c.results;
  if (!res) { $("compare-results").innerHTML = ""; $("compare-buttons").innerHTML = ""; return; }
  const names = S.options.models;
  $("compare-results").innerHTML = res.rows.map((row, n) => {
    const when = new Date(row.time * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    return `<div class="cmp-row"><div class="item-meta">Recording ${n + 1} · ${when} · ${row.seconds ?? "?"} s</div>` +
      Object.entries(row.texts).map(([k, t]) => `<div class="cmp-line"><b>${esc(names[k].split(" (")[0])}</b><span>${esc(t)}</span></div>`).join("") + "</div>";
  }).join("") + `<p class="muted small" style="margin-top:12px">Average time: ` +
    Object.entries(res.timing).map(([k, s]) => `${esc(names[k].split(" (")[0])} ${s}s`).join(" · ") + "</p>";
  $("compare-buttons").innerHTML = Object.entries(names).map(([k, n]) =>
    `<button class="pill ${S.settings.model === k ? "primary" : ""}" data-use-model="${k}"><span>Use ${esc(n.split(" (")[0])}</span><i class="pill-icon">${icon.ok}</i></button>`).join("");
}

let toastTimer;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

// ---------- interactions ----------
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
$("dictionary").addEventListener("input", debounce((e) => api({ type: "save_dictionary", text: e.target.value }), 600));
$("rules").addEventListener("input", debounce((e) => api({ type: "save_rules", text: e.target.value }), 600));
$("search").addEventListener("input", () => S && renderHistory());
$("copy-latest").addEventListener("click", () => {
  lastSet.latest = $("latest").value;  // copied = saved, so the next dictation may replace it
  api({ type: "copy_latest", text: $("latest").value });
});
$("latest").addEventListener("blur", () => S && render());
let clearArmed = null;  // deleting history takes two clicks, so it can't happen by accident
$("clear-history").addEventListener("click", (e) => {
  if (!S || !S.history.length) return;
  const btn = e.currentTarget;
  if (clearArmed) { clearTimeout(clearArmed); clearArmed = null; btn.textContent = "Clear all"; api({ type: "clear_history" }); return; }
  btn.textContent = "Click again to delete all";
  clearArmed = setTimeout(() => { clearArmed = null; btn.textContent = "Clear all"; }, 3000);
});
$("reset-learning").addEventListener("click", () => api({ type: "reset_learning" }));
$("compare-close").addEventListener("click", () => ($("compare").hidden = true));

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action],[data-style],[data-toggle],[data-copy],[data-delete],[data-forget-fix],[data-forget-word],[data-setup],[data-use-model],#open-compare");
  if (!el || !S) return;
  const d = el.dataset;
  if (d.action) api({ type: d.action });
  else if (d.style) set("style", d.style);
  else if (d.toggle) { el.querySelector(".switch").classList.toggle("on"); set(d.toggle, !S.settings[d.toggle]); }
  else if (d.copy !== undefined) api({ type: "copy", text: S.history.find((h) => h.i === +d.copy).text });
  else if (d.delete !== undefined) api({ type: "delete_history", time: +d.delete });
  else if (d.forgetFix !== undefined) api({ type: "forget_fix", wrong: d.forgetFix, right: d.right });
  else if (d.forgetWord !== undefined) api({ type: "forget_word", word: d.forgetWord });
  else if (d.setup) api({ type: "setup", id: d.setup });
  else if (d.useModel) { set("model", d.useModel); $("compare").hidden = true; }
  else if (el.id === "open-compare") { $("compare").hidden = false; api({ type: "compare" }); }
});
document.addEventListener("change", (e) => {
  const key = e.target.dataset && e.target.dataset.select;
  if (key) set(key, e.target.value);
});

go(location.hash.slice(1));
loop();
