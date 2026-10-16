/* MarbleTrack UI: setup wizard + live race control.
   Talks to the local server (marbletrack_app/server.py). Every action posts to /api/<action> and
   gets the new state back; live data and toasts arrive on one Server-Sent Events stream. */
"use strict";

const TOKEN = new URLSearchParams(location.search).get("k") || "";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PR = new Intl.PluralRules("en");
const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${PR.select(n) === "one" ? one : many}`;
const fmtM = (v, d = 2) => (v == null || !isFinite(v) ? "—" : `${(+v).toFixed(d)} m`);
const fmtS = (v, d = 3) => (v == null || !isFinite(v) ? "—" : `${(+v).toFixed(d)} s`);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const I = {
  check: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3L13 4.5"/></svg>',
  alert: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M8 1.8 15 14H1z" stroke-linejoin="round"/><path d="M8 6.2v3.6M8 12h.01"/></svg>',
  info: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="8" r="6.4"/><path d="M8 7.2V11M8 5h.01"/></svg>',
  ok: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6.4"/><path d="M5.2 8.2l2 1.9 3.6-3.8"/></svg>',
  chev: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5 10.5 8 6 12.5"/></svg>',
  up: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 10 8 5.5 12.5 10"/></svg>',
  down: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6 8 10.5 12.5 6"/></svg>',
  x: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  undo: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 3 2.5 6l3 3"/><path d="M3 6h6.5a4 4 0 0 1 0 8H7"/></svg>',
  plus: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 3v10M3 8h10"/></svg>',
  search: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="7" cy="7" r="4.6"/><path d="M10.5 10.5 14 14"/></svg>',
  play: '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M4.5 2.8v10.4L13 8z"/></svg>',
  wand: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2.5 13.5 10 6M11.5 2v2M14 4.5h-2M13 7.5l-1.3-1.3M8.5 3 9.8 4.3"/></svg>',
};

/* ------------------------------------------------------------------ server calls */
const S = { st: null, step: null, sel: { wp: null, col: null, marble: null }, live: null, dirty: false, saving: false };

async function api(action, body = {}, opts = {}) {
  let res, data;
  try {
    res = await fetch(`/api/${action}`, { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body) });
    data = await res.json();
  } catch (e) {
    toast("bad", "The app's engine is not responding. If this persists, restart MarbleTrack.");
    throw e;
  }
  if (!res.ok) {
    if (!opts.quiet) toast(res.status === 409 ? "warn" : "bad", data.error || "Something went wrong");
    throw new Error(data.error);
  }
  if (data && data.steps) setState(data, opts.mutates !== false);
  return data;
}
async function loadState() {
  const r = await fetch("/api/state", { headers: { "X-Token": TOKEN } });
  if (!r.ok) { document.body.innerHTML = '<p style="padding:40px">This page must be opened from the MarbleTrack app.</p>'; throw new Error("auth"); }
  setState(await r.json(), false);
}
const img = (path, q = "") => `/api/${path}?k=${encodeURIComponent(TOKEN)}${q}`;

let saveTimer = null;
function setState(st, mutated) {
  S.st = st;
  if (mutated && st.mode === "setup") {
    S.dirty = true;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(autosave, 1200);
  }
  render();
}
async function autosave() {
  if (!S.dirty || S.saving) return;
  S.saving = true; $("#saved").textContent = "Saving…";
  try {
    const r = await fetch("/api/save", { method: "POST", headers: { "X-Token": TOKEN } });
    if (!r.ok) throw new Error((await r.json()).error);
    S.dirty = false; $("#saved").textContent = "All changes saved";
    if (!S.st.folder) await loadState();
  }
  catch { $("#saved").textContent = "Not saved"; }
  S.saving = false;
}

/* ------------------------------------------------------------------ toasts (enter from the edge they live on; leave faster) */
function toast(kind, text, ms = 4200) {
  const box = $("#toaster");
  while (box.children.length >= 4) box.firstElementChild.remove();
  const el = document.createElement("div");
  el.className = `toast ${kind} enter`;
  el.setAttribute("role", kind === "bad" ? "alert" : "status");
  el.innerHTML = `${kind === "ok" ? I.ok : kind === "info" ? I.info : I.alert}<div class="tx">${esc(text)}</div>`;
  box.append(el);
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("enter")));
  let timer = setTimeout(close, ms);
  el.addEventListener("pointerenter", () => clearTimeout(timer));  // hovering keeps it open
  el.addEventListener("pointerleave", () => (timer = setTimeout(close, 1500)));
  function close() { el.classList.add("leave"); setTimeout(() => el.remove(), 160); }
}

/* ------------------------------------------------------------------ busy buttons */
async function busy(btn, label, fn) {
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span>${esc(label)}`;
  try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = old; }
}

/* ------------------------------------------------------------------ layout */
const STEPS = [
  { id: "race", t: "Race & marbles" }, { id: "cameras", t: "Cameras" }, { id: "waypoints", t: "Track waypoints" },
  { id: "background", t: "Empty track" }, { id: "colours", t: "Marble colours" }, { id: "server", t: "Leaderboard output" },
  { id: "review", t: "Review & go live" },
];
const active = () => S.st.cameras.filter((c) => c.enabled);

function stepSummary(id) {
  const st = S.st, p = st.project, act = active();
  switch (id) {
    case "race": return `${plural(p.marbles.length, "marble")} · ${p.track_length ? fmtM(p.track_length, 1) : "length not set"}${p.laps > 1 ? ` · ${p.laps} laps` : ""}`;
    case "cameras": return st.cameras.length ? `${act.length} of ${plural(st.cameras.length, "camera")} in use` : "None found yet";
    case "waypoints": return `${act.filter((c) => c.markers.length >= 2).length} of ${act.length} cameras placed`;
    case "background": return `${act.filter((c) => c.has_background).length} of ${act.length} captured`;
    case "colours": return `${act.filter((c) => p.marbles.every((m) => c.palette[m.id])).length} of ${act.length} cameras complete`;
    case "server": {
      const ws = p.server.enabled ? (p.server.url ? p.server.url.replace(/^wss?:\/\//, "") : "Address not set") : "";
      const vm = p.vmix.enabled ? `vMix: ${p.vmix.input}` : "";
      return [ws, vm].filter(Boolean).join(" · ") || "No output";
    }
    case "review": { const n = st.steps.review.length; return n ? `${plural(n, "thing")} left` : "Ready to go live"; }
  }
}

function render() {
  const st = S.st;
  const live = st.mode === "live";
  $("#app").classList.toggle("is-live", live);
  $("#live").hidden = !live;
  $("#modePill").textContent = live ? "Live" : "Setup";
  $("#modePill").classList.toggle("on-air", live);
  $("#projectName").textContent = st.project.name;
  $("#projectName").title = st.project.name;
  $("#mapsBtn").hidden = live;
  if (live) { renderLive(); return; }
  stopLive();
  if (S.step !== st.step) mountStep(st.step, S.step != null);
  renderRail();
  updateStep();
  renderFoot();
}

function renderRail() {
  const st = S.st;
  $("#rail").innerHTML = `<h2>Setup</h2>` + STEPS.map((s, i) => {
    const issues = st.steps[s.id] || [];
    const needsCams = ["cameras", "waypoints", "background", "colours", "review"].includes(s.id);
    const done = !issues.length && (!needsCams || active().length > 0);
    const cls = `${s.id === st.step ? "on" : ""} ${done ? "done" : "todo"}`;
    return `<button class="step ${cls}" data-step="${s.id}" aria-current="${s.id === st.step ? "step" : "false"}">
      <span class="dot">${done ? I.check : i + 1}</span><b>${s.t}</b><small>${esc(stepSummary(s.id))}</small></button>`;
  }).join("") + `<div class="spacer"></div><div class="meta">
      ${st.demo ? '<span class="chip acc">Demo cameras on</span>' : ""}
      <span class="ellipsis" title="${esc(st.folder || "")}">${st.folder ? "Saved in " + esc(st.folder) : "Not saved yet"}</span>
      <span>Version ${esc(st.version)}</span></div>`;
}

function renderFoot() {
  const st = S.st, i = STEPS.findIndex((s) => s.id === st.step), issues = st.steps[st.step] || [];
  const last = st.step === "review";
  const ready = !st.steps.review.length;
  $("#foot").innerHTML = `
    <button class="btn ghost" id="back" ${i === 0 ? "disabled" : ""}>Back</button>
    <div class="msg ${issues.length ? "" : "ok"}" title="${esc(issues.join("\n"))}">${issues.length ? esc(issues[0]) + (issues.length > 1 ? ` (+${issues.length - 1} more)` : "") : last ? "Everything is set up." : "This step is complete."}</div>
    ${last ? `<button class="btn primary" id="golive" ${ready ? "" : "disabled"} title="${ready ? "" : "Finish the steps marked above first"}">${I.play}Go live</button>`
           : `<button class="btn primary" id="next">Continue</button>`}`;
  $("#back").onclick = () => go(STEPS[i - 1].id);
  if ($("#next")) $("#next").onclick = () => go(STEPS[i + 1].id);
  if ($("#golive")) $("#golive").onclick = (e) => busy(e.currentTarget, "Starting cameras…", () => api("go_live", {}, { mutates: false }));
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-step]");
  if (b && S.st.mode === "setup") go(b.dataset.step, e.detail === 0);
});
async function go(step, fromKeyboard = false) {
  S.instant = fromKeyboard;
  await api("set_step", { step }, { mutates: false });
  await loadState();
}

/* ------------------------------------------------------------------ step mounting */
const VIEWS = {};
let current = null;
function mountStep(step, animate) {
  S.step = step;
  closePop();
  if (current && current.unmount) current.unmount();
  const main = $("#main");
  main.innerHTML = "";
  const panel = document.createElement("div");
  panel.className = "panel" + (animate && !S.instant ? " enter" : "");
  main.append(panel);
  current = VIEWS[step];
  current.mount(panel);
  main.scrollTop = 0;
  if (animate && !S.instant) requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.remove("enter")));
}
function updateStep() { if (current && current.update) current.update(); }

const head = (title, text) => `<div class="head"><h1>${title}</h1><p>${text}</p></div>`;
const note = (kind, html) => `<div class="note ${kind}">${kind === "ok" ? I.ok : kind === "info" ? I.info : I.alert}<div>${html}</div></div>`;
function num(id, label, value, unit, step = "0.01", hint = "") {
  return `<label class="field"><span>${label}</span><div class="unit" data-unit="${unit}"><input class="input num" id="${id}" type="number" inputmode="decimal" min="0" step="${step}" value="${value ?? ""}"></div>${hint ? `<small>${hint}</small>` : ""}</label>`;
}

/* ================================================================== 1. race & marbles */
VIEWS.race = {
  mount(el) {
    const p = S.st.project;
    el.innerHTML = head("Race & marbles", "Describe the track and the marbles once. Distances are measured along the track, from the start gate.") + `
      <div class="card stack">
        <label class="field"><span>Race name</span><input class="input" id="pname" maxlength="80" value="${esc(p.name)}"></label>
        <div class="grid3">
          ${num("plen", p.laps > 1 ? "Lap length" : "Track length", p.track_length || "", "m", "0.01", p.laps > 1 ? "Start line, once round the circuit, back to the start line." : "Start gate to the end of the run-out.")}
          ${num("pfin", "Finish line at", p.finish_s || "", "m", "0.01", p.laps > 1 ? "Distance from the start line, crossed on the last lap." : "Distance from the start gate.")}
          ${num("plaps", "Laps", p.laps, "laps", "1", "More than one: the track is a circuit that returns to the start line.")}
          ${num("pspc", "Waypoint marks every", p.marker_spacing, "m", "0.05", "Put a small mark on the track at this spacing.")}
        </div>
        <details class="more"><summary>${I.chev}More track settings</summary><div class="grid3">
          ${num("pstart", "Race clock starts at", p.start_s, "m", "0.01", "The clock starts when the first marble passes here.")}
          ${num("pchan", "Channel width", Math.round(p.track_width_m * 1000), "mm", "1", "Inside width of the running channel.")}
          ${num("pdia", "Marble diameter", p.marble_diameter_mm, "mm", "0.5")}
          <label class="field" style="grid-column: 1 / -1"><span>Tunnels and other places no camera can see</span>
            <input class="input" id="pblind" placeholder="e.g. 3.6–4.0, 9.2–9.5" value="${esc(p.blind_zones.map(([a, b]) => `${a}–${b}`).join(", "))}">
            <small>Ranges in metres. The tracker holds positions there instead of guessing.</small></label>
          <label class="field" style="grid-column: 1 / -1"><span>Wider sections of the channel</span>
            <input class="input" id="pwide" placeholder="e.g. 6.5–7.0 @ 105, 15–15.7 @ 90" value="${esc((p.wide_zones || []).map(([a, b, w]) => `${a}–${b} @ ${Math.round(w * 1000)}`).join(", "))}">
            <small>From–to in metres @ inside width in mm, for obstacle zones and splitters wider than the channel width above. The search band follows them.</small></label>
          <label class="toggle"><input type="checkbox" id="pauto" ${p.auto_new_race ? "checked" : ""}> Start a new race automatically when marbles return after the track was clear</label>
        </div></details>
      </div>
      <div class="card stack">
        <div class="row"><h3 style="margin:0;flex:1">Marbles <span class="chip" id="mcount"></span></h3>
          <button class="btn sm" id="madd">${I.plus}Add marble</button></div>
        <div class="marbles" id="mlist"></div>
        <small class="hint">The colour swatch is for the leaderboard only. Each camera learns the real colours in step 5.</small>
      </div>`;
    const commit = (k, v) => api("update_race", { [k]: v });
    $("#pname", el).onchange = (e) => commit("name", e.target.value);
    $("#plen", el).onchange = (e) => commit("track_length", +e.target.value || 0);
    $("#pfin", el).onchange = (e) => commit("finish_s", +e.target.value || 0);
    $("#plaps", el).onchange = (e) => commit("laps", Math.max(1, Math.round(+e.target.value || 1)));
    $("#pspc", el).onchange = (e) => commit("marker_spacing", Math.max(0.02, +e.target.value || 0.2));
    $("#pstart", el).onchange = (e) => commit("start_s", +e.target.value || 0);
    $("#pchan", el).onchange = (e) => commit("track_width_m", (+e.target.value || 50) / 1000);
    $("#pdia", el).onchange = (e) => commit("marble_diameter_mm", +e.target.value || 25);
    $("#pauto", el).onchange = (e) => commit("auto_new_race", e.target.checked);
    $("#pblind", el).onchange = (e) => {
      const zones = e.target.value.split(/[,;]/).map((x) => x.trim()).filter(Boolean).map((x) => x.split(/\s*[–-]\s*/).map(Number));
      if (zones.some((z) => z.length !== 2 || z.some((v) => !isFinite(v)))) { toast("warn", "Write ranges like 3.6–4.0, separated by commas."); return; }
      commit("blind_zones", zones);
    };
    $("#pwide", el).onchange = (e) => {
      const zones = e.target.value.split(/[,;]/).map((x) => x.trim()).filter(Boolean).map((x) => {
        const m = x.match(/^([\d.]+)\s*[–-]\s*([\d.]+)\s*@\s*([\d.]+)$/);
        return m ? [+m[1], +m[2], +m[3] / 1000] : null;
      });
      if (zones.some((z) => !z || z.some((v) => !isFinite(v)) || z[1] <= z[0])) { toast("warn", "Write sections like 6.5–7.0 @ 105 (metres, then width in mm), separated by commas."); return; }
      commit("wide_zones", zones);
    };
    $("#madd", el).onclick = () => {
      const ms = S.st.project.marbles.map((m) => ({ ...m }));
      const used = new Set(ms.map((m) => m.name.toLowerCase()));
      let n = ms.length + 1; while (used.has(`marble ${n}`)) n++;
      ms.push({ id: "", name: `Marble ${n}`, color: "#888888" });
      api("set_marbles", { marbles: ms }).then(() => { const ins = $$("#mlist input.input"); ins[ins.length - 1]?.select(); });
    };
    this.renderMarbles();
  },
  renderMarbles() {
    const ms = S.st.project.marbles;
    $("#mcount").textContent = ms.length;
    $("#mlist").innerHTML = ms.map((m, i) => `<div class="marble-row" data-i="${i}">
        <input type="color" value="${esc(m.color)}" aria-label="Colour of ${esc(m.name)}">
        <input class="input" value="${esc(m.name)}" maxlength="40" aria-label="Marble name">
        <button class="btn icon ghost" title="Remove ${esc(m.name)}" aria-label="Remove ${esc(m.name)}">${I.x}</button></div>`).join("");
    $$("#mlist .marble-row").forEach((row) => {
      const i = +row.dataset.i;
      const send = (patch) => api("set_marbles", { marbles: S.st.project.marbles.map((m, j) => (j === i ? { ...m, ...patch } : m)) });
      $("input[type=color]", row).onchange = (e) => send({ color: e.target.value });
      $("input.input", row).onchange = (e) => send({ name: e.target.value.trim() || S.st.project.marbles[i].name });
      $("button", row).onclick = () => {
        const m = S.st.project.marbles[i];
        const sampled = S.st.cameras.some((c) => c.palette[m.id]);
        if (sampled && !confirm(`Remove ${m.name}? Its colour samples on every camera are deleted too.`)) return;
        api("set_marbles", { marbles: S.st.project.marbles.filter((_, j) => j !== i) });
      };
    });
  },
  update() { if ($("#mlist") && $("#mlist").children.length !== S.st.project.marbles.length) this.renderMarbles(); else if ($("#mcount")) $("#mcount").textContent = S.st.project.marbles.length; },
};

/* ================================================================== 2. cameras */
VIEWS.cameras = {
  mount(el) {
    this.el = el;
    el.innerHTML = head("Cameras", "Connect every camera, then put them in order along the track, from the start gate to the finish. The finish camera gets its own role.") +
      `<div class="row"><button class="btn" id="find">${I.search}Find cameras</button><span class="hint" id="findnote"></span></div><div id="camlist"></div>`;
    $("#find", el).onclick = (e) => this.find(e.currentTarget);
    if (!S.st.cameras.length) this.find($("#find", el));
    this.draw(true);
  },
  async find(btn) {
    await busy(btn, "Looking for cameras…", () => api("discover_cameras", {}, { mutates: true }));
    const n = S.st.cameras.filter((c) => c.connected).length;
    $("#findnote").textContent = n ? `${plural(n, "camera")} connected.` : "";
    this.draw(true);
  },
  draw(stagger) {
    const cams = S.st.cameras, box = $("#camlist");
    if (!box) return;
    if (!cams.length) {
      box.innerHTML = `<div class="card empty-state"><h3>No cameras found</h3><p>MarbleTrack looks for USB and industrial (Basler) cameras.</p>
        <ul><li>Check the cable and that the camera has power.</li><li>Close other apps that use the camera (Teams, Zoom, the Camera app).</li>
        <li>Many cameras on one USB controller can exceed its bandwidth: spread them over ports or add a USB card.</li>
        <li>Basler cameras need the pylon software installed.</li></ul>
        ${S.st.demo ? "" : '<p class="hint">To try MarbleTrack without hardware, start it with <code>--demo</code>.</p>'}</div>`;
      return;
    }
    const sec = cams.filter((c) => c.enabled && c.role === "section");
    box.innerHTML = `<div class="cams">${cams.map((c, i) => {
      const order = c.enabled ? (c.role === "finish" ? "Finish" : `Section ${sec.indexOf(c) + 1}`) : "Not used";
      return `<article class="cam ${c.enabled ? "" : "off"} ${stagger ? "enter" : ""}" data-key="${esc(c.key)}" style="transition-delay:${stagger && !reduceMotion ? Math.min(i, 8) * 40 : 0}ms">
        <div class="thumb">${c.connected ? `<img alt="" data-snap="${esc(c.key)}">` : `<div class="empty">Not connected.<br>Plug it in, then Find cameras.</div>`}
          <span class="order">${order}</span>${c.preview.error ? `<span class="badge chip bad" title="${esc(c.preview.error)}">Error</span>` : ""}</div>
        <div class="cam-body">
          <input class="input name" value="${esc(c.name)}" maxlength="60" aria-label="Camera name" title="${esc(c.name)}">
          <div class="cam-meta"><span class="num">${c.width && c.height ? `${c.width}×${c.height}` : "—"}</span><span>${c.fps ? Math.round(c.fps) + " fps" : ""}</span><span class="ellipsis" title="${esc(c.key)}">${esc(c.backend === "opencv" ? "USB" : c.backend === "pylon" ? "Basler" : "Demo")}</span></div>
          <div class="row">
            <label class="toggle" style="flex:1"><input type="checkbox" data-f="enabled" ${c.enabled ? "checked" : ""}> Use this camera</label>
            <button class="btn icon" data-mv="-1" title="Earlier on the track" aria-label="Move earlier" ${i === 0 ? "disabled" : ""}>${I.up}</button>
            <button class="btn icon" data-mv="1" title="Later on the track" aria-label="Move later" ${i === cams.length - 1 ? "disabled" : ""}>${I.down}</button>
          </div>
          <select class="input" data-f="role" aria-label="What this camera watches"><option value="section" ${c.role === "section" ? "selected" : ""}>Watches a track section</option><option value="finish" ${c.role === "finish" ? "selected" : ""}>Watches the finish line</option></select>
          ${c.backend === "demo" ? "" : `<details class="more"><summary>${I.chev}Exposure & colour</summary><div class="stack">
            <label class="toggle"><input type="checkbox" data-f="auto_exposure" ${c.auto_exposure ? "checked" : ""}> Automatic exposure</label>
            <label class="field"><span>Exposure</span><input type="range" min="-13" max="-1" step="1" data-f="exposure" value="${c.exposure ?? -6}" ${c.auto_exposure ? "disabled" : ""}><small>Short exposures stop motion blur. Keep the lighting constant.</small></label>
            <label class="toggle"><input type="checkbox" data-f="auto_white_balance" ${c.auto_white_balance ? "checked" : ""}> Automatic white balance</label>
            <small class="hint">Lock both for racing: automatic changes shift the marble colours the tracker learned.</small>
            ${num("lat-" + i, "Camera delay", c.latency_ms, "ms", "1", "Time from exposure to the PC. USB webcams: about 40 ms.").replace('id="lat-' + i + '"', `data-f="latency_ms"`)}
          </div></details>`}
          ${c.connected ? "" : `<button class="btn sm ghost danger" data-forget>Remove from project</button>`}
        </div></article>`;
    }).join("")}</div>`;
    $$(".cam", box).forEach((card) => {
      const key = card.dataset.key;
      $$("[data-f]", card).forEach((inp) => (inp.onchange = () => {
        const f = inp.dataset.f, v = inp.type === "checkbox" ? inp.checked : inp.type === "range" || inp.type === "number" ? +inp.value : inp.value;
        api("update_camera", { key, [f]: v }).then(() => this.draw(false));
      }));
      $(".name", card).onchange = (e) => api("update_camera", { key, name: e.target.value });
      $$("[data-mv]", card).forEach((b) => (b.onclick = () => api("move_camera", { key, delta: +b.dataset.mv }).then(() => this.draw(false))));
      const fg = $("[data-forget]", card); if (fg) fg.onclick = () => api("forget_camera", { key }).then(() => this.draw(false));
    });
    if (stagger) requestAnimationFrame(() => requestAnimationFrame(() => $$(".cam.enter", box).forEach((c) => c.classList.remove("enter"))));
    startSnapshots(box);
  },
  update() { /* card inputs commit on change; redraw only after structural actions */ },
  unmount() { stopSnapshots(); },
};

/* thumbnails: each image asks for the next one when the previous arrived (self-paced, no stream per tile) */
let snapGen = 0;
function startSnapshots(root, w = 320, gap = 400) {
  const gen = ++snapGen;
  $$("img[data-snap]", root).forEach((im) => {
    const key = im.dataset.snap;
    const next = () => { if (gen !== snapGen || !im.isConnected) return; im.src = img(`snapshot/${encodeURIComponent(key)}`, `&w=${w}&t=${Date.now()}`); };
    im.onload = () => setTimeout(next, gap);
    im.onerror = () => setTimeout(next, 1500);
    next();
  });
}
function stopSnapshots() { snapGen++; }

/* ================================================================== shared camera viewer */
function camTabs(sel, kind) {
  return `<div class="tabs" role="tablist">${active().map((c) => {
    const done = kind === "wp" ? c.markers.length >= 2 : S.st.project.marbles.every((m) => c.palette[m.id]);
    const warn = kind === "col" && c.palette_issues.length;
    return `<button class="tab-btn ${c.key === sel ? "on" : ""}" role="tab" aria-selected="${c.key === sel}" data-cam="${esc(c.key)}" title="${esc(c.name)}">
      <span class="st ${warn ? "warn" : done ? "ok" : ""}"></span><span class="l">${esc(c.name)}</span></button>`;
  }).join("")}</div>`;
}

class Viewer {
  constructor(host, cam, { onClick, overlay }) {
    this.cam = cam; this.onClick = onClick; this.overlay = overlay;
    host.innerHTML = `<div class="viewer" style="aspect-ratio:${cam.width || 4} / ${cam.height || 3}">
      <img alt="Live image of ${esc(cam.name)}" draggable="false">
      <svg class="ov" viewBox="0 0 ${cam.width || 640} ${cam.height || 480}" preserveAspectRatio="none"></svg>
      <span class="status"></span></div>`;
    this.box = $(".viewer", host); this.img = $("img", host); this.svg = $("svg", host); this.status = $(".status", host);
    this.img.src = img(`stream/${encodeURIComponent(cam.key)}`, "&w=1280");
    this.img.onerror = () => { this.status.textContent = "No image: check the camera"; };
    this.box.addEventListener("pointermove", (e) => this.loupe(e));
    this.box.addEventListener("pointerleave", () => ($("#loupe").hidden = true));
    this.box.addEventListener("click", (e) => {
      if (this.dragged || e.target.closest(".lbl,.mk")) return;
      const p = this.toImage(e); if (p) this.onClick(p, e);
    });
    this.draw();
  }
  toImage(e) {
    const r = this.box.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * (this.cam.width || 640), y = ((e.clientY - r.top) / r.height) * (this.cam.height || 480);
    return x >= 0 && y >= 0 ? { x, y } : null;
  }
  scale() { return (this.cam.width || 640) / this.box.getBoundingClientRect().width; }  // image px per screen px
  draw() { this.svg.innerHTML = this.overlay(this.scale()); }
  loupe(e) {
    const L = $("#loupe"), ctx = L.getContext("2d"), r = this.box.getBoundingClientRect();
    if (!this.img.naturalWidth) return;
    const zoom = 3, size = L.width;
    const sx = ((e.clientX - r.left) / r.width) * this.img.naturalWidth, sy = ((e.clientY - r.top) / r.height) * this.img.naturalHeight;
    const k = this.img.naturalWidth / r.width, src = size / zoom * k;
    ctx.save(); ctx.clearRect(0, 0, size, size); ctx.beginPath(); ctx.arc(size / 2, size / 2, size / 2, 0, 7); ctx.clip();
    try { ctx.drawImage(this.img, sx - src / 2, sy - src / 2, src, src, 0, 0, size, size); } catch { /* frame not ready */ }
    ctx.strokeStyle = "rgba(242,169,59,.95)"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(size / 2 - 10, size / 2); ctx.lineTo(size / 2 + 10, size / 2); ctx.moveTo(size / 2, size / 2 - 10); ctx.lineTo(size / 2, size / 2 + 10); ctx.stroke();
    ctx.restore();
    L.hidden = false;
    const px = e.clientX + 24 + size > innerWidth ? e.clientX - 24 - size : e.clientX + 24;
    L.style.transform = `translate(${px}px, ${Math.max(8, e.clientY - size - 16)}px)`;
    L.style.left = "0"; L.style.top = "0";
  }
  destroy() { this.img.src = ""; $("#loupe").hidden = true; }
}

/* Catmull-Rom through the clicks (the same curve the tracker uses) */
function spline(pts, sub = 8) {
  if (pts.length < 3) return pts;
  const P = [[2 * pts[0][0] - pts[1][0], 2 * pts[0][1] - pts[1][1]], ...pts, [2 * pts.at(-1)[0] - pts.at(-2)[0], 2 * pts.at(-1)[1] - pts.at(-2)[1]]];
  const out = [];
  const d = (a, b) => Math.max(1e-6, Math.hypot(a[0] - b[0], a[1] - b[1])) ** 0.5;
  for (let i = 0; i < pts.length - 1; i++) {
    const [p0, p1, p2, p3] = P.slice(i, i + 4), t0 = 0, t1 = t0 + d(p0, p1), t2 = t1 + d(p1, p2), t3 = t2 + d(p2, p3);
    for (let k = 0; k < sub + (i === pts.length - 2 ? 1 : 0); k++) {
      const t = t1 + (k / sub) * (t2 - t1);
      const L = (a, b, ta, tb) => [((tb - t) / (tb - ta)) * a[0] + ((t - ta) / (tb - ta)) * b[0], ((tb - t) / (tb - ta)) * a[1] + ((t - ta) / (tb - ta)) * b[1]];
      const A1 = L(p0, p1, t0, t1), A2 = L(p1, p2, t1, t2), A3 = L(p2, p3, t2, t3), B1 = L(A1, A2, t0, t2), B2 = L(A2, A3, t1, t3);
      out.push(L(B1, B2, t1, t2));
    }
  }
  return out;
}

function coverageBar(selKey) {
  const p = S.st.project, L = p.track_length;
  if (!(L > 0)) return "";
  const pct = (v) => `${Math.max(0, Math.min(100, (v / L) * 100))}%`;
  const lanes = active().filter((c) => c.s_range).map((c) => `<div class="lane" title="${esc(c.name)}: ${fmtM(c.s_range[0])} to ${fmtM(c.s_range[1])}">
      <div class="seg ${c.key === selKey ? "cur" : ""} ${c.role === "finish" ? "fin" : ""}" style="left:${pct(c.s_range[0])};width:calc(${pct(c.s_range[1])} - ${pct(c.s_range[0])})"></div></div>`).join("");
  const gaps = S.st.coverage.gaps.map(([a, b]) => `<div class="gap" style="left:${pct(a)};width:calc(${pct(b)} - ${pct(a)})" title="Not covered: ${fmtM(a)} to ${fmtM(b)}"></div>`).join("");
  const blind = p.blind_zones.map(([a, b]) => `<div class="blind" style="left:${pct(a)};width:calc(${pct(b)} - ${pct(a)})" title="Tunnel / blind zone ${fmtM(a)} to ${fmtM(b)}"></div>`).join("");
  return `<div class="cover" aria-label="Track coverage">
    <div class="lane" title="Whole track">${blind}${gaps}<div class="flag" style="left:${pct(p.finish_s)}" title="Finish line ${fmtM(p.finish_s)}"></div></div>
    ${lanes}<div class="axis"><span>0 m</span><span>${fmtM(L / 2, 1)}</span><span>${fmtM(L, 1)}</span></div></div>`;
}

/* ================================================================== 3. waypoints */
VIEWS.waypoints = {
  mount(el) {
    this.el = el;
    const act = active();
    if (!act.length) { el.innerHTML = head("Track waypoints", "") + note("warn", "Turn on at least one camera in step 2 first."); return; }
    if (!act.some((c) => c.key === S.sel.wp)) S.sel.wp = (act.find((c) => c.markers.length < 2) || act[0]).key;
    el.innerHTML = head("Track waypoints", "In each camera, click the distance marks along the track in race direction. They tell the tracker how far along the track every pixel is, perspective included.") +
      `<div id="tabs"></div><div class="work"><div id="view"></div><div class="side" id="side"></div></div>
       <div class="card"><h3>Coverage of the whole track</h3><div id="cover"></div><small class="hint">Each bar is one camera. Hatched red stretches are seen by no camera: the tracker treats them like a tunnel.</small></div>`;
    this.keyh = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); api("undo_marker", { key: S.sel.wp }); } };
    addEventListener("keydown", this.keyh);
    this.open();
  },
  cam() { return S.st.cameras.find((c) => c.key === S.sel.wp); },
  open() {
    const cam = this.cam();
    $("#tabs").innerHTML = camTabs(cam.key, "wp");
    $$("#tabs [data-cam]").forEach((b) => (b.onclick = () => { S.sel.wp = b.dataset.cam; this.viewer?.destroy(); this.open(); }));
    this.viewer = new Viewer($("#view"), cam, {
      onClick: (p) => api("add_marker", { key: cam.key, x: p.x, y: p.y }),  // frequent action: no animation
      overlay: (k) => this.overlay(k),
    });
    this.bindDrag();
    this.side();
  },
  overlay(k) {
    const cam = this.cam(), mAll = cam.markers, s = cam.marker_s, bandAll = cam.band || [];
    const stretches = cam.stretches && cam.stretches.length ? cam.stretches : (mAll.length ? [[0, mAll.length - 1]] : []);
    let out = "";
    for (const [a0, a1] of stretches) {  // each stretch of track this camera sees: its own band and curve
      const m = mAll.slice(a0, a1 + 1), band = bandAll.slice(a0, a1 + 1);
      if (m.length >= 2 && band.length === m.length) {  // searched band, perspective-scaled like the tracker's mask
        const left = [], right = [];
        m.forEach((p, i) => {
          const a = m[Math.max(0, i - 1)], b = m[Math.min(m.length - 1, i + 1)], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1;
          const nx = -dy / l, ny = dx / l;
          left.push([p[0] + nx * band[i], p[1] + ny * band[i]]); right.push([p[0] - nx * band[i], p[1] - ny * band[i]]);
        });
        out += `<polygon points="${[...left, ...right.reverse()].map((p) => p.join(",")).join(" ")}" fill="rgba(108,196,216,.16)" stroke="rgba(108,196,216,.9)" stroke-width="${1.2 * k}" stroke-dasharray="${5 * k} ${4 * k}"/>`;
      }
      if (m.length >= 2) out += `<polyline points="${spline(m).map((p) => p.join(",")).join(" ")}" fill="none" stroke="#f2a93b" stroke-width="${2 * k}" stroke-linejoin="round"/>`;
    }
    m.forEach((p, i) => {
      const r = 6 * k;
      out += `<g><circle class="mk" data-i="${i}" cx="${p[0]}" cy="${p[1]}" r="${r}" fill="rgba(242,169,59,.25)" stroke="#f2a93b" stroke-width="${2 * k}"/>
        <circle cx="${p[0]}" cy="${p[1]}" r="${1.4 * k}" fill="#fff" pointer-events="none"/>
        <g class="lbl" data-i="${i}" transform="translate(${p[0] + 9 * k},${p[1] - 9 * k})"><rect x="0" y="${-13 * k}" rx="${4 * k}" width="${(String(s[i]).length * 7 + 22) * k}" height="${18 * k}" fill="rgba(7,9,12,.85)"/>
        <text x="${6 * k}" y="${0}" font-size="${12 * k}" fill="#e8ecf1" font-family="Cascadia Mono, Consolas, monospace">${s[i]} m</text></g></g>`;
    });
    return out;
  },
  bindDrag() {
    const v = this.viewer;
    v.svg.addEventListener("pointerdown", (e) => {
      const mk = e.target.closest(".mk"); if (!mk) return;
      e.preventDefault(); mk.setPointerCapture(e.pointerId);
      const i = +mk.dataset.i, cam = this.cam(); let moved = false;
      v.dragged = false;
      const move = (ev) => {
        const p = v.toImage(ev); if (!p) return;
        moved = true; v.dragged = true;
        cam.markers[i] = [p.x, p.y]; v.draw();
      };
      const up = () => {
        mk.removeEventListener("pointermove", move);
        if (moved) api("move_marker", { key: cam.key, index: i, x: cam.markers[i][0], y: cam.markers[i][1] });
        setTimeout(() => (v.dragged = false), 0);
      };
      mk.addEventListener("pointermove", move);
      mk.addEventListener("pointerup", up, { once: true });
    });
    v.svg.addEventListener("click", (e) => {
      const lb = e.target.closest(".lbl"); if (!lb) return;
      e.stopPropagation();
      const i = +lb.dataset.i, cam = this.cam();
      openPop(lb.getBoundingClientRect(), `<label class="field"><span>Waypoint ${i + 1} is at</span><div class="unit" data-unit="m"><input class="input num" type="number" step="0.01" value="${cam.marker_s[i]}"></div></label>
        <small class="hint">${i === 0 ? "Moving the first waypoint shifts all of them." : "Only this waypoint changes."}</small>
        <div class="row end"><button class="btn sm ghost" data-c>Cancel</button><button class="btn sm primary" data-ok>Save</button></div>`, (pop) => {
        const inp = $("input", pop); inp.select();
        const ok = () => api("set_marker_distance", { key: cam.key, index: i, s: +inp.value }).then(closePop).catch(() => inp.select());
        $("[data-ok]", pop).onclick = ok; $("[data-c]", pop).onclick = closePop;
        inp.onkeydown = (ev) => { if (ev.key === "Enter") ok(); if (ev.key === "Escape") closePop(); };
      });
    });
  },
  side() {
    const cam = this.cam(), p = S.st.project, n = cam.markers.length;
    const pendingBreak = (cam.breaks || []).some((b) => b >= n);
    $("#side").innerHTML = `
      <div class="card stack">
        <h3>${esc(cam.name)}</h3>
        ${n === 0 ? note("info", `Click the centre of the track at the <b>first mark</b> this camera sees, then the next ones in race direction. Tip: rest a marble on each mark and click its centre.`) : ""}
        ${n === 1 ? note("info", "Now the next mark in race direction.") : ""}
        ${pendingBreak ? note("info", "Next click starts a <b>new stretch</b>: the first mark of that other part of the track. Then click its label and enter its distance.") : ""}
        <dl class="kv"><dt>Waypoints</dt><dd>${n}</dd><dt>Covers</dt><dd>${(cam.s_ranges || []).length ? cam.s_ranges.map(([a, b]) => `${fmtM(a)} – ${fmtM(b)}`).join("<br>") : "—"}</dd><dt>Spacing</dt><dd>${fmtM(p.marker_spacing)}</dd>
          <dt title="Track length per image pixel along the track: what one pixel of error costs">Detail</dt><dd>${cam.mm_per_px ? `${cam.mm_per_px[0]}–${cam.mm_per_px[1]} mm per pixel` : "—"}</dd></dl>
        ${cam.mm_per_px && cam.mm_per_px[1] > 6 ? note("warn", `The far end of this view has only one pixel per ${cam.mm_per_px[1]} mm of track. Positions there are less precise: move the camera closer or give the stretch to another camera.`) : ""}
        <label class="field"><span>Track band width</span><input type="range" min="0.5" max="2.5" step="0.05" id="wscale" value="${cam.width_scale}" ${n < 2 ? "disabled" : ""}>
          <small>The blue band is where the tracker looks for marbles. It should cover the channel, walls included.</small></label>
        <div class="row"><button class="btn sm" id="undo" ${n ? "" : "disabled"} title="Ctrl+Z">${I.undo}Undo</button>
          <button class="btn sm" id="stretch" ${n >= 2 && !pendingBreak ? "" : "disabled"} title="This camera also sees another part of the track (the other leg of a hairpin, a pass under a bridge): mark it as a separate stretch, in race order">${I.plus}New stretch</button>
          <button class="btn sm ghost danger" id="clear" ${n ? "" : "disabled"}>Clear all</button>
          ${cam.backend === "demo" ? `<button class="btn sm" id="auto" title="Places the marks an installer would click">${I.wand}Place for me (demo)</button>` : ""}</div>
        <small class="hint">Drag a waypoint to move it. Click its label to change its distance.</small>
      </div>
      ${S.st.coverage.gaps.length ? note("warn", `${plural(S.st.coverage.gaps.length, "stretch", "stretches")} of track ${S.st.coverage.gaps.length === 1 ? "is" : "are"} not covered by any camera. Fine for tunnels; otherwise move a camera or add waypoints.`) : ""}`;
    $("#undo").onclick = () => api("undo_marker", { key: cam.key });
    $("#stretch").onclick = () => api("new_stretch", { key: cam.key });
    $("#clear").onclick = () => { if (confirm(`Remove all ${n} waypoints of ${cam.name}?`)) api("clear_markers", { key: cam.key }); };
    $("#wscale").oninput = (e) => { cam.width_scale = +e.target.value; };
    $("#wscale").onchange = (e) => api("update_camera", { key: cam.key, width_scale: +e.target.value });
    if ($("#auto")) $("#auto").onclick = (e) => busy(e.currentTarget, "Placing…", () => api("autofill_markers", { key: cam.key }));
  },
  update() {
    if (!this.viewer || !this.cam()) return;
    this.viewer.cam = this.cam();
    this.viewer.draw();
    this.side();
    $("#tabs").innerHTML = camTabs(S.sel.wp, "wp");
    $$("#tabs [data-cam]").forEach((b) => (b.onclick = () => { S.sel.wp = b.dataset.cam; this.viewer?.destroy(); this.open(); }));
    $("#cover").innerHTML = coverageBar(S.sel.wp);
  },
  unmount() { removeEventListener("keydown", this.keyh); this.viewer?.destroy(); this.viewer = null; },
};

/* ================================================================== 4. background */
VIEWS.background = {
  mount(el) {
    this.el = el; this.v = Date.now();
    el.innerHTML = head("Empty track", "Each camera stores what the empty track looks like, so marbles stand out. Do this with the race lighting on.") + `
      <div class="card stack">
        ${note("info", "<b>Remove every marble</b> from the track and keep hands and people out of view. Capturing takes about a second.")}
        <div class="row"><button class="btn primary" id="cap">Capture all cameras</button><span class="hint" id="capnote"></span></div>
      </div><div class="bgs" id="bgs"></div>`;
    $("#cap", el).onclick = (e) => this.capture(e.currentTarget);
    this.draw();
  },
  async capture(btn, keys) {
    const r = await busy(btn, "Capturing…", () => api("capture_background", keys ? { keys } : {}));
    this.v = Date.now();
    const res = Object.values(r.results);
    const bad = res.filter((x) => !x.ok).length, moving = res.filter((x) => x.ok && !x.static).length;
    if (!bad && !moving) toast("ok", `Captured ${plural(res.length, "camera")}.`);
    if (bad) toast("bad", `${plural(bad, "camera")} could not be captured. See the cards below.`);
    this.results = { ...(this.results || {}), ...r.results };
    this.draw();
  },
  draw() {
    const R = this.results || {};
    $("#bgs").innerHTML = active().map((c) => {
      const r = R[c.key];
      const state = r && !r.ok ? `<span class="chip bad" title="${esc(r.error)}">Failed</span>` : r && !r.static ? `<span class="chip warn" title="${r.moving}% of the track changed while capturing">Something moved</span>` : c.has_background ? `<span class="chip ok">${I.check}Captured</span>` : `<span class="chip">Not yet</span>`;
      return `<figure>${c.has_background ? `<img alt="Empty track seen by ${esc(c.name)}" src="${img(`background/${encodeURIComponent(c.key)}`, `&v=${this.v}`)}">` : `<div class="thumb"><span class="empty">No capture yet</span></div>`}
        <figcaption><span class="ellipsis" title="${esc(c.name)}">${esc(c.name)}</span><span class="row" style="gap:4px;flex-wrap:nowrap">${state}<button class="btn sm ghost" data-re="${esc(c.key)}" title="Capture ${esc(c.name)} again">Again</button></span></figcaption></figure>`;
    }).join("");
    $$("#bgs [data-re]").forEach((b) => (b.onclick = () => this.capture(b, [b.dataset.re])));
  },
  update() {},
};

/* ================================================================== 5. colours */
VIEWS.colours = {
  mount(el) {
    this.el = el;
    const act = active();
    if (!act.length) { el.innerHTML = head("Marble colours", "") + note("warn", "Turn on at least one camera in step 2 first."); return; }
    if (!act.some((c) => c.key === S.sel.col)) S.sel.col = (act.find((c) => S.st.project.marbles.some((m) => !c.palette[m.id])) || act[0]).key;
    el.innerHTML = head("Marble colours", "Each camera learns every marble's colour under its own light. Pick a marble on the right, then click it in the picture.") +
      `<div id="tabs"></div><div class="work"><div id="view"></div><div class="side" id="side"></div></div>`;
    this.open();
  },
  cam() { return S.st.cameras.find((c) => c.key === S.sel.col); },
  nextMissing(cam) { const m = S.st.project.marbles.find((x) => !cam.palette[x.id]); return m ? m.id : null; },
  open() {
    const cam = this.cam();
    if (!S.sel.marble || !S.st.project.marbles.some((m) => m.id === S.sel.marble)) S.sel.marble = this.nextMissing(cam) || S.st.project.marbles[0]?.id;
    $("#tabs").innerHTML = camTabs(cam.key, "col");
    $$("#tabs [data-cam]").forEach((b) => (b.onclick = () => { S.sel.col = b.dataset.cam; S.sel.marble = null; this.viewer?.destroy(); this.open(); }));
    this.rings = [];
    this.viewer = new Viewer($("#view"), cam, { onClick: (p) => this.sample(p), overlay: (k) => this.overlay(k) });
    this.side();
  },
  overlay(k) {
    return this.rings.map((r) => `<circle class="ring ${r.go ? "go" : ""}" cx="${r.x}" cy="${r.y}" r="${r.r}" fill="none" stroke="${r.color}" stroke-width="${3 * k}"/>`).join("");
  },
  async sample(p) {
    const cam = this.cam(), mid = S.sel.marble;
    if (!mid) { toast("info", "Pick a marble in the list first."); return; }
    if (cam.markers.length < 2) { toast("warn", "Place this camera's waypoints first (step 3)."); return; }
    const m = S.st.project.marbles.find((x) => x.id === mid);
    try {
      const r = await api("sample_colour", { key: cam.key, marble_id: mid, x: p.x, y: p.y });
      // feedback: a ring around the blob that was learned, fading out (rare enough per marble to deserve it)
      const ring = { x: r.centre[0], y: r.centre[1], r: r.radius * 1.35, color: m.color, go: false };
      this.rings.push(ring); this.viewer.draw();
      requestAnimationFrame(() => requestAnimationFrame(() => { ring.go = true; this.viewer.draw(); }));
      setTimeout(() => { this.rings = this.rings.filter((x) => x !== ring); this.viewer?.draw(); }, 450);
      if (r.ignored_clicks) toast("warn", `${r.ignored_clicks === 1 ? "One click" : `${r.ignored_clicks} clicks`} of ${m.name} looked different from the others and ${r.ignored_clicks === 1 ? "is" : "are"} left out (part of the marble may blend into the track there). Click ${m.name} where it stands out clearly.`, 7000);
      else if (r.issues.length) toast("warn", `${m.name} looks like ${r.issues.map((i) => i.other).join(", ")} to this camera. Click ${m.name} again in another spot, or see the advice on the right.`, 7000);
      const next = this.nextMissing(this.cam());
      if (next) S.sel.marble = next;
      this.side();
    } catch { /* toast shown */ }
  },
  side() {
    const cam = this.cam(), p = S.st.project, ms = p.marbles;
    const done = ms.filter((m) => cam.palette[m.id]).length;
    const others = active().filter((c) => c.key !== cam.key && ms.every((m) => c.palette[m.id]));
    const name = (id) => ms.find((m) => m.id === id)?.name || id;
    $("#side").innerHTML = `
      <div class="card stack">
        <h3 style="margin:0">${done} of ${plural(ms.length, "marble")} learned</h3>
        ${cam.backend === "demo" ? `<button class="btn sm" id="auto" style="justify-self:start">${I.wand}Sample for me (demo)</button>` : ""}
        ${cam.has_background ? "" : note("warn", "Capture the empty track first (step 4): clicks work much better with it.")}
        ${S.st.demo && cam.backend === "demo" ? note("info", "Demo: the marbles are parked for you. A short view shows them in groups that take turns.") : note("info", "Put the marbles on the track where this camera sees them, not touching each other.")}
        <div class="picker" role="listbox" aria-label="Marbles">${ms.map((m) => {
          const s = cam.palette[m.id];
          const st = !s ? `<span class="chip">To do</span>` : s.copied_from ? `<span class="chip warn" title="Copied from another camera">Copied</span>` : `<span class="chip ok">${I.check}${s.clicks > 1 ? s.clicks + "×" : ""}</span>`;
          return `<button class="pick ${m.id === S.sel.marble ? "on" : ""}" role="option" aria-selected="${m.id === S.sel.marble}" data-m="${esc(m.id)}"><span class="swatch" style="background:${esc(m.color)}"></span><span class="ellipsis" title="${esc(m.name)}">${esc(m.name)}</span>${st}</button>`;
        }).join("")}</div>
        ${S.sel.marble && cam.palette[S.sel.marble] ? `<button class="btn sm ghost danger" id="forget">Forget ${esc(name(S.sel.marble))} on this camera</button>` : ""}
      </div>
      ${cam.palette_issues.length ? note("warn", `<b>Colours this camera could confuse:</b> ${cam.palette_issues.slice(0, 4).map((i) => `${esc(name(i.a))} & ${esc(name(i.b))}`).join(", ")}.
        Click those marbles once more in another spot. If it stays, change the lighting or swap one marble for a clearly different colour.`) : done === ms.length ? note("ok", "Every marble is clearly distinguishable on this camera.") : ""}
      ${others.length && done < ms.length ? `<div class="card stack"><label class="field"><span>Can't place every marble in view?</span>
        <select class="input" id="copysrc"><option value="">Copy missing colours from…</option>${others.map((c) => `<option value="${esc(c.key)}">${esc(c.name)}</option>`).join("")}</select>
        <small>A fallback: lighting differs between cameras, so samples taken here are more reliable.</small></label></div>` : ""}`;
    $$("#side [data-m]").forEach((b) => (b.onclick = () => { S.sel.marble = b.dataset.m; this.side(); }));
    if ($("#forget")) $("#forget").onclick = () => api("clear_colour", { key: cam.key, marble_id: S.sel.marble });
    if ($("#copysrc")) $("#copysrc").onchange = (e) => e.target.value && api("copy_colours", { src: e.target.value, dst: cam.key });
    if ($("#auto")) $("#auto").onclick = (e) => busy(e.currentTarget, "Sampling…", () => api("autofill_colours", { key: cam.key }));
  },
  update() {
    if (!this.viewer || !this.cam()) return;
    this.viewer.cam = this.cam();
    $("#tabs").innerHTML = camTabs(S.sel.col, "col");
    $$("#tabs [data-cam]").forEach((b) => (b.onclick = () => { S.sel.col = b.dataset.cam; S.sel.marble = null; this.viewer?.destroy(); this.open(); }));
    this.side();
  },
  unmount() { this.viewer?.destroy(); this.viewer = null; },
};

/* ================================================================== 6. server */
VIEWS.server = {
  mount(el) {
    const s = S.st.project.server;
    const v = S.st.project.vmix;
    el.innerHTML = head("Leaderboard output", "MarbleTrack fills a vMix title with the live leaderboard, and can also send it to your backend over a WebSocket, about ten times a second, plus an event for every start, finish and camera problem.") + `
      <div class="card stack">
        <label class="toggle"><input type="checkbox" id="ven" ${v.enabled ? "checked" : ""}> Fill a vMix title</label>
        <div class="grid2" id="vform">
          <label class="field"><span>vMix computer</span><input class="input" id="vhost" value="${esc(v.host)}" spellcheck="false"><small>127.0.0.1 when vMix runs on this PC. Uses the vMix TCP API (port ${esc(v.port)}).</small></label>
          <label class="field"><span>Title input</span><input class="input" id="vinput" value="${esc(v.input)}" spellcheck="false"><small>The title's name or number in vMix.</small></label>
          <label class="field"><span>Name field</span><input class="input" id="vname" value="${esc(v.name_field)}" spellcheck="false"><small>{n} is the row: P1Name.Text, P2Name.Text, …</small></label>
          <label class="field"><span>Time / gap field</span><input class="input" id="vinfo" value="${esc(v.info_field)}" spellcheck="false"><small>Finish time, or the gap to the leader while racing.</small></label>
          <label class="field"><span>Colour swatch</span><input class="input" id="vcolor" value="${esc(v.color_field)}" spellcheck="false" placeholder="e.g. P{n}Color.Fill.Color"><small>A shape filled with the marble's colour. Optional.</small></label>
          <label class="field"><span>Clock and status fields</span><div class="row" style="flex-wrap:nowrap"><input class="input" id="vclock" value="${esc(v.clock_field)}" spellcheck="false"><input class="input" id="vstatus" value="${esc(v.status_field)}" spellcheck="false"></div><small>Race clock; READY / LIVE / FINAL. Leave empty to skip.</small></label>
          ${num("vrows", "Rows in the title", v.rows, "rows", "1", "0 = one row per marble.")}
          <label class="field"><span>Lap field</span><input class="input" id="vlap" value="${esc(v.lap_field || "")}" spellcheck="false" placeholder="e.g. Lap.Text"><small>Lap races: LAP 2/3, then FINAL LAP. Optional.</small></label>
        </div>
        <div class="row"><button class="btn" id="vtest">Test vMix</button><span id="vres" class="hint" aria-live="polite"></span></div>
      </div>` + `
      <div class="card stack">
        <label class="toggle"><input type="checkbox" id="sen" ${s.enabled ? "checked" : ""}> Stream to a server</label>
        <div class="grid2" id="sform">
          <label class="field" style="grid-column:1/-1"><span>Server address</span><input class="input" id="surl" placeholder="wss://example.com/marbles" value="${esc(s.url)}" spellcheck="false" autocomplete="off"><small>Starts with ws:// or wss:// (secure).</small></label>
          <label class="field"><span>Access token</span><div class="row" style="flex-wrap:nowrap"><input class="input" id="stok" type="password" value="${esc(s.token)}" autocomplete="off" spellcheck="false" placeholder="Optional"><button class="btn sm ghost" id="show" type="button">Show</button></div><small>Sent as <code>Authorization: Bearer …</code></small></label>
          <label class="field"><span>Station name</span><input class="input" id="sid" maxlength="60" value="${esc(s.station_id)}"><small>Tells your backend which track this is.</small></label>
          ${num("shz", "Updates per second", s.publish_hz, "/s", "1")}
        </div>
        <div class="row"><button class="btn" id="test">Test connection</button><span id="tres" class="hint" aria-live="polite"></span></div>
      </div>
      <div class="card stack">
        <label class="toggle"><input type="checkbox" id="ren" ${S.st.project.record.enabled ? "checked" : ""}> Record every camera while live</label>
        <small class="hint">For checking the tracker on real races: every frame of every camera, the leaderboards that went out and all events are saved, and <code>python -m marbletrack_app.replay &lt;folder&gt;</code> runs the tracker again on them. About 0.5–1 GB per camera per minute.</small>
        <label class="field"><span>Recordings folder</span><input class="input" id="rdir" value="${esc(S.st.project.record.folder)}" placeholder="recordings, inside this map's folder" spellcheck="false"></label>
      </div>
      <details class="more card"><summary>${I.chev}What gets sent</summary><div><pre class="hint" style="white-space:pre-wrap;font:12px var(--mono);margin:0">{"schema":"marbletrack.v1","type":"leaderboard","station_id":"track-1","seq":412,
 "sent_at":"2026-10-14T18:03:22.481Z","race_id":"3f2a…","race_state":"running","race_time":12.418,
 "board":[{"rank":1,"marble_id":"ruby","name":"Ruby","color":"#d72a2d","status":"tracking",
           "progress_m":9.214,"gap_m":0.0,"speed_mps":1.42,"finish_time":null,"photo_finish":false}, …],
 "cameras":{"opencv:0":true, …}}

{"schema":"marbletrack.v1","type":"race_event","event":"finish","race_id":"3f2a…",
 "marble_id":"ruby","name":"Ruby","rank":1,"time":12.4183, …}</pre></div></details>`;
    const setv = (k, val) => api("update_vmix", { [k]: val });
    $("#ven", el).onchange = (e) => { setv("enabled", e.target.checked); $("#vform").style.opacity = e.target.checked ? 1 : 0.5; };
    $("#vform").style.opacity = v.enabled ? 1 : 0.5;
    [["vhost", "host"], ["vinput", "input"], ["vname", "name_field"], ["vinfo", "info_field"], ["vcolor", "color_field"],
     ["vclock", "clock_field"], ["vstatus", "status_field"], ["vlap", "lap_field"]].forEach(([id, k]) => { $(`#${id}`, el).onchange = (e) => setv(k, e.target.value); });
    $("#vrows", el).onchange = (e) => setv("rows", +e.target.value || 0);
    $("#vtest", el).onclick = (e) => busy(e.currentTarget, "Connecting…", async () => {
      document.activeElement?.blur();
      await new Promise((r) => setTimeout(r, 50));
      const r = await api("test_vmix", {}, { mutates: false });
      $("#vres").innerHTML = r.ok ? `<span class="chip ok">${I.check}${esc(r.detail)}</span>` : `<span class="chip bad">Failed</span> ${esc(r.detail)}`;
    });
    $("#ren", el).onchange = (e) => api("update_record", { enabled: e.target.checked });
    $("#rdir", el).onchange = (e) => api("update_record", { folder: e.target.value });
    const set = (k, v) => api("update_server", { [k]: v });
    $("#sen", el).onchange = (e) => { set("enabled", e.target.checked); $("#sform").style.opacity = e.target.checked ? 1 : 0.5; };
    $("#sform").style.opacity = s.enabled ? 1 : 0.5;
    $("#surl", el).onchange = (e) => set("url", e.target.value);
    $("#stok", el).onchange = (e) => set("token", e.target.value);
    $("#sid", el).onchange = (e) => set("station_id", e.target.value);
    $("#shz", el).onchange = (e) => set("publish_hz", +e.target.value || 10);
    $("#show", el).onclick = (e) => { const t = $("#stok"); t.type = t.type === "password" ? "text" : "password"; e.currentTarget.textContent = t.type === "password" ? "Show" : "Hide"; };
    $("#test", el).onclick = (e) => busy(e.currentTarget, "Connecting…", async () => {
      $("#surl").blur(); $("#stok").blur();
      await new Promise((r) => setTimeout(r, 50));
      const r = await api("test_server", {}, { mutates: false });
      $("#tres").innerHTML = r.ok ? `<span class="chip ok">${I.check}${esc(r.detail)}</span>` : `<span class="chip bad">Failed</span> ${esc(r.detail)}`;
    });
  },
  update() {},
};

/* ================================================================== 7. review */
VIEWS.review = {
  mount(el) { this.el = el; this.draw(); },
  draw() {
    const st = S.st, p = st.project;
    const rows = STEPS.slice(0, -1).map((s) => {
      const iss = st.steps[s.id];
      return `<div class="check"><span style="color:${iss.length ? "var(--warn)" : "var(--ok)"}">${iss.length ? I.alert : I.ok}</span>
        <div style="min-width:0"><b>${s.t}</b><div class="hint ellipsis" title="${esc(iss.join("\n"))}">${iss.length ? esc(iss[0]) + (iss.length > 1 ? ` (+${iss.length - 1} more)` : "") : esc(stepSummary(s.id))}</div></div>
        ${iss.length ? `<button class="btn sm" data-step="${s.id}">Fix</button>` : ""}</div>`;
    }).join("");
    const gaps = st.coverage.gaps;
    const fin = active().some((c) => c.role === "finish");
    this.el.innerHTML = head("Review & go live", "Check everything, then go live. You can come back to setup at any time.") + `
      <div class="checklist">${rows}</div>
      ${viewCard()}
      ${!fin ? note("info", "No finish-line camera. Finish times then come from the section camera at the line (a few ms resolution instead of under one).") : ""}
      ${gaps.length ? note("warn", `Not covered by any camera: ${gaps.map(([a, b]) => `${a}–${b} m`).join(", ")}. The tracker holds positions there.`) : ""}
      <div class="card"><h3>Coverage</h3>${coverageBar(null)}</div>`;
    bindViewCard(this.el);
  },
  update() { this.draw(); },
};

/* ================================================================== live */
let es = null, focusKey = null, lastOrder = [];
function renderLive() {
  const live = $("#live");
  if (!live.dataset.mounted) {
    live.dataset.mounted = "1";
    const cams = active();
    focusKey = focusKey && cams.some((c) => c.key === focusKey) ? focusKey : cams[0]?.key;
    live.innerHTML = `
      <div class="wall"><div class="tiles" id="tiles">${cams.map((c) => `<button class="tile ${c.key === focusKey ? "on" : ""}" data-k="${esc(c.key)}" title="${esc(c.name)}">
          <img alt="" data-snap="${esc(c.key)}"><span class="t"><span class="led"></span><span class="ellipsis">${esc(c.name)}</span></span></button>`).join("")}</div>
        <div class="focus" id="focus"><img alt="Focused camera"><span class="cap"></span></div></div>
      <aside class="board"><div class="board-head"><div class="race-state"><span id="rstate" class="chip">Waiting</span>
          <div class="row"><button class="btn sm" id="newrace" title="Clear the board and wait for the next race">New race</button><button class="btn sm ghost" id="stop">Back to setup</button></div></div>
          <div class="clock" id="clock">0.000</div></div><div class="rows" id="rows"></div></aside>
      <div class="status-bar">
        <div class="sbox"><span class="k">Cameras</span><span class="v" id="camsum"></span><span class="d" id="camdet"></span></div>
        <div class="sbox"><span class="k">Leaderboard server</span><span class="v" id="srv"></span><span class="d" id="srvdet"></span></div>
        <div class="sbox"><span class="k">vMix</span><span class="v" id="vmx"></span><span class="d" id="vmxdet"></span></div>
        <div class="sbox"><span class="k">Race control</span><div class="log" id="log"></div></div>
      </div>`;
    $("#tiles").onclick = (e) => { const t = e.target.closest(".tile"); if (t) setFocus(t.dataset.k); };
    $("#newrace").onclick = (e) => busy(e.currentTarget, "…", () => api("new_race", {}, { mutates: false }));
    $("#stop").onclick = async (e) => {
      if (S.live?.race?.state === "running" && !confirm("A race is running. Stop tracking and go back to setup?")) return;
      await busy(e.currentTarget, "Stopping…", () => api("stop_live", {}, { mutates: false }));
      live.dataset.mounted = ""; live.innerHTML = "";
    };
    startSnapshots($("#tiles"), 240, 250);
    setFocus(focusKey);
  }
  if (!es) {
    es = new EventSource(img("live"));
    es.onmessage = (ev) => { const d = JSON.parse(ev.data); (d.toasts || []).forEach((t) => toast(t.kind, t.text)); if (d.mode === "live") { S.live = d; paintLive(d); } };
  }
}
function setFocus(key) {
  focusKey = key;
  $$("#tiles .tile").forEach((t) => t.classList.toggle("on", t.dataset.k === key));
  const cam = S.st.cameras.find((c) => c.key === key);
  $("#focus img").src = key ? img(`stream/${encodeURIComponent(key)}`) : "";
  $("#focus .cap").textContent = cam ? cam.name : "";
  api("focus_camera", { key }, { mutates: false, quiet: true }).catch(() => {});
}
function stopLive() {
  if (es) { es.close(); es = null; }
  const live = $("#live"); if (live.dataset.mounted) { live.dataset.mounted = ""; live.innerHTML = ""; }
}
const STATE_TXT = { waiting: ["Waiting for marbles", ""], running: ["Racing", "acc"], complete: ["Finished", "ok"] };
function paintLive(d) {
  const r = d.race || {}, board = d.board || [];
  const [txt, cls] = STATE_TXT[r.state] || ["—", ""];
  const rs = $("#rstate"); if (!rs) return;
  rs.textContent = txt; rs.className = `chip ${cls}`;
  rs.title = r.late ? "Tracking started after this race had begun: the clock and finish times are estimates." : "";
  if (r.late) rs.textContent += " · joined late";
  $("#clock").textContent = r.clock == null ? "0.000" : r.clock.toFixed(3);
  // leaderboard with FLIP reordering: a pass slides the two rows (state indication), 200 ms
  const rows = $("#rows"), before = new Map($$(".lrow", rows).map((el) => [el.dataset.id, el.getBoundingClientRect().top]));
  const leader = board.find((e) => e.status !== "finished");
  rows.innerHTML = board.map((e) => {
    const fin = e.finish_time != null, hid = e.status === "coasting" || e.status === "lost" || e.status === "unseen";
    const val = fin ? fmtS(e.finish_time) : e.status === "unseen" ? "not seen yet" : e === leader ? fmtM(e.progress_m, 1) : `+${(e.gap_m).toFixed(2)} m`;
    const laps = S.st.project.laps || 1;
    const lapTxt = laps > 1 && !fin && e.status !== "unseen" ? `Lap ${e.lap || 1}/${laps} · ` : "";
    const sub = lapTxt + (e.photo_finish ? "photo finish: review" : fin ? `#${e.rank}` : e.status === "coasting" ? "hidden" : e.status === "lost" ? "lost" : e.status === "acquiring" ? "new" : `${e.speed_mps.toFixed(2)} m/s`);
    return `<div class="lrow ${fin ? "fin" : ""} ${hid ? "hid" : ""} ${e.photo_finish ? "photo" : ""}" data-id="${esc(e.marble_id)}">
      <span class="rk">${e.rank}</span><span class="swatch" style="background:${esc(e.color)}"></span><span class="nm" title="${esc(e.name)}">${esc(e.name)}</span>
      <span class="val">${esc(val)}<small>${esc(sub)}</small></span></div>`;
  }).join("");
  if (!reduceMotion) $$(".lrow", rows).forEach((el) => {
    const y0 = before.get(el.dataset.id); if (y0 == null) return;
    const dy = y0 - el.getBoundingClientRect().top; if (Math.abs(dy) < 1) return;
    el.animate([{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }], { duration: 200, easing: "cubic-bezier(0.77, 0, 0.175, 1)" });
  });
  // cameras
  const cs = Object.entries(d.cameras || {});
  const up = cs.filter(([, c]) => c.online && !c.error).length;
  const errs = cs.filter(([, c]) => c.error);
  $("#camsum").innerHTML = `<span class="led ${up === cs.length ? "ok" : up ? "warn" : "bad"}"></span>${up} of ${cs.length} online`;
  const fps = cs.map(([, c]) => c.fps).filter((x) => x > 0);
  $("#camdet").textContent = errs.length ? `${S.st.cameras.find((c) => c.key === errs[0][0])?.name || errs[0][0]}: ${errs[0][1].error}` : fps.length ? `${Math.round(Math.min(...fps))}–${Math.round(Math.max(...fps))} fps · ${Math.round(Math.max(...cs.map(([, c]) => c.proc_ms || 0)))} ms per frame (slowest)` : "starting…";
  $$("#tiles .tile").forEach((t) => { const c = d.cameras?.[t.dataset.k]; t.classList.toggle("down", !!c && (!c.online || !!c.error)); });
  // server
  const s = d.server || {};
  const led = { connected: "ok", connecting: "warn", reconnecting: "warn", error: "bad", off: "", stopped: "" }[s.state] ?? "";
  $("#srv").innerHTML = `<span class="led ${led}"></span>${esc({ connected: "Connected", connecting: "Connecting…", reconnecting: "Reconnecting…", error: "Not connected", off: "Off", stopped: "Stopped" }[s.state] || s.state || "—")}`;
  $("#srvdet").textContent = s.state === "connected" ? `${(s.sent || 0).toLocaleString()} messages sent` : s.detail || "";
  $("#srvdet").title = s.detail || "";
  const vm = d.vmix || {};
  $("#vmx").innerHTML = `<span class="led ${{ connected: vm.errors ? "warn" : "ok", connecting: "warn", reconnecting: "warn" }[vm.state] ?? ""}"></span>${esc({ connected: "Connected", connecting: "Connecting…", reconnecting: "Reconnecting…", off: "Off", stopped: "Stopped" }[vm.state] || vm.state || "—")}`;
  $("#vmxdet").textContent = vm.state === "connected" ? (vm.errors ? `${vm.errors} rejected · ${vm.last_error}` : `${(vm.sent || 0).toLocaleString()} field updates`) : vm.detail || "";
  $("#vmxdet").title = vm.last_error || vm.detail || "";
  // race control log
  const KIND = { finish: (e) => [`${e.name} finishes P${e.rank} · ${fmtS(e.time)}`, "acc"], race_started: () => ["Race started", "ok"], race_complete: () => ["Race complete", "ok"],
    new_race: (e) => [`New race (${e.reason})`, ""], camera_offline: (e) => [`${e.name} stopped sending frames`, "bad"], camera_online: (e) => [`${e.name} back online`, "ok"],
    camera_error: (e) => [`${e.name}: ${e.error}`, "bad"], camera_started: (e) => [`${e.name} started`, ""], tracking_started: (e) => [`Tracking started on ${plural(e.cameras, "camera")}`, "ok"] };
  $("#log").innerHTML = (d.log || []).slice(0, 30).map((e) => {
    const f = KIND[e.kind]; if (!f) return "";
    const [t, c] = f(e); const time = new Date(e.t * 1000).toLocaleTimeString();
    return `<div class="${c}"><span style="color:var(--faint)">${time}</span> ${esc(t)}</div>`;
  }).join("");
}

/* ------------------------------------------------------------------ popover (origin-aware: grows from its anchor) */
function openPop(rect, html, init) {
  const pop = $("#pop");
  pop.innerHTML = html; pop.hidden = false;
  const w = 220, h = pop.offsetHeight;
  const left = Math.min(innerWidth - w - 12, rect.left), below = rect.bottom + 8 + h < innerHeight;
  pop.style.left = `${Math.max(12, left)}px`; pop.style.top = `${below ? rect.bottom + 8 : rect.top - h - 8}px`;
  pop.style.setProperty("--origin", `${below ? "top" : "bottom"} left`);
  pop.classList.add("enter"); requestAnimationFrame(() => requestAnimationFrame(() => pop.classList.remove("enter")));
  init(pop);
  setTimeout(() => addEventListener("pointerdown", outside), 0);
}
function outside(e) { if (!e.target.closest("#pop")) closePop(); }
function closePop() { $("#pop").hidden = true; removeEventListener("pointerdown", outside); }

/* ------------------------------------------------------------------ setup-time events (toasts, camera fps) */
let setupES = null;
function startSetupEvents() {
  if (setupES) return;
  setupES = new EventSource(img("live"));
  setupES.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    (d.toasts || []).forEach((t) => toast(t.kind, t.text));
    if (d.mode === "live" && S.st?.mode !== "live") loadState();
  };
  setupES.onerror = () => {};
}

window.addEventListener("resize", () => current?.viewer?.draw());
loadState().then(() => { startSetupEvents(); if (S.st.cameras.length === 0 && S.st.step === "race" && S.st.demo) { /* first run with demo */ } });

/* ================================================================== camera views vs the saved map */
const VIEW = { same: ["ok", "Matches the map"], moved: ["warn", "Moved"], changed: ["bad", "Different view"], size: ["bad", "Different resolution"],
  realigned: ["warn", "Waypoints carried over"], redo: ["warn", "Redo"], error: ["bad", "No picture"] };
function viewCard() {
  const cams = active().filter((c) => c.has_background);
  if (!cams.length) return "";
  const checked = cams.filter((c) => c.view);
  const moved = cams.filter((c) => c.view?.status === "moved");
  const rows = checked.map((c) => {
    const [k, t] = VIEW[c.view.status] || ["", c.view.status];
    return `<div class="check" style="grid-template-columns:auto minmax(0,1fr) auto"><span class="chip ${k}">${esc(t)}</span><div style="min-width:0"><b>${esc(c.name)}</b><div class="hint ellipsis">${esc(c.view.detail || "")}</div></div>
      ${c.view.status === "moved" ? `<button class="btn sm" data-realign="${esc(c.key)}">Carry waypoints over</button>`
        : ["changed", "size", "redo"].includes(c.view.status) ? `<button class="btn sm" data-step="waypoints">Redo waypoints</button>`
        : c.view.status === "realigned" ? `<button class="btn sm" data-step="background">Capture empty track</button>` : ""}</div>`;
  }).join("");
  return `<div class="card stack"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Camera views</h3>
      <div class="row">${moved.length > 1 ? `<button class="btn sm" id="realignAll">Carry all over (${moved.length})</button>` : ""}<button class="btn sm" id="checkViews">${I.search}Check camera views</button></div></div>
      <p class="hint" style="margin:0">Compares what every camera sees now with the empty track saved in this map. Do this whenever you reopen a map: cameras may have been moved since.</p>
      ${rows ? `<div class="checklist">${rows}</div>` : ""}</div>`;
}
function bindViewCard(el) {
  const b = $("#checkViews", el);
  if (b) b.onclick = (e) => busy(e.currentTarget, "Comparing…", () => api("check_views", {}, { mutates: false }));
  const all = $("#realignAll", el);
  if (all) all.onclick = (e) => busy(e.currentTarget, "…", () => api("realign_cameras", {}));
  $$("[data-realign]", el).forEach((x) => { x.onclick = (e) => busy(e.currentTarget, "…", () => api("realign_cameras", { keys: [x.dataset.realign] })); });
}

/* ================================================================== maps (one saved map per route) */
const fmtDate = (iso) => { if (!iso) return "never saved"; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(); };
async function openMaps() {
  if (S.st.mode !== "setup") return;
  if (S.dirty) await autosave();
  const back = $("#sheet"), sheet = $(".sheet", back);
  const draw = async () => {
    const { maps, current } = await api("list_maps", {}, { mutates: false });
    const p = S.st.project;
    sheet.innerHTML = `
      <div class="head"><h2 id="sheetTitle">Maps</h2><button class="btn sm ghost" id="mclose" aria-label="Close">${I.x}</button></div>
      <p class="hint" style="margin:0">One map per route: cameras, waypoints, empty-track images and colours. Every map is kept, so an old route can be reopened later. When you reopen one, check the camera views on the Review step.</p>
      <div class="card stack"><h3>This map</h3>
        <div class="grid2">
          <label class="field"><span>Name</span><input class="input" id="mname" maxlength="80" value="${esc(p.name)}"></label>
          <label class="field"><span>Saved</span><input class="input" value="${esc(fmtDate(p.saved_at))}" disabled></label>
          <label class="field" style="grid-column:1/-1"><span>Notes</span><textarea class="input" id="mnotes" maxlength="2000" placeholder="Which draw or route this is, what changed…">${esc(p.notes || "")}</textarea></label>
        </div>
        <div class="row"><button class="btn sm" id="mexport">Export as .zip</button><span class="hint" id="mexres"></span></div>
      </div>
      <div class="card stack"><h3>New route</h3>
        <div class="row" style="flex-wrap:nowrap"><input class="input" id="nname" maxlength="80" placeholder="Name of the new route"><button class="btn primary" id="nempty">Start new route</button></div>
        <small class="hint">Keeps the marbles, cameras and outputs; waypoints, empty track and colours are done again. To start from an existing route instead, use <b>Copy as new route</b> below.</small>
      </div>
      <div class="stack"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Saved maps (${maps.length})</h3><button class="btn sm" id="mimport">Import .zip</button></div>
        <div class="maplist">${maps.map((m) => `<div class="maprow ${m.folder === current ? "cur" : ""}">
          <div style="min-width:0"><b class="ellipsis" style="display:block">${esc(m.name)}${m.folder === current ? ' <span class="chip acc">open</span>' : ""}</b>
            <div class="meta" title="${esc(m.notes)}">${esc(fmtDate(m.saved_at))} · ${plural(m.cameras, "camera")} (${m.cameras_set_up} set up) · ${m.track_length ? fmtM(m.track_length, 1) : "length not set"}${m.notes ? " · " + esc(m.notes) : ""}</div></div>
          <div class="acts">${m.folder === current ? "" : `<button class="btn sm" data-open="${esc(m.folder)}">Open</button>`}
            <button class="btn sm ghost" data-copy="${esc(m.folder)}" data-name="${esc(m.name)}">Copy as new route</button>
            <button class="btn sm ghost" data-export="${esc(m.folder)}">Export</button></div></div>`).join("") || '<p class="hint">No saved maps yet.</p>'}</div></div>`;
    $("#mclose").onclick = closeMaps;
    $("#mname").onchange = (e) => api("update_race", { name: e.target.value });
    $("#mnotes").onchange = (e) => api("update_race", { notes: e.target.value });
    const exported = (r) => { $("#mexres").textContent = `Saved to ${r.path}`; toast("ok", `Exported to ${r.path}`, 8000); };
    $("#mexport").onclick = (e) => busy(e.currentTarget, "Exporting…", async () => exported(await api("export_map", {}, { mutates: false })));
    $("#nempty").onclick = (e) => {
      const name = $("#nname").value.trim();
      if (!name) { $("#nname").focus(); toast("warn", "Give the new route a name first."); return; }
      busy(e.currentTarget, "Creating…", async () => { await api("new_map", { name }, { mutates: false }); closeMaps(); });
    };
    $$("[data-open]", sheet).forEach((b) => { b.onclick = (e) => busy(e.currentTarget, "Opening…", async () => { await api("open_map", { folder: b.dataset.open }, { mutates: false }); closeMaps(); afterOpen(); }); });
    $$("[data-copy]", sheet).forEach((b) => { b.onclick = (e) => {
      const name = prompt("Name of the new route (a copy of this map):", `${b.dataset.name} (copy)`);
      if (!name) return;
      busy(e.currentTarget, "Copying…", async () => { await api("new_map", { name, from_folder: b.dataset.copy }, { mutates: false }); closeMaps(); afterOpen(); });
    }; });
    $$("[data-export]", sheet).forEach((b) => { b.onclick = (e) => busy(e.currentTarget, "…", async () => { const r = await api("export_map", { folder: b.dataset.export }, { mutates: false }); toast("ok", `Exported to ${r.path}`, 8000); }); });
    $("#mimport").onclick = () => $("#importFile").click();
  };
  await draw();
  back.hidden = false; back.classList.add("enter");
  requestAnimationFrame(() => requestAnimationFrame(() => back.classList.remove("enter")));
  $("#mclose").focus();
}
function closeMaps() { $("#sheet").hidden = true; }
async function afterOpen() {
  // a reopened map may no longer fit the cameras: compare right away
  if (!active().some((c) => c.has_background)) return;
  toast("info", "Comparing every camera with the saved map…");
  try { await api("check_views", {}, { mutates: false }); } catch { /* toast shown */ }
}
$("#mapsBtn").onclick = openMaps;
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeMaps(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#sheet").hidden) closeMaps(); });
$("#importFile").onchange = async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  try {
    const res = await fetch("/api/import_map", { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/zip" }, body: await f.arrayBuffer() });
    const data = await res.json();
    if (!res.ok) { toast("bad", data.error || "Import failed"); return; }
    setState(data, false); closeMaps(); toast("ok", `Imported ${data.project.name}`); afterOpen();
  } catch { toast("bad", "Import failed"); }
};
