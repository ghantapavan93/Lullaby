// Proof Layer — client. Every number on screen comes from the API; the client
// never computes a fact the server owns. Screens keep the revision they
// displayed until the person asks for the newer one.

const TZ = "America/New_York";
const VIEWERS = [
  { id: "mem_parent_a", short: "Parent A" },
  { id: "mem_caregiver_b", short: "Caregiver B" },
];
// Purchase-to-access is the primary journey; the night briefing is the family
// evidence lab. Old links (#/briefing, #/changes, #/features) keep working.
const ROUTES = [
  { id: "home", label: "Home", short: "Home", icon: "home", group: "family" },
  { id: "access", label: "Access journey", short: "Access", icon: "link", group: "family" },
  { id: "recorded", label: "Recorded evidence", short: "Video", icon: "camera", group: "family" },
  { id: "briefing", label: "Night briefing", short: "Night", icon: "moon", group: "evidence" },
  { id: "changes", label: "What changed", short: "Changes", icon: "compare", group: "evidence" },
  { id: "features", label: "Plans & features", short: "Features", icon: "key", group: "evidence" },
  { id: "studio", label: "Scenario Studio", short: "Studio", icon: "sliders", group: "engineering" },
  { id: "growth", label: "Growth Lab", short: "Growth", icon: "chart", group: "engineering" },
  { id: "customers", label: "Customer evidence", short: "Customers", icon: "user", group: "family" },
];
const TAB_ROUTES = ["home", "access", "recorded", "briefing"];
const EVIDENCE_ROUTES = ["briefing", "changes", "features"];
const FACTS = ["recorded_sleep", "sleep_onset", "final_wake", "observation_coverage", "unobserved"];

const ICONS = {
  moon: '<path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z"/>',
  compare: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5V15a3 3 0 0 0 3 3h6.5M18 15.5V9a3 3 0 0 0-3-3H8.5"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l2 2M14 9l2 2"/>',
  scan: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16"/>',
  camera: '<rect x="3" y="7" width="13" height="10" rx="2"/><path d="M16 11l5-3v8l-5-3"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  unknown: '<circle cx="12" cy="12" r="9" stroke-dasharray="3 3"/><path d="M9.5 9.5a2.5 2.5 0 0 1 4.6 1.3c0 1.7-2.1 2-2.1 3.2M12 17h.01"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  xoct: '<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8z"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  device: '<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M10 7h4M12 17h.01"/>',
  card: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18M7 15h3"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14-5l-2 2M4 4v4h4M4 13a8 8 0 0 0 14 5l2-2M20 20v-4h-4"/>',
  sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  minus: '<path d="M6 12h12"/>',
  pause: '<path d="M9 6v12M15 6v12"/>',
  play: '<path d="M8 5l11 7-11 7z"/>',
  next: '<path d="M6 5l9 7-9 7zM18 5v14"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  home: '<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  chart: '<path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6"/>',
};
const icon = (name, cls = "") => `<svg class="icon ${cls}" aria-hidden="true" viewBox="0 0 24 24">${ICONS[name]}</svg>`;
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const state = {
  tokens: {},
  viewer: VIEWERS[0].id,
  route: "home",
  twoScreens: false,
  windowId: null,
  screens: {},          // memberId -> { brief, previous, ack, highlight }
  latest: null,         // latest brief (polled), for staleness only
  changes: null,
  features: [],
  feature: "developmental_milestones",
  device: null,
  preview: null,
  commercial: null,
  decision: null,
  decisionNote: null,
  purchase: null,
  purchaseKey: null,
  purchaseNote: null,
  crashNext: false,
  xrayOpen: false,
  xray: null,
  xrayTab: "live",      // "live" (this household's trace) or "proof" (Data & Proof Explorer)
  proof: null,          // /v1/proof, fetched when the explorer opens
  proofTab: "flows",
  proofFlow: "correction",
  xsecOpen: {},         // X-Ray sections the person opened or closed (survives refreshes)
  scenarioOpen: false,  // developer drawer
  log: [],
  receipts: new Set(),
  access: null,           // /v1/access: every camera's status
  accessDevice: null,
  accessDetail: null,     // /v1/access/cameras/{id}
  accessVerified: null,   // { device, check } after a verification that found it usable
  accessLookup: null,     // a purchase or code the parent asked about
  accessSupport: null,
  accessMountGuide: null,
  lookupChannel: "activation_code",
  lookupRef: "",
  accessTracked: new Set(),
  studio: null,
  studioRun: null,
  window: null,
  outcome: null,
  certification: null,
  certOpen: null,
  windowNote: null,
  customers: null,        // /v1/customer-evidence
};

// ------------------------------------------------------------------ api

function traceId() { return "trc_ui_" + Math.random().toString(16).slice(2, 14); }

async function api(path, { method = "GET", body, viewer = state.viewer, headers = {}, retried = false } = {}) {
  const trace = traceId();
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.tokens[viewer]}`, "X-Trace-Id": trace, ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // The request never reached the server, so nothing changed there.
    setConnection(false);
    return { ok: false, status: 0, trace,
      data: { code: "OFFLINE", message: "We can't reach the server right now. Nothing was changed; try again when you're back online." } };
  }
  setConnection(true);
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  // A restarted server has a new session key, so every open tab's token stops
  // working. A 401 ran nothing: sign the synthetic viewers in again and repeat
  // the request once; if that isn't possible, say so instead of doing nothing.
  if (res.status === 401 && ["NO_WORKSPACE", "WORKSPACE_EXPIRED"].includes(data?.code)) showWorkspaceEnded();
  if (res.status === 401 && data?.code === "UNAUTHENTICATED" && !retried) {
    if (await renewSessions()) return api(path, { method, body, viewer, headers, retried: true });
    showSessionEnded();
  }
  return { ok: res.ok, status: res.status, data, trace };
}

async function renewSessions() {
  try {
    for (const v of VIEWERS) {
      const r = await fetch("/v1/dev/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ member_id: v.id }) });
      if (!r.ok) return false;
      state.tokens[v.id] = (await r.json()).token;
    }
    return true;
  } catch {
    return false;
  }
}

function showSessionEnded() {
  if (document.getElementById("session-banner")) return;
  const bar = document.createElement("div");
  bar.id = "session-banner";
  bar.className = "connection-banner";
  bar.setAttribute("role", "alert");
  bar.innerHTML = `Your session ended. Nothing was changed. <button type="button" class="btn btn-secondary" data-action="reload">Reload</button>`;
  document.getElementById("top-stack").append(bar);
}

function setConnection(online) {
  let bar = document.getElementById("connection-banner");
  if (online) { bar?.remove(); return; }
  if (bar) return;
  bar = document.createElement("div");
  bar.id = "connection-banner";
  bar.className = "connection-banner";
  bar.setAttribute("role", "status");
  bar.textContent = "Offline: showing the last data we loaded. We'll retry automatically.";
  document.getElementById("top-stack").append(bar);
}

// Replace an element's markup only when it changed, so a background refresh
// doesn't destroy the element that has keyboard focus.
const painted = new WeakMap();
function paint(el, html) {
  if (!el || painted.get(el) === html) return;
  painted.set(el, html);
  el.innerHTML = html;
}

const isParent = (viewer) => viewer === "mem_parent_a";
const name = (viewer) => VIEWERS.find((v) => v.id === viewer)?.short ?? viewer;

// ------------------------------------------------------------------ formatting

function fmtMinutes(m) {
  if (m === null || m === undefined) return "Unknown";
  const total = Math.round(m);
  const h = Math.floor(total / 60), min = total % 60;
  return h ? `${h} h ${String(min).padStart(2, "0")} min` : `${min} min`;
}
const clockFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "long", month: "short", day: "numeric" });
const clock = (iso) => (iso ? clockFmt.format(new Date(iso)) : "—");
const SOURCE_LABEL = { CAMERA: "Camera observed", PARENT: "Parent reported", CAREGIVER: "Caregiver reported" };

function sourceChip(source) {
  if (source === "CAMERA") return `<span class="chip chip-camera">${icon("camera", "icon-sm")}Camera observed</span>`;
  if (source === "PARENT" || source === "CAREGIVER") return `<span class="chip chip-parent">${icon("user", "icon-sm")}${SOURCE_LABEL[source]}</span>`;
  return `<span class="chip chip-unknown">${icon("unknown", "icon-sm")}Unknown source</span>`;
}

function factValue(key, fact) {
  if (!fact) return "—";
  switch (key) {
    case "recorded_sleep": return fact.state === "contested" ? `${fmtMinutes(fact.uncontested_minutes)} + ${fmtMinutes(fact.contested_minutes)} unresolved` : fmtMinutes(fact.minutes);
    case "sleep_onset": case "final_wake": return clock(fact.at);
    case "observation_coverage": return fact.pct === null ? "Unknown" : `${fact.pct}%`;
    case "unobserved": return fact.minutes === null ? "Unknown" : fmtMinutes(fact.minutes);
    default: return "—";
  }
}
const FACT_LABEL = { recorded_sleep: "Recorded sleep", sleep_onset: "Fell asleep", final_wake: "Final wake", observation_coverage: "Camera observation", unobserved: "Unobserved time" };
function factSources(key, fact) {
  if (!fact) return [];
  if (key === "recorded_sleep" || key === "sleep_onset" || key === "final_wake") return fact.sources || [];
  return ["CAMERA"];
}

function announce(text) {
  const el = document.getElementById("announcer");
  el.textContent = "";
  setTimeout(() => { el.textContent = text; }, 50);
}
let toastTimer;
function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text; el.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 6000);
}
function log(line) {
  const t = clockFmt.format(new Date());
  state.log.unshift(`${t}  ${line}`);
  state.log = state.log.slice(0, 60);
  document.querySelectorAll(".dev-log").forEach((el) => { el.textContent = state.log.join("\n"); });
}

// ------------------------------------------------------------------ data loading

async function loadBrief(viewer, revision) {
  const q = revision ? `?revision=${revision}` : "";
  const r = await api(`/v1/windows/${state.windowId}/brief${q}`, { viewer });
  if (!r.ok) throw r;
  return r.data;
}

async function loadWeek(viewer) {
  const r = await api(`/v1/windows/${state.windowId}/week`, { viewer });
  return r.ok ? r.data : null;
}

async function openScreen(viewer) {
  const brief = await loadBrief(viewer);
  state.screens[viewer] = { brief, previous: null, ack: null, highlight: false, week: await loadWeek(viewer) };
  state.latest = brief;
}

async function refreshLatest() {
  if (!state.windowId) return;
  const r = await api(`/v1/windows/${state.windowId}/brief`, { viewer: state.viewer });
  // The night this screen shows no longer exists: someone reset the demo.
  // A reset this page started itself (Studio, Customer evidence, scenario controls) is not "elsewhere".
  if (r.status === 404 && !present.active && !state.resetting) { state.resetElsewhere = true; return; }
  if (r.ok) state.latest = r.data;
  const c = await api(`/v1/windows/${state.windowId}/changes`, { viewer: state.viewer });
  if (c.ok) state.changes = c.data;
}

async function loadCommercial({ restorePurchase = false } = {}) {
  const [c, f, p] = await Promise.all([api("/v1/household/commercial", { viewer: VIEWERS[0].id }),
    api("/v1/features", { viewer: VIEWERS[0].id }), api("/v1/purchase-intents", { viewer: VIEWERS[0].id })]);
  if (c.ok) {
    state.commercial = c.data;
    if (!state.device || !c.data.devices.some((d) => d.id === state.device)) state.device = c.data.devices[0]?.id;
    await loadPreview();
  }
  if (f.ok) state.features = f.data;
  // A refresh must not forget an operation the server is still carrying.
  if (restorePurchase && !state.purchase && p.ok && p.data.length) {
    state.purchase = p.data[0];
    await refreshPurchase();
  }
}

// The synthetic night's window id changes whenever the demo is reseeded, so it
// is always resolved from the server, never cached across a reset.
async function loadNight() {
  const me = await api("/v1/me", { viewer: VIEWERS[0].id });
  const night = me.data.windows.find((w) => w.local_date === "2026-10-09") || me.data.windows[0];
  state.windowId = night.window_id;
  for (const v of VIEWERS) await openScreen(v.id);
}

// Behind the review gateway (deploy/gateway) each visitor first gets a private,
// expiring synthetic workspace. Run directly, the route doesn't exist (404) and
// nothing changes.
async function ensureWorkspace() {
  // A free host sleeps when idle: until the gateway itself answers (a JSON body
  // with a code), keep waking it for up to three minutes, and say so. A slot
  // still being wiped for the next visitor is also worth waiting for.
  const started = Date.now();
  let r, body;
  for (let attempt = 0; ; attempt++) {
    try { r = await fetch("/v1/workspace", { method: "POST" }); } catch { r = null; }
    if (r && (r.status === 404 || r.status === 405)) return true;
    if (r && r.ok) { state.workspace = await r.json(); return true; }
    body = r ? await r.json().catch(() => null) : null;
    const preparing = body?.code === "WORKSPACE_NOT_READY";
    // Every workspace in use: one frees within the idle limit, so wait for it (up to 15 minutes) instead of giving up.
    const busy = body?.code === "ALL_WORKSPACES_BUSY";
    if ((body?.code && !preparing && !busy) || Date.now() - started > (busy ? 900000 : 180000)) break;
    document.getElementById("main").innerHTML = busy
      ? `<div class="alert alert-info" role="status">${icon("clock")}<div>
      <p class="alert-title">Someone else is using the review workspace</p><p>This copy runs one private workspace at a time on free infrastructure. It frees up when they finish, or after a few idle minutes. This page keeps trying and opens as soon as it's yours.</p></div></div>`
      : preparing
      ? `<div class="alert alert-info" role="status">${icon("clock")}<div>
      <p class="alert-title">Preparing your workspace…</p><p>Its synthetic data is being reset to the starting point. This takes a few seconds.</p></div></div>`
      : `<div class="alert alert-info" role="status">${icon("clock")}<div>
      <p class="alert-title">Waking the backend…</p><p>It runs on free infrastructure and naps after 15 quiet minutes. This usually takes under a minute.</p></div></div>`;
    await new Promise((res) => setTimeout(res, busy ? 15000 : 4000));
  }
  body = body || {};
  document.getElementById("main").innerHTML = `<div class="alert alert-warning" role="alert">${icon("alert")}<div>
    <p class="alert-title">The review workspace couldn't start</p><p>${esc(body.message || "Try again in a few minutes.")}</p>
    <div class="btn-row"><button type="button" class="btn btn-secondary" data-action="reload">Try again</button></div></div></div>`;
  return false;
}

function showWorkspaceEnded() {
  if (document.getElementById("workspace-banner") || !state.workspace) return;   // one that never started didn't end
  clearInterval(pollTimer);
  const bar = document.createElement("div");
  bar.id = "workspace-banner";
  bar.className = "connection-banner";
  bar.setAttribute("role", "alert");
  const w = state.workspace || {};
  const limits = w.idle_minutes ? ` (they end after ${Math.round(w.idle_minutes)} idle minutes, or ${Math.round(w.max_minutes || 120)} in all)` : "";
  bar.innerHTML = `This private review workspace has ended${esc(limits)}. Its synthetic data is gone. <button type="button" class="btn btn-secondary" data-action="reload">Start a fresh one</button>`;
  document.getElementById("top-stack").append(bar);
}

async function boot() {
  if (!(await ensureWorkspace())) return;
  let accounts = await (await fetch("/v1/dev/accounts")).json();
  if (!accounts.length) { await fetch("/v1/dev/reset", { method: "POST" }); accounts = await (await fetch("/v1/dev/accounts")).json(); }
  for (const v of VIEWERS) {
    const r = await fetch("/v1/dev/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ member_id: v.id }) });
    state.tokens[v.id] = (await r.json()).token;
  }
  await loadNight();
  // The video module can be switched off on the server; its page then disappears.
  if (!(await api("/v1/video/status")).ok) {
    ROUTES.splice(ROUTES.findIndex((r) => r.id === "recorded"), 1);
    TAB_ROUTES.splice(TAB_ROUTES.indexOf("recorded"), 1);
  }
  await Promise.all([refreshLatest(), loadCommercial({ restorePurchase: true }), loadAccess()]);
  await enterRoute();
  pollTimer = setInterval(poll, 4000);
  const audience = new URLSearchParams(location.search).get("present");
  if (audience) runPresent(["recruiter", "product", "engineering"].includes(audience) ? audience : "recruiter");
}

let pollTimer;
async function poll() {
  if (state.resetElsewhere || state.resetting) return;
  try {
    const signature = () => JSON.stringify([state.latest?.revision, state.changes?.members, state.purchase?.state,
      state.access?.state_revision, state.access?.cameras.map((c) => c.status), state.accessDetail?.status]);
    const before = signature();
    await refreshLatest();
    if (["home", "access"].includes(state.route)) await refreshAccess();
    if (state.resetElsewhere) { clearInterval(pollTimer); render(); return; }
    if (state.purchase && !["CONFIRMED", "FAILED", "CANCELLED_BEFORE_SUBMISSION"].includes(state.purchase.state)) await refreshPurchase();
    const changed = signature() !== before;
    if (changed) render();
    else renderChrome();
    if (changed && state.xrayOpen && state.xrayTab === "live") await loadXray();
  } catch (e) { /* keep the current screen; next poll retries */ }
}

// ------------------------------------------------------------------ routing & chrome

function readRoute() {
  const [r, param] = (location.hash || "#/home").replace("#/", "").split("/");
  state.route = ROUTES.some((x) => x.id === r) ? r : "home";
  if (state.route === "access" && param) state.accessDevice = decodeURIComponent(param);
}

// Each route loads what it shows before it paints.
async function enterRoute() {
  readRoute();
  if (state.route === "home" || state.route === "access") await refreshAccess();
  if (state.route === "studio") { await loadStudio(); await loadAccess(); }
  if (state.route === "growth") { await loadAccess(); await loadGrowth(); }
  if (state.route === "customers") await loadCustomerEvidence();
  if (state.route === "recorded") await loadRecorded();
  renderChrome();
  render();
  document.title = `${ROUTES.find((r) => r.id === state.route)?.label ?? "Home"} · Proof Layer`;
}
// A new page starts at its top, with focus on its heading (not the scroll position of the last one).
window.addEventListener("hashchange", async () => {
  if (state.xrayOpen && !matchMedia("(min-width: 1280px)").matches) closeXray();   // its links navigate the page behind it
  await enterRoute();
  window.scrollTo(0, 0);
  const heading = document.querySelector("#main h1");
  if (heading) heading.tabIndex = -1;
  (heading || document.getElementById("main")).focus({ preventScroll: true });
  if (state.openComparison && state.route === "studio") { state.openComparison = false; await openComparison(); }
});

async function openComparison() {
  await runCompare();
  render();
  const card = document.getElementById("compare");
  card?.scrollIntoView({ behavior: smooth(), block: "start" });
  card?.querySelector(".cmp-grid")?.focus?.({ preventScroll: true });
}

function pendingCount() {
  const me = state.changes?.members?.find((m) => m.member_id === state.viewer);
  return me ? me.needs_review.length : 0;
}

function renderChrome() {
  const count = pendingCount();
  const badge = count ? `<span class="badge" aria-hidden="true">${count}</span><span class="sr-only">${count} unacknowledged changes</span>` : "";
  // On a phone the evidence tab stands for the whole lab (briefing, changes, plans).
  const current = (r, tab) => state.route === r.id || (tab && r.id === "briefing" && EVIDENCE_ROUTES.includes(state.route));
  const link = (r, cls, tab = false) => `<a href="#/${r.id}" ${current(r, tab) ? 'aria-current="page"' : ""}>${icon(r.icon, cls)}<span>${cls ? r.short : r.label}</span>${r.id === (tab ? "briefing" : "changes") ? badge : ""}</a>`;
  paint(document.getElementById("tab-bar"), ROUTES.filter((r) => TAB_ROUTES.includes(r.id)).map((r) => link(r, "icon-lg", true)).join("") +
    `<a href="#" data-action="xray-open" role="button">${icon("scan", "icon-lg")}<span>X-Ray</span></a>`);
  const nav = document.getElementById("side-nav");
  if (!nav.querySelector("#nav-family")) {
    nav.innerHTML = `<div id="nav-family"></div><div class="nav-group-label">Family evidence lab</div><div id="nav-evidence"></div>
      <details class="nav-eng" id="nav-eng"><summary>${icon("scan", "icon-sm")}<span>For engineers</span></summary>
        <div id="nav-engineering"></div>
        <button class="nav-item" type="button" data-action="xray-open">${icon("scan")}<span>Engineering X-Ray</span></button>
        <a class="nav-item" href="/architecture">${icon("compare")}<span>System architecture</span></a>
        <button class="nav-item nav-item-quiet" type="button" data-action="scenario-open" aria-controls="dev-drawer">${icon("sliders")}<span>Scenario controls</span></button>
      </details>`;
  }
  // Engineering pages stay out of the parent's way unless you're on one.
  // Open on engineering pages, or when the person opened it; closed on the parent's pages.
  document.getElementById("nav-eng").open = ROUTES.some((r) => r.group === "engineering" && r.id === state.route) || Boolean(state.navEngOpen);
  for (const group of ["family", "evidence", "engineering"]) {
    paint(document.getElementById(`nav-${group}`), ROUTES.filter((r) => r.group === group).map((r) => link(r, "")).join(""));
  }
  paint(document.getElementById("viewer-switch"), VIEWERS.map((v) =>
    `<button type="button" role="radio" aria-checked="${state.viewer === v.id}" data-action="viewer" data-viewer="${v.id}">${v.short}</button>`).join(""));
  document.querySelectorAll(".dev-log").forEach((el) => { el.textContent = state.log.join("\n"); });
}

// ------------------------------------------------------------------ briefing surface

function timeline(brief, previous) {
  const f = brief.facts;
  const start = new Date(f.window.starts_at).getTime(), end = new Date(f.window.ends_at).getTime();
  const pct = (iso) => ((new Date(iso).getTime() - start) / (end - start)) * 100;
  const seg = (s, e) => `left:${pct(s).toFixed(3)}%;width:${(pct(e) - pct(s)).toFixed(3)}%`;
  const rows = [];
  const parts = [];
  // Observed-awake = observed time without recorded sleep; drawn as a thin rule.
  parts.push(`<div class="seg seg-awake" style="left:0;width:100%" aria-hidden="true"></div>`);
  for (const g of f.unobserved.intervals || []) {
    const label = g.reason === "camera_reported_unavailable" ? "camera offline" : "no camera report";
    parts.push(`<div class="seg seg-unobserved" style="${seg(g.start, g.end)}">${pct(g.end) - pct(g.start) > 9 ? "Not observed" : ""}</div>`);
    rows.push([clock(g.start), clock(g.end), fmtMinutes(g.minutes), "Not observed", label]);
  }
  for (const i of f.recorded_sleep.intervals || []) {
    const parent = i.source !== "CAMERA";
    const mins = (new Date(i.end) - new Date(i.start)) / 60000;
    parts.push(`<div class="seg seg-sleep ${parent ? "parent" : "camera"}" style="${seg(i.start, i.end)}">${parent ? "P" : ""}</div>`);
    rows.push([clock(i.start), clock(i.end), fmtMinutes(mins), "Asleep", SOURCE_LABEL[i.source]]);
  }
  if (previous && brief.revision !== previous.revision) {
    const now = new Set((f.recorded_sleep.intervals || []).map((i) => i.start + i.end));
    for (const i of previous.facts.recorded_sleep.intervals || []) {
      if (!now.has(i.start + i.end)) parts.push(`<div class="seg-ghost" style="${seg(i.start, i.end)}" aria-hidden="true"></div>`);
    }
  }
  const ticks = [];
  for (let h = 0; h <= 12; h++) {
    const t = new Date(start + h * 3600000);
    const minor = h % 2 === 1 ? " minor" : (h % 4 !== 0 && h !== 12 ? " sparse" : "");
    ticks.push(`<span class="tick${minor}" style="left:${(h / 12) * 100}%">${clockFmt.format(t)}</span>`);
  }
  // Window-relative order (19:00 sorts before 03:40), for the table and the spoken label.
  const minuteOf = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return ((h + 5) % 24) * 60 + m; };
  rows.sort((a, b) => minuteOf(a[0]) - minuteOf(b[0]));
  const spoken = rows.map((r) => `${r[3].toLowerCase()} ${r[0]} to ${r[1]}, ${r[4].toLowerCase()}`);
  return `
    <div class="timeline">
      <div class="timeline-head"><h2>The night, 19:00 to 07:00</h2><span class="muted small">${f.window.elapsed_minutes} minutes in this window</span></div>
      <div class="track" role="img" aria-label="Night timeline, 19:00 to 07:00: ${esc(spoken.join("; "))}. Details in the list below.">${parts.join("")}</div>
      <div class="ticks" aria-hidden="true">${ticks.join("")}</div>
      <div class="legend" aria-hidden="true">
        <span><i class="sw camera"></i>Asleep · camera observed</span>
        <span><i class="sw parent"></i>Asleep · parent reported (P)</span>
        <span><i class="sw awake"></i>Observed, no sleep recorded</span>
        <span><i class="sw unobserved"></i>Not observed</span>
      </div>
      <details class="list-toggle"><summary>Show as list</summary>
        <div class="table-scroll" tabindex="0" role="region" aria-label="Night as a list"><table class="plain"><thead><tr><th scope="col">Start</th><th scope="col">End</th><th scope="col">Duration</th><th scope="col">State</th><th scope="col">Source</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      </details>
    </div>`;
}

const weekdayFmt = new Intl.DateTimeFormat("en-US", { weekday: "short", day: "numeric", timeZone: "UTC" });

function weekStrip(screen) {
  const w = screen.week;
  if (!w || !w.nights.length) return "";
  const max = Math.max(...w.nights.map((n) => n.minutes || 0), 1);
  const bars = w.nights.map((n) => {
    const day = weekdayFmt.format(new Date(n.local_date + "T12:00:00Z"));
    const height = n.minutes ? Math.max(8, Math.round((n.minutes / max) * 100)) : 100;
    const tag = n.corrected ? `<span class="week-tag edited">${icon("edit", "icon-sm")}<span class="week-tag-text">edited</span></span>`
      : n.minutes === null ? `<span class="week-tag">${icon("unknown", "icon-sm")}<span class="week-tag-text">${esc(n.excluded_reason)}</span></span>` : "";
    const spoken = `${day}: ${n.minutes === null ? `not counted, ${n.excluded_reason}` : fmtMinutes(n.minutes)}${n.corrected ? ", edited" : ""}`;
    return `<li class="week-night${n.minutes === null ? " excluded" : ""}${n.corrected ? " corrected" : ""}" aria-label="${esc(spoken)}">
      <span class="week-tag-slot" aria-hidden="true">${tag}</span>
      <span class="week-value" aria-hidden="true">${n.minutes === null ? "—" : esc(fmtMinutes(n.minutes).replace(" min", "m").replace(" h ", "h "))}</span>
      <span class="week-bar" style="height:${height}%" aria-hidden="true"></span>
      <span class="week-day" aria-hidden="true">${esc(day)}</span>
    </li>`;
  }).join("");
  const avg = w.average_minutes === null ? "No exact average" : `Average ${fmtMinutes(w.average_minutes)}`;
  return `<section class="week" aria-label="Last 7 nights">
    <div class="timeline-head"><h2>Last 7 nights</h2><span class="muted small">${esc(avg)} · ${w.counted} of ${w.of} nights</span></div>
    <ol class="week-bars">${bars}</ol>
    <p class="small muted">Read from each night's current version whenever you open it, so an edit to a past night shows up here. Edited nights are marked; nights without an exact total are left out of the average.</p>
  </section>`;
}

function statusLine(f) {
  const s = f.recorded_sleep;
  const sleep = s.state === "no_records" ? "No sleep was recorded" :
    s.state === "contested" ? `${fmtMinutes(s.uncontested_minutes)} of recorded sleep, plus ${fmtMinutes(s.contested_minutes)} that sources disagree about` :
      `${fmtMinutes(s.minutes)} of recorded sleep`;
  const gaps = f.unobserved.intervals || [];
  if (f.observation_coverage.state === "unknown") return `${sleep}. We have no camera observation report for this night, so how much was observed is unknown.`;
  if (!gaps.length) return `${sleep}. The camera observed the whole window.`;
  const g = gaps.map((x) => `${clock(x.start)}–${clock(x.end)}`).join(", ");
  return `${sleep}. The camera didn't observe ${g}, so ${gaps.length === 1 ? "that" : "those"} ${fmtMinutes(f.unobserved.minutes)} ${gaps.length === 1 ? "stays" : "stay"} unknown.`;
}

const STATUS_CHIP = {
  complete: `<span class="chip chip-camera">${icon("check", "icon-sm")}Fully observed</span>`,
  incomplete_observation: `<span class="chip chip-unknown">${icon("unknown", "icon-sm")}Incomplete observation</span>`,
  unknown_observation: `<span class="chip chip-unknown">${icon("unknown", "icon-sm")}Observation unknown</span>`,
  no_observation: `<span class="chip chip-unknown">${icon("unknown", "icon-sm")}Not observed</span>`,
  conflicting_evidence: `<span class="chip chip-unknown">${icon("alert", "icon-sm")}Sources disagree</span>`,
};

function tile(key, brief, previous, sub, extraLabel = "") {
  const f = brief.facts[key];
  const changed = previous && previous.revision !== brief.revision && previous.fingerprints[key] !== brief.fingerprints[key];
  const unknown = f && ((key === "observation_coverage" && f.pct === null) || (key === "unobserved"));
  const sources = factSources(key, f);
  return `<div class="tile ${changed ? "changed" : ""} tile-${key}">
    <div class="tile-head"><span class="tile-label">${extraLabel}${FACT_LABEL[key]}</span>${changed ? '<span class="chip chip-changed">Changed</span>' : ""}</div>
    <div class="tile-value ${unknown ? "unknown" : ""} ${changed ? "value-in" : ""}"><span class="${changed ? "highlight" : ""}">${esc(factValue(key, f))}</span></div>
    ${changed ? `<div class="was"><span class="sr-only">was </span>was ${esc(factValue(key, previous.facts[key]))}</div>` : ""}
    ${sub ? `<div class="tile-sub">${sub}</div>` : ""}
    ${key === "recorded_sleep" || key === "final_wake" || key === "sleep_onset" ? `<div class="tile-chips">${sources.map(sourceChip).join("")}</div>` : ""}
  </div>`;
}

function ackCard(viewer, screen) {
  const brief = screen.brief;
  const latest = state.latest;
  const member = state.changes?.members?.find((m) => m.member_id === viewer);
  const ack = screen.ack;
  if (ack && ack.newer_available && ack.changed_since.length) {
    return `<div class="card ack-card stale" role="status">
      <p class="ack-title">${icon("alert")}${esc(ack.message.split(". ")[0])}.</p>
      <p class="ack-body">${esc(ack.message.split(". ").slice(1).join(". "))} Your acknowledgment was recorded for revision ${ack.acknowledged_revision}; revision ${ack.current_revision} is the current one.</p>
      <dl class="diff mt-4">${ack.changed_since.map((k) => diffRow(k, brief.facts[k], latest.facts[k], true, latest.facts[k])).join("")}</dl>
      <div class="btn-row">
        <button class="btn btn-primary" type="button" data-action="show-latest" data-viewer="${viewer}">Review revision ${ack.current_revision}</button>
      </div>
      <p class="reason-line">An acknowledgment records which version you viewed. It doesn't mean anything was confirmed or handed over.</p>
    </div>`;
  }
  if (member && member.acknowledged_revision === brief.revision) {
    return `<div class="card ack-card"><p class="ack-title">${icon("check")}You acknowledged revision ${brief.revision}</p>
      <p class="ack-body">Recorded as ${esc(name(viewer))} viewing these facts.</p></div>`;
  }
  return `<div class="card ack-card">
    <p class="ack-title">${icon("eye")}Revision ${brief.revision} is ready to review</p>
    <p class="ack-body">Acknowledging records that you viewed this version of the five facts above.</p>
    <div class="btn-row"><button class="btn btn-primary" type="button" data-action="ack" data-viewer="${viewer}">Acknowledge revision ${brief.revision}</button></div>
  </div>`;
}

function briefingScreen(viewer) {
  const screen = state.screens[viewer];
  if (!screen) return `<div class="skeleton"></div><p class="muted">Loading last night…</p>`;
  const b = screen.brief, f = b.facts;
  const stale = state.latest && state.latest.revision > b.revision;
  const gap = (f.unobserved.intervals || [])[0];
  const head = `
      <p class="eyebrow">${dayFmt.format(new Date(f.window.starts_at))} into ${dayFmt.format(new Date(f.window.ends_at))} · ${esc(b.child.name)}</p>
      <h1 class="display">Last night, clearly.</h1>
      <div class="head-row">
        <span class="rev-badge ${stale ? "superseded" : ""}" aria-label="Revision ${b.revision}${stale ? ", earlier version" : ", current"}">Rev ${b.revision}${stale ? " · earlier version" : ""}</span>
        ${STATUS_CHIP[f.status] || ""}
        <span class="muted small">Updated ${clock(b.computed_at)}</span>
      </div>`;
  const staleBanner = stale ? `<div class="alert alert-info stale-banner" role="status">${icon("refresh")}<div><p class="alert-title">A newer version exists. You're viewing revision ${b.revision}.</p>
      <div class="btn-row" style="margin-top:8px"><button class="btn btn-secondary" type="button" data-action="show-latest" data-viewer="${viewer}">Show revision ${state.latest.revision}</button></div></div></div>` : "";
  const window = `${f.window.elapsed_minutes} min window`;
  const reportedUnseen = f.recorded_sleep.reported_in_unobserved_minutes || 0;
  const sleepSub = reportedUnseen
    ? `includes ${fmtMinutes(reportedUnseen)} parent reported while the camera wasn't observing`
    : "selected records, not a clinical measure";
  const tiles = `<div class="tiles" role="group" aria-label="Last night in five facts">
      ${tile("recorded_sleep", b, screen.previous, sleepSub)}
      ${tile("sleep_onset", b, screen.previous, f.sleep_onset.at_window_edge ? "at the start of the window" : "")}
      ${tile("final_wake", b, screen.previous, "")}
      ${tile("observation_coverage", b, screen.previous, f.observation_coverage.pct === null ? "no camera report" : `camera observation of the ${window}`)}
      ${tile("unobserved", b, screen.previous, gap ? `${clock(gap.start)}–${clock(gap.end)} · ${gap.reason === "camera_reported_unavailable" ? "camera offline" : "no report"}` : "", '<span class="hatch-swatch" aria-hidden="true"></span>')}
    </div>`;
  const correct = isParent(viewer) && !stale ? `<button class="btn btn-secondary" type="button" data-action="correct-open" data-viewer="${viewer}">${icon("edit", "icon-sm")}Correct final wake</button>` : "";
  return `${staleBanner}
    <div class="briefing">
      <section class="night-hero" aria-label="Summary" data-render="night_briefing" data-subject="${b.revision_id}" data-viewer="${viewer}">
        <div class="hero-text">${head}<p class="summary-line">${esc(statusLine(f))}</p></div>
        ${correct ? `<div class="hero-actions">${correct}</div>` : ""}
      </section>
      ${tiles}
      <div class="card timeline-card">${timeline(b, screen.previous)}</div>
      <div class="briefing-lower">
        <div class="card week-card">${weekStrip(screen)}</div>
        <section class="ack-col" aria-label="Acknowledgment">${ackCard(viewer, screen)}
          <p class="small muted footnote">Camera observation measures whether the camera could see, not how well the baby slept. Time the camera didn't observe counts as sleep only when a parent reports it, and it's labelled parent reported.</p>
        </section>
      </div>
    </div>`;
}

// ------------------------------------------------------------------ what changed

// What kind of change a fact went through. The server says *whether* a fact
// changed (its fingerprint covers value, provenance and uncertainty); this
// only names which part changed, so "40 min → 40 min" is never shown as a new value.
function changeKind(key, before, after, changed) {
  if (!changed) return "same";
  if (factValue(key, before) !== factValue(key, after)) return "value";
  if (factSources(key, before).join() !== factSources(key, after).join()) return "source";
  return "detail";
}
const KIND_BADGE = {
  value: `<span class="kind kind-value">${icon("edit", "icon-sm")}Value changed</span>`,
  source: `<span class="kind kind-source">${icon("user", "icon-sm")}Source changed</span>`,
  detail: `<span class="kind kind-detail">${icon("refresh", "icon-sm")}Details changed</span>`,
  same: `<span class="kind kind-same">${icon("minus", "icon-sm")}Unchanged</span>`,
};
const DETAIL_TEXT = {
  recorded_sleep: "Same total; the sleep blocks behind it moved.",
  unobserved: "Same total; the unobserved period moved.",
};

function diffRow(key, before, after, changed, _latest) {
  const kind = changeKind(key, before, after, changed);
  const src = after ? factSources(key, after) : [];
  let values;
  if (kind === "value") {
    values = `<span class="before"><span class="sr-only">was </span>${esc(factValue(key, before))}</span>${icon("arrow", "icon-sm")}<span class="after"><span class="sr-only">now </span>${esc(factValue(key, after))}</span>`;
  } else if (kind === "source") {
    values = `<span class="same-value">${esc(factValue(key, after))}</span><span class="diff-note">Same value. Was ${esc(factSources(key, before).map((x) => SOURCE_LABEL[x] || x).join(", ") || "unknown")}.</span>`;
  } else if (kind === "detail") {
    values = `<span class="same-value">${esc(factValue(key, after))}</span><span class="diff-note">${esc(DETAIL_TEXT[key] || "Same value; its supporting details changed.")}</span>`;
  } else {
    values = `<span class="same-value">${esc(factValue(key, after))}</span>`;
  }
  return `<div class="diff-row kind-row-${kind}"><dt>${FACT_LABEL[key]}</dt>
    <dd class="diff-vals">${values}</dd>
    <dd class="diff-meta">${KIND_BADGE[kind]}${kind === "same" ? "" : `<span class="diff-src">${src.map(sourceChip).join("")}</span>`}</dd></div>`;
}

function changesScreen() {
  const c = state.changes;
  if (!c) return `<div class="skeleton"></div>`;
  const cause = c.latest_cause || {};
  const by = cause.member_id ? name(cause.member_id) : "The camera";
  const what = cause.kind === "correction" ? `${by} corrected a sleep record` : "New camera data arrived";
  const changed = c.changes.filter((x) => x.changed);
  const kinds = c.changes.map((x) => changeKind(x.fact, x.before, x.after, x.changed));
  const count = (k) => kinds.filter((x) => x === k).length;
  const tally = [["value", "value changed", "values changed"], ["source", "source changed", "sources changed"],
    ["detail", "detail changed", "details changed"], ["same", "unchanged", "unchanged"]]
    .filter(([k]) => count(k)).map(([k, one, many]) => `<span class="kind kind-${k}">${count(k)} ${count(k) === 1 ? one : many}</span>`).join("");
  const members = c.members.map((m) => {
    const statusText = m.acknowledged_revision == null ? "Has not acknowledged any version" :
      m.has_acknowledged_current ? `Acknowledged revision ${m.acknowledged_revision}` :
        `Acknowledged revision ${m.acknowledged_revision} · has not acknowledged revision ${m.current_revision}`;
    const items = m.facts.map((f) => {
      const cls = f.state === "acknowledged" ? "st-ok" : f.state === "changed_since_acknowledgment" ? "st-changed" : "st-none";
      const label = f.state === "acknowledged" ? `${icon("check", "icon-sm")}Still acknowledged` : f.state === "changed_since_acknowledgment" ? `${icon("alert", "icon-sm")}Needs review · changed since rev ${f.acknowledged_revision}` : `${icon("minus", "icon-sm")}Not acknowledged`;
      return `<li class="${cls}-row"><span>${esc(f.label)}</span><span class="fact-state ${cls}">${label}</span></li>`;
    }).join("");
    const review = m.facts.filter((f) => f.state === "changed_since_acknowledgment").length;
    const verdict = m.acknowledged_revision == null
      ? `<p class="member-verdict st-none">${icon("minus")}Hasn't acknowledged any version</p>`
      : review ? `<p class="member-verdict st-changed">${icon("alert")}${review === 1 ? "One fact needs" : `${review} facts need`} review</p>`
        : `<p class="member-verdict st-ok">${icon("check")}Up to date with revision ${m.acknowledged_revision}</p>`;
    return `<div class="card member-card ${review ? "needs-review" : ""}"><h3>${esc(m.display_name)} <span class="chip chip-camera">${esc(m.role)}</span></h3>
      ${verdict}${m.acknowledged_revision == null ? "" : `<p class="small muted" style="margin:0">${statusText}</p>`}<ul class="fact-list">${items}</ul></div>`;
  }).join("");
  return `
    <div class="page-head"><p class="eyebrow">Revision ${c.current_revision}${c.compared_with ? ` compared with revision ${c.compared_with}` : ""}</p>
      <h1 class="display">What changed</h1>
      <p class="lede">${c.compared_with ? `${esc(what)}. ${changed.length ? `${changed.length === 1 ? "One detail" : changed.length === 2 ? "Two details" : `${changed.length} details`} changed; everything else stands.` : "No acknowledged facts changed."}` : "This is the first version of the briefing."}</p></div>
    <section aria-label="Changes"><div class="card diff-card"><div class="card-head"><h2>Before and after</h2><div class="tally">${tally}</div></div>
      <dl class="diff diff-table">${c.changes.map((x) => diffRow(x.fact, x.before, x.after, x.changed)).join("")}</dl>
      ${cause.kind === "correction" ? `<p class="small muted mt-4">The original camera record is kept unchanged in the evidence history. The correction is labelled parent reported. Unobserved time stays unknown.</p>` : ""}
    </div></section>
    <section aria-label="Who has reviewed this"><h2>Who has reviewed which version</h2>
      <p class="small muted" style="margin-top:-6px">An acknowledgment records which version someone viewed. It doesn't mean they've seen anything newer.</p>
      <div class="member-grid">${members}</div></section>`;
}

// ------------------------------------------------------------------ feature availability

const PLAN_LABEL = { none: "No plan", sleep: "Sleep", memories: "Memories", milestones: "Milestones" };
const STALE_TEXT = {
  HOUSEHOLD_STATE_CHANGED: "Your camera setup or plan changed after this offer was shown.",
  DECISION_EXPIRED: "The offer expired.",
  CATALOG_CHANGED: "The plan catalog changed.",
  POLICY_CHANGED: "The feature rules changed.",
  ELIGIBILITY_CHANGED: "Eligibility changed.",
};

function requirementList(d) {
  return `<ul class="req-list">${(d.inputs.requirements || []).map((r) => {
    const st = r.met === true ? ["req-met", "check", "Met"] : r.met === false ? ["req-unmet", r.kind === "hardware" ? "device" : "card", "Not met"] : ["req-unknown", "unknown", "Not checked yet"];
    return `<li>${icon(st[1])}<span>${esc(r.label)} <span class="muted small">(${r.kind})</span></span><span class="req-state ${st[0]}">${st[2]}</span></li>`;
  }).join("")}</ul>`;
}

function offerCard(d) {
  const o = d.offer;
  const until = new Date(d.expires_at);
  const minsLeft = Math.max(0, Math.round((until - Date.now()) / 60000));
  if (state.purchaseNote) return state.purchaseNote;
  // A killed experiment reports no variant, so its exposure is never counted.
  return `<div class="card offer-card">
    <p class="offer-title">${esc(o.label)}</p>
    <p style="margin:8px 0 0"><span class="chip chip-sim">Simulated payment. No money moves.</span></p>
    <dl class="terms">
      <dt>Plan</dt><dd>${esc(o.plan_label)}</dd>
      <dt>Unlocks</dt><dd>${esc(featureLabel(o.feature))}</dd>
      <dt>Billing</dt><dd>Yearly · $${(o.amount_minor / 100).toFixed(2)} ${esc(o.currency)}</dd>
      <dt>Price basis</dt><dd>${esc(o.price_basis)}</dd>
      <dt>Checked against</dt><dd class="muted">${esc(o.catalog_version)} · setup rev ${d.state_revision}</dd>
    </dl>
    <p class="expiry ${minsLeft <= 2 ? "soon" : ""}">${icon("clock", "icon-sm")}Offer valid until ${clock(d.expires_at)} (${minsLeft} min)</p>
    <div class="btn-row"><button class="btn btn-primary" type="button" data-action="buy">Start simulated upgrade</button></div>
  </div>`;
}

function featureLabel(key) { return state.features.find((f) => f.feature === key)?.label ?? key; }

const SETTLED = ["CONFIRMED", "FAILED", "CANCELLED_BEFORE_SUBMISSION"];

// A settled purchase can leave the screen; one still in flight stays until it
// settles. A new offer always gets a new idempotency key.
function clearSettledPurchase() {
  if (state.purchase && SETTLED.includes(state.purchase.state)) state.purchase = null;
  state.purchaseKey = null;
}

function purchasePanel() {
  const p = state.purchase;
  if (!p) return "";
  const text = {
    PREPARED: [icon("clock", "icon-sm"), "Pending: waiting for the worker"],
    SUBMITTED: [icon("clock", "icon-sm"), "Pending: waiting for the provider"],
    CONFIRMED: [icon("check", "icon-sm"), "Confirmed (simulated)"],
    FAILED: [icon("xoct", "icon-sm"), "Failed. Not charged"],
    UNKNOWN: [icon("unknown", "icon-sm"), "Outcome unknown. Checking"],
    CANCELLED_BEFORE_SUBMISSION: [icon("unknown", "icon-sm"), "Cancelled before submission. Not charged"],
  }[p.state];
  const retries = p.provider_record ? p.provider_record.attempts : 0;
  const explain = {
    UNKNOWN: "We don't know yet whether the provider completed this. We're checking its records and won't assume either way.",
    SUBMITTED: "Sent to the simulated provider. If the app stops now, it recovers this exact operation from the provider's records.",
    CONFIRMED: `Simulated ${PLAN_LABEL[p.terms.plan_code]} plan active. Check availability again to see the feature unlocked.`,
    FAILED: "The simulated provider declined it. Nothing was charged.",
    CANCELLED_BEFORE_SUBMISSION: "Your setup changed before submission, so it was never sent. Nothing was charged.",
    PREPARED: "Saved once. Retrying uses the same operation.",
  }[p.state];
  const scope = p.terms?.feature ? `${featureLabel(p.terms.feature)} on ${deviceName(p.terms.device_id)} · ${PLAN_LABEL[p.terms.plan_code] || p.terms.plan_code}` : "";
  return `<div class="card" role="status" aria-live="polite"><h3>Simulated upgrade</h3>
    ${scope ? `<p class="small muted">${esc(scope)}</p>` : ""}
    <div class="state-row"><span class="pill pill-${p.state}">${text[0]}${text[1]}</span></div>
    <p class="op-line">${esc(explain)}</p>
    <p class="op-line">One operation · provider attempts ${retries}${p.transitions ? ` · ${p.transitions.length} recorded state changes` : ""}</p>
    ${p.state === "CONFIRMED" || p.state === "FAILED" ? `<div class="btn-row"><button class="btn btn-secondary" type="button" data-action="decide">${icon("refresh", "icon-sm")}Check availability again</button></div>` : ""}
  </div>`;
}

function decisionCard() {
  const d = state.decision;
  if (!d) return `<div class="card empty-result">${icon("key", "icon-lg")}<p class="muted" style="margin:0">Pick a camera and a feature, then check whether it works with that camera's mount and plan.</p></div>${purchasePanel()}`;
  const hardware = d.blockers.filter((b) => b.kind === "hardware");
  const plan = d.blockers.filter((b) => b.kind === "subscription");
  const label = featureLabel(d.feature);
  const checklistFirst = d.experiment.variant === "requirements_checklist";
  let body;
  if (d.status === "AVAILABLE") {
    body = `<div class="alert alert-success">${icon("check")}<div><p class="alert-title">Available on this account</p><p>${esc(label)} works with your current camera setup and plan.</p></div></div>`;
  } else if (d.status === "UNKNOWN") {
    body = `<div class="alert alert-unknown">${icon("unknown")}<div><p class="alert-title">We can't tell yet</p><p>${esc(d.blockers.map((b) => b.parent_message).join(" "))} We won't suggest a purchase until we know.</p></div></div>`;
  } else if (hardware.length) {
    body = `<div class="alert alert-warning">${icon("device")}<div><p class="alert-title">Blocked by hardware</p><p>${esc(hardware[0].parent_message)}</p>${plan.length ? `<p class="small">It would also need a different plan, but changing the plan alone wouldn't help, so we're not offering one.</p>` : ""}</div></div>`;
  } else {
    body = `<div class="alert alert-info">${icon("card")}<div><p class="alert-title">Needs a plan</p><p>${esc(plan[0].parent_message)}</p></div></div>`;
  }
  const checklist = `<h3 class="mt-4">Requirements</h3>${requirementList(d)}`;
  const side = `${d.offer && (!state.purchase || ["FAILED", "CANCELLED_BEFORE_SUBMISSION"].includes(state.purchase.state)) ? offerCard(d) : ""}${purchasePanel()}`;
  return `<div class="result-grid ${side.trim() ? "has-side" : ""}">
    <div class="card blocker-card ${hardware.length ? "hardware" : ""}" data-render="feature_availability" data-subject="${d.decision_id}" data-variant="${d.experiment.killed ? "" : d.experiment.variant}">
      <h2>${esc(label)}</h2>
      ${checklistFirst ? checklist + `<div class="mt-4">${body}</div>` : body + checklist}
    </div>
    ${side.trim() ? `<div class="result-side">${side}</div>` : ""}
  </div>`;
}

function selectedCamera() {
  const devices = state.commercial?.devices || [];
  return devices.find((d) => d.id === state.device) || devices[0];
}

function planLine(plan) {
  if (!plan) return "";
  if (plan.state === "checking") return `Confirming a ${esc(PLAN_LABEL[plan.pending_plans.at(-1)] || "")} purchase…`;
  return plan.plan_code === "none" ? "No plan" : `${esc(plan.plan_label)} plan`;
}

function cameraPicker() {
  const devices = state.commercial?.devices || [];
  return `<div class="camera-picker" role="radiogroup" aria-label="Camera">${devices.map((d) => {
    const on = selectedCamera()?.id === d.id;
    return `<button type="button" role="radio" aria-checked="${on}" data-action="camera" data-device="${d.id}" class="camera-card">
      <span class="camera-icon" aria-hidden="true">${icon("camera", "icon-lg")}</span>
      <span class="camera-body">
        <span class="camera-name">${esc(d.label)}</span>
        <span class="camera-meta">${esc(d.mount_label)}</span>
        <span class="camera-tags"><span class="tag">${planLine(d.plan)}</span><span class="tag">Up to ${d.plan.member_cap} caregivers</span></span>
      </span>
      <span class="camera-check" aria-hidden="true">${on ? `${icon("check", "icon-sm")}Selected` : ""}</span>
    </button>`;
  }).join("")}</div>`;
}

function planPreview() {
  const pv = state.preview;
  const cam = selectedCamera();
  if (!pv || !cam || pv.device.id !== cam.id) return "";
  const features = Object.keys(pv.feature_labels);
  const cell = (f) => f.status === "AVAILABLE" ? `<span class="cell cell-yes">${icon("check", "icon-sm")}Included</span>`
    : f.blocked_by.includes("hardware") ? `<span class="cell cell-hw">${icon("device", "icon-sm")}Needs a different mount</span>`
      : `<span class="cell cell-no">${icon("minus", "icon-sm")}Not in this plan</span>`;
  const planHead = (p) => `<span class="plan-name">${esc(p.plan_label)}${p.is_current ? ' <span class="chip chip-camera">current</span>' : ""}</span>${p.amount_minor ? `<span class="plan-price">$${p.amount_minor / 100}/year (simulated)</span>` : ""}`;
  // Wide containers get a comparison table; narrow ones get one card per plan.
  // Same data, nothing hidden: the card list carries every column.
  return `<section class="plans-section" aria-label="Plans for this camera"><div class="card plans-card">
    <h2>What each plan would unlock on the ${esc(pv.device.label.toLowerCase())}</h2>
    <p class="small muted card-sub">Same rules as the check above. Mount: ${esc(pv.device.mount_label)}. A plan can't change a mount.</p>
    <div class="table-scroll plan-table" tabindex="0" role="region" aria-label="Plan comparison"><table class="plain compare">
      <thead><tr><th scope="col">Plan</th>${features.map((f) => `<th scope="col">${esc(pv.feature_labels[f])}</th>`).join("")}</tr></thead>
      <tbody>${pv.plans.map((p) => `<tr${p.is_current ? ' class="current-plan"' : ""}><th scope="row">${planHead(p)}</th>${features.map((f) => `<td>${cell(p.features[f])}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <ul class="plan-cards" aria-label="Plan comparison">${pv.plans.map((p) => `<li class="plan-card${p.is_current ? " current-plan" : ""}">
      <div class="plan-card-head">${planHead(p)}</div>
      <dl>${features.map((f) => `<div><dt>${esc(pv.feature_labels[f])}</dt><dd>${cell(p.features[f])}</dd></div>`).join("")}</dl></li>`).join("")}</ul>
    </div></section>`;
}

function featuresScreen(viewer) {
  if (!isParent(viewer)) {
    return `<div class="page-head"><h1 class="display">Feature availability</h1></div>
      <div class="alert alert-unknown">${icon("lock")}<div><p class="alert-title">Only the account owner can check plans</p><p>${esc(name(viewer))} is a caregiver on this household. Plans and purchases are managed by the owner.</p></div></div>`;
  }
  const picker = state.features.map((f) => `<button type="button" aria-pressed="${state.feature === f.feature}" data-action="feature" data-feature="${f.feature}">
      <span class="feature-name">${esc(f.label)}</span><span class="feature-desc">${esc(f.parent_value || "")}</span></button>`).join("");
  return `<div class="page-head"><p class="eyebrow">Your household</p><h1 class="display">Feature availability</h1>
      <p class="lede">Plans attach to a camera. Pick a camera and a feature to see whether it works, and whether a plan would actually unlock it.</p></div>
    <div class="step-label-row"><span class="step-num">1</span><h2>Choose a camera</h2></div>
    ${cameraPicker()}
    <div class="step-label-row"><span class="step-num">2</span><h2>Choose a feature</h2></div>
    <div class="feature-picker" role="group" aria-label="Feature">${picker}</div>
    <div class="check-row"><button class="btn btn-primary btn-lg" type="button" data-action="decide">Check availability</button>
      <span class="muted small">Checked by the server against this camera's plan and mount. Nothing is bought here.</span></div>
    ${state.decisionNote || ""}
    <section class="result-section" aria-label="Result">${decisionCard()}</section>
    ${planPreview()}`;
}

async function loadPreview() {
  const cam = selectedCamera();
  if (!cam) return;
  const r = await api(`/v1/devices/${cam.id}/plan-preview`, { viewer: VIEWERS[0].id });
  if (r.ok) state.preview = r.data;
}

// ------------------------------------------------------------------ purchase to access
// The server decides every status, stage and allowed action; these screens only
// lay them out. Nothing here ever offers a purchase.

const ACCESS_TONE = {
  ACTIVE_FEATURE_USABLE: ["success", "check"], ACTIVE_HARDWARE_BLOCKED: ["warning", "device"],
  PLAN_EXCLUDES_FEATURE: ["neutral", "minus"], ASSIGNED_PENDING_CONFIRMATION: ["pending", "clock"],
  VERIFIED_UNBOUND: ["action", "link"], DUPLICATE_PURCHASE: ["warning", "copy"], ALREADY_ASSIGNED: ["warning", "camera"],
  PURCHASE_PENDING: ["pending", "clock"], PROVIDER_UNKNOWN: ["unknown", "unknown"], CONFLICTING_EVIDENCE: ["unknown", "alert"],
  REFUNDED_OR_REVOKED: ["warning", "alert"], PLAN_EXPIRED: ["warning", "clock"], PURCHASE_UNVERIFIED: ["unknown", "unknown"],
  PURCHASE_NOT_FOUND: ["unknown", "unknown"], NO_PURCHASE: ["neutral", "minus"], PLAN_SCHEDULED: ["pending", "clock"],
  BILLING_GRACE: ["action", "alert"], BILLING_RETRY: ["warning", "alert"], PLAN_PAUSED: ["pending", "clock"],
};
const ACCESS_SHORT = {
  ACTIVE_FEATURE_USABLE: "Ready to use", ACTIVE_HARDWARE_BLOCKED: "Mount limits features", PLAN_EXCLUDES_FEATURE: "Not in this plan",
  ASSIGNED_PENDING_CONFIRMATION: "Connected, checking", VERIFIED_UNBOUND: "Needs attention", DUPLICATE_PURCHASE: "Already has this plan",
  ALREADY_ASSIGNED: "On another camera", PURCHASE_PENDING: "Waiting for the store", PROVIDER_UNKNOWN: "Still confirming",
  CONFLICTING_EVIDENCE: "Needs support", REFUNDED_OR_REVOKED: "Refunded", PLAN_EXPIRED: "Plan ended", PURCHASE_UNVERIFIED: "Not verified",
  PURCHASE_NOT_FOUND: "Not found", NO_PURCHASE: "No plan", PLAN_SCHEDULED: "Starts soon",
  BILLING_GRACE: "Payment needs attention", BILLING_RETRY: "Payment retrying", PLAN_PAUSED: "Paused",
};
// What the store says about a purchase, in words. Billing states keep the purchase on its camera.
const STORE_SAYS = { verified: "confirmed", pending: "not confirmed yet", refunded: "refunded", unverified: "couldn't verify",
  conflicting: "conflicting", grace: "payment failed, grace period", billing_retry: "payment failed, retrying",
  paused: "paused", expired: "expired" };
const BILLING_STATES = new Set(["grace", "billing_retry", "paused", "expired"]);
const STAGE_ICON = { verified: "check", action: "arrow", waiting: "clock", blocked: "xoct", conflicting: "alert", unknown: "unknown", unchecked: "unknown", not_needed: "minus" };
const CHANNEL_LABEL = { web: "Website", apple: "App Store", google: "Google Play", activation_code: "Activation code", trial: "Trial", simulated_purchase: "In-app purchase (simulated)" };
const NEEDS_ATTENTION = new Set(["VERIFIED_UNBOUND", "ALREADY_ASSIGNED", "DUPLICATE_PURCHASE", "PURCHASE_PENDING", "PROVIDER_UNKNOWN",
  "CONFLICTING_EVIDENCE", "REFUNDED_OR_REVOKED", "PLAN_EXPIRED", "PURCHASE_UNVERIFIED", "ASSIGNED_PENDING_CONFIRMATION", "ACTIVE_HARDWARE_BLOCKED",
  "BILLING_GRACE", "BILLING_RETRY", "PLAN_PAUSED"]);

async function loadAccess() {
  const r = await api("/v1/access");
  if (r.ok) {
    state.access = r.data;
    const ids = r.data.cameras.map((c) => c.device.id);
    if (!ids.includes(state.accessDevice)) {
      const asked = state.accessDevice;
      state.accessDevice = (r.data.cameras.find((c) => NEEDS_ATTENTION.has(c.status)) || r.data.cameras[0])?.device.id;
      // A link to a camera that isn't on this account says so instead of quietly showing another.
      if (asked) toast(`That camera isn't on this family's account. Showing ${deviceName(state.accessDevice)}.`);
    }
  }
  return r;
}

async function loadAccessDetail(device = state.accessDevice) {
  if (!device) return;
  const r = await api(`/v1/access/cameras/${device}`);
  if (r.ok) state.accessDetail = r.data;
}

async function refreshAccess() {
  await loadAccess();
  await loadAccessDetail();
}

function accessPill(status) {
  const [tone, ic] = ACCESS_TONE[status] || ["neutral", "unknown"];
  return `<span class="apill apill-${tone}">${icon(ic, "icon-sm")}${esc(ACCESS_SHORT[status] || status)}</span>`;
}

function stageRail(stages, { compact = false } = {}) {
  return `<ol class="stage-rail${compact ? " compact" : ""}" aria-label="From purchase to access">${stages.map((s) => `<li class="stage stage-${s.state}">
      <span class="stage-dot" aria-hidden="true">${icon(STAGE_ICON[s.state] || "unknown", "icon-sm")}</span>
      <span class="stage-text"><span class="stage-label">${esc(s.label)}</span><span class="stage-state">${esc(s.state_label)}</span>
        ${compact ? "" : `<span class="stage-fact">${esc(s.fact)}</span>`}</span></li>`).join("")}</ol>`;
}

function planText(plan) {
  if (!plan) return "";
  if (plan.state === "checking") return "Confirming a purchase";
  return plan.plan_code === "none" ? "No plan now" : `${plan.plan_label} plan now`;
}

function purchaseLine(p) {
  if (!p) return "";
  return `${PLAN_LABEL[p.plan_code] || p.plan_code} · ${CHANNEL_LABEL[p.channel] || p.channel}`;
}

function homeScreen() {
  const a = state.access;
  if (!a) return `<div class="page-head"><h1 class="display">Your cameras</h1></div><div class="skeleton"></div><div class="skeleton"></div>`;
  const deviceLabel = (id) => a.cameras.find((c) => c.device.id === id)?.device.label;
  const cards = a.cameras.map((c) => {
    const attention = NEEDS_ATTENTION.has(c.status);
    return `<article class="card access-card${attention ? " attention" : ""}" aria-labelledby="cam-${c.device.id}">
      <div class="access-card-head">
        <span class="camera-icon" aria-hidden="true">${icon("camera", "icon-lg")}</span>
        <div class="access-card-title"><h2 id="cam-${c.device.id}">${esc(c.device.label)}</h2>
          <p class="camera-meta">${esc(c.device.mount_label)} · ${esc(planText(c.plan))}</p></div>
        ${accessPill(c.status)}
      </div>
      <p class="access-headline">${esc(c.headline)}</p>
      <p class="access-message">${esc(c.message)}</p>
      ${stageRail(c.stages, { compact: true })}
      <div class="btn-row"><a class="btn ${attention ? "btn-primary" : "btn-secondary"}" href="#/access/${c.device.id}">Check my plan${icon("arrow", "icon-sm")}</a></div>
    </article>`;
  }).join("");
  const purchases = a.purchases_visible && a.purchases.length ? `<section class="card purchases-card" aria-labelledby="purchases-title">
      <h2 id="purchases-title">Purchases on this account</h2>
      <p class="small muted card-sub">Synthetic store records. Each one is connected to at most one camera.</p>
      <ul class="purchase-list">${a.purchases.map((p) => `<li>
        <span class="purchase-what"><strong>${esc(purchaseLine(p))}</strong><span class="mono-ref muted">${esc(p.external_ref)}</span></span>
        <span class="purchase-state">Store says: ${esc(STORE_SAYS[p.state] || p.state)}${p.state === "conflicting" ? ` (${esc(p.conflicting_statuses.join(" / "))})` : ""}</span>
        <span class="purchase-where">${p.binding.bound ? (p.binding.elsewhere ? "Used on a camera outside this family" : `Connected to ${esc(deviceLabel(p.binding.device_id) || p.binding.device_id)}`) : "Not connected to a camera"}</span>
      </li>`).join("")}</ul></section>` : "";
  return `<div class="page-head"><p class="eyebrow">Your family</p><h1 class="display">Your cameras</h1>
      <p class="lede">Paid for a plan, and something doesn't work on a camera? Plans attach to a camera. Each one below shows what was bought, where it is connected, and what works right now, and why.</p></div>
    <section class="card start-here" aria-labelledby="start-title">
      <p class="eyebrow">Start here</p>
      <h2 id="start-title">Parents write: “I paid for this. Why can’t I use it?”</h2>
      <p>The cause differs from family to family: a plan bought but never connected, a code used on the wrong camera, a mount the feature can't use, a renewal the app hasn't seen. In most of them, buying again wouldn't help. This prototype finds the actual cause, and says so when a purchase isn't the answer.</p>
      <div class="btn-row"><button type="button" class="btn btn-primary" data-present="start:recruiter">Watch it work, in about a minute</button>
        <button type="button" class="btn btn-secondary" data-action="compare-open">Same problem. Different cause.</button>
        <a class="btn btn-ghost" href="#/customers">What parents reported</a></div>
    </section>
    <div class="access-grid">${cards}</div>
    ${purchases}
    <section class="card lab-entry" aria-labelledby="lab-title">
      <div><p class="eyebrow">Family evidence lab</p><h2 id="lab-title">Last night, clearly</h2>
      <p class="small muted" style="margin:0">The night briefing with corrections, unobserved time and caregiver acknowledgments.</p></div>
      <a class="btn btn-secondary" href="#/briefing">Open the briefing${icon("arrow", "icon-sm")}</a>
    </section>
    <nav class="card eng-entry" aria-label="For engineers">
      <p class="eyebrow">For engineers</p>
      <div class="btn-row"><a class="btn btn-secondary" href="#/studio">Scenario Studio</a><a class="btn btn-secondary" href="#/growth">Growth Lab</a>
        <a class="btn btn-secondary" href="#/customers">Customer evidence</a><button type="button" class="btn btn-ghost" data-action="xray-open">Engineering X-Ray</button>
        <a class="btn btn-ghost" href="/architecture">System architecture</a></div>
    </nav>`;
}

function accessCameraPicker() {
  const cams = state.access?.cameras || [];
  return `<div class="camera-picker" role="radiogroup" aria-label="Camera">${cams.map((c) => {
    const on = c.device.id === state.accessDevice;
    return `<button type="button" role="radio" aria-checked="${on}" data-action="access-camera" data-device="${c.device.id}" class="camera-card">
      <span class="camera-icon" aria-hidden="true">${icon("camera", "icon-lg")}</span>
      <span class="camera-body"><span class="camera-name">${esc(c.device.label)}</span>
        <span class="camera-meta">${esc(c.device.mount_label)}</span>
        <span class="camera-tags">${accessPill(c.status)}</span></span>
      <span class="camera-check" aria-hidden="true">${on ? `${icon("check", "icon-sm")}Selected` : ""}</span>
    </button>`;
  }).join("")}</div>`;
}

function featureRows(d) {
  return `<ul class="access-features">${d.features.map((f) => {
    const st = f.usable ? ["cell-yes", "check", "Works here"] : f.included && f.blocked_by.includes("hardware") ? ["cell-hw", "device", "Needs a different mount"] : ["cell-no", "minus", "Not in this plan"];
    return `<li><span class="af-name"><strong>${esc(f.label)}</strong><span class="small muted">${esc(f.parent_value)}</span></span>
      <span class="cell ${st[0]}">${icon(st[1], "icon-sm")}${st[2]}</span></li>`;
  }).join("")}</ul>`;
}

function supportCard(d) {
  const p = d.focus_purchase;
  const asked = d.lookedUp;          // what the parent typed: support needs it most when nothing was found
  const summary = [`Camera: ${d.device.label} (${d.device.mount_label})`,
    p ? `Purchase: ${purchaseLine(p)} · ${p.external_ref}` : asked ? `Looked up: ${CHANNEL_LABEL[asked.channel] || asked.channel} · ${asked.ref}` : null,
    `Status: ${d.headline} (${d.status})`, `Checked at ${new Date().toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`].filter(Boolean).join("\n");
  return `<div class="card support-card" role="region" aria-label="What to tell support">
    <h3>What to tell support</h3>
    <p class="small muted" style="margin:0">This prototype doesn't contact anyone. A real app would send this summary with your request.</p>
    <pre class="support-summary">${esc(summary)}</pre>
    <div class="btn-row"><button type="button" class="btn btn-secondary" data-copy="${esc(summary)}">${icon("copy", "icon-sm")}Copy summary</button></div>
  </div>`;
}

function actionButtons(d) {
  const label = {
    connect: [`Connect to ${d.device.label}`, "btn-primary", "access-connect-open"],
    verify: [d.status === "ASSIGNED_PENDING_CONFIRMATION" ? "Check my camera now" : "Check again", d.status === "ASSIGNED_PENDING_CONFIRMATION" ? "btn-primary" : "btn-secondary", "access-verify"],
    check_again: ["Check again", "btn-secondary", "access-check-again"],
    contact_support: ["What to tell support", "btn-secondary", "access-support"],
    mount_guide: ["How to fix the mount", "btn-secondary", "access-mount-guide"],
  };
  const buttons = d.allowed_actions.filter((a) => label[a]).map((a) => `<button type="button" class="btn ${label[a][1]}" data-action="${label[a][2]}">${esc(label[a][0])}</button>`).join("");
  const owner = d.allowed_actions.includes("ask_owner") ? `<p class="small">Only the account owner or an admin can connect plans. Ask Parent A to connect it.</p>` : "";
  return buttons || owner ? `<div class="btn-row">${buttons}</div>${owner}` : "";
}

function verifiedHero(d, check) {
  const limits = d.features.filter((f) => f.included && f.blocked_by.includes("hardware"));
  return `<div class="card verified-card" role="status">
    <div class="verified-head">${icon("check", "icon-lg")}<div><p class="eyebrow" style="margin:0">Access verified · ${clock(check.at)}</p>
      <h2 style="margin:0">It works on the ${esc(d.device.label.toLowerCase())}</h2></div></div>
    <p>${esc(d.message)}</p>
    ${limits.length ? `<div class="alert alert-warning mt-4">${icon("device")}<div><p class="alert-title">Still limited by the mount</p><p>${esc(limits.map((f) => f.label).join(", "))} need the camera above the crib on a Floor Stand or Wall Mount.</p></div></div>` : ""}
    <p class="small muted mt-4" style="margin-bottom:0">Connecting the plan was one step; this check is another. Whether you actually use it is a third, and we don't assume it.</p>
  </div>`;
}

const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const longDate = (iso) => (iso ? dateFmt.format(new Date(iso)) : "no end date");
// The last day with access before an end instant, as the server words it ("through Oct 21").
const lastDay = (iso) => dateFmt.format(new Date(Date.parse(iso) - 1));
const PERIOD_STATE = { active: ["check", "Active"], ended: ["clock", "Ended"], scheduled: ["clock", "Starts"],
  refunded: ["alert", "Refunded"], revoked: ["alert", "Revoked"] };

// The camera's plan periods, newest first. A renewal is a second period of the same purchase.
function periodsCard(d) {
  if (!d.periods?.length) return "";
  const rows = d.periods.map((p) => {
    const [ic, label] = PERIOD_STATE[p.state] || ["unknown", p.state];
    return `<li class="period period-${p.state}">
      <span class="period-dot" aria-hidden="true">${icon(ic, "icon-sm")}</span>
      <span class="period-body"><span class="period-title">${esc(p.plan_label)}${p.trial ? " trial" : ""}${p.renewal ? ` <span class="period-tag">renewal</span>` : ""}${p.grace ? ` <span class="period-tag">grace period</span>` : ""}</span>
        <span class="period-dates">${esc(longDate(p.starts_at))} – ${esc(longDate(p.ends_at))} · ${esc(p.channel)}</span></span>
      <span class="period-state">${esc(p.state === "scheduled" ? `Starts ${longDate(p.starts_at)}`
        : p.grace && p.state === "active" ? `Grace through ${lastDay(p.ends_at)}` : label)}</span></li>`;
  }).join("");
  return `<section class="card" aria-labelledby="periods-title"><h2 id="periods-title">Plan history on this camera</h2>
    <p class="small muted card-sub">Every period the store confirmed for this camera. A renewal is a new period of the same purchase, on the same camera.${d.periods.some((p) => p.grace) ? " A grace period is time the store kept the plan on while it retried a payment." : ""}</p>
    <ol class="periods">${rows}</ol></section>`;
}

// Simulation only: the store's next event for a Scenario Studio purchase, shown
// while the camera is in that scenario's status. A developer control, outside
// the parent's flow; no store is contacted. `from` is the period start in days
// from today; every period runs a year.
const STORE_NEXT = {
  plan_ended: { status: "PLAN_EXPIRED", label: "Store sends the renewal", then: "Then: the store sends the renewal",
    says: "send the store's renewal for this purchase.", from: -5,
    body: { channel: "apple", external_ref: "apple-txn-8008", status: "verified", provider_version: 2 } },
  billing_grace: { status: "BILLING_GRACE", label: "Store recovers the payment", then: "Then: the store recovers the payment",
    says: "send the App Store's billing recovery for this purchase.", from: 0,
    body: { channel: "apple", external_ref: "apple-txn-1101", provider_event: "DID_RENEW/BILLING_RECOVERY", provider_version: 3 } },
  evidence_journey: { status: "PLAN_EXPIRED", label: "Store sends the renewal", then: "Then: the store sends the renewal",
    says: "send the App Store's renewal of Milestones for this purchase.", from: -5, plan: "milestones",
    body: { channel: "apple", external_ref: "apple-txn-1505", provider_event: "DID_RENEW", provider_version: 2 } },
  billing_retry: { status: "BILLING_RETRY", label: "Store recovers the payment", then: "Then: the store recovers the payment",
    says: "send Google Play's recovery notice for this purchase.", from: 0,
    body: { channel: "google", external_ref: "gpa-1202", provider_event: "SUBSCRIPTION_RECOVERED", provider_version: 3 } },
};

// Once the camera's plan works again, the recorded evidence is one step away.
function recordedLink(d) {
  if (!ROUTES.some((r) => r.id === "recorded") || d.status !== "ACTIVE_FEATURE_USABLE") return "";
  return `<a class="card recorded-link" href="#/recorded">${icon("camera")}<span><strong>Recorded evidence</strong>
    <span class="small muted" style="display:block">See what the camera's clip shows and add what you know.</span></span>${icon("arrow", "icon-sm")}</a>`;
}

function storeDemo(d) {
  const next = STORE_NEXT[state.studioRun?.scenario];
  if (!next || d.status !== next.status || d.device.id !== "dev_cam_demo") return "";
  return `<div class="demo-bar" role="region" aria-label="Simulation">
    <span class="dev-chip">simulation</span><span>Not part of the parent app: ${esc(next.says)}</span>
    <button type="button" data-action="studio-renewal">${esc(next.label)}</button></div>`;
}

async function storeNext() {
  const key = state.studioRun?.scenario in STORE_NEXT ? state.studioRun.scenario : "plan_ended";
  const next = STORE_NEXT[key];
  const t = new Date();
  const start = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()) + next.from * 86400000).toISOString();
  const r = await api("/v1/dev/purchase-evidence", { method: "POST", viewer: VIEWERS[0].id, body: {
    ...next.body, plan_code: next.plan || "memories", starts_at: start, days: 365 } });
  log(`store event for ${next.body.external_ref} → ${r.status} ${r.data?.renewed_claim ? "new period " + r.data.renewed_claim : r.data?.code || "no new period"}`);
  if (state.studioRun?.scenario === key && r.ok) {
    // "matches" described the build. After the store's event, show what the server says now.
    state.studioRun.renewed = true;
    state.studioRun.after = await measureScenario(state.studio.find((s) => s.key === key));
  }
  await refreshAccess();
  announce(`${state.accessDetail.device.label}: ${state.accessDetail.headline}.`);
}

function lookupForm() {
  if (!state.access?.purchases_visible) return "";
  const r = state.accessLookup;
  const result = r ? `<div class="lookup-result" role="status">
      <div class="state-row">${accessPill(r.status)}<strong>${esc(r.headline)}</strong></div>
      <p>${esc(r.message)}</p>${stageRail(r.stages)}${actionButtons(r)}
      ${state.accessSupport === "lookup" ? supportCard(r) : ""}</div>` : "";
  return `<section class="card" aria-labelledby="lookup-title"><h2 id="lookup-title">Have a code or a receipt?</h2>
    <p class="small muted card-sub">We check it against this camera. Nothing is connected until you confirm.</p>
    <form class="lookup-form" id="access-lookup">
      <div class="field"><label for="lookup-channel">Where you bought it</label>
        <select id="lookup-channel" name="channel">${["activation_code", "web", "apple", "google"].map((c) => `<option value="${c}"${state.lookupChannel === c ? " selected" : ""}>${CHANNEL_LABEL[c]}</option>`).join("")}</select></div>
      <div class="field"><label for="lookup-ref">Code or order number</label>
        <input id="lookup-ref" name="ref" autocomplete="off" spellcheck="false" required minlength="3" maxlength="120" value="${esc(state.lookupRef || "")}"></div>
      <div class="btn-row"><button class="btn btn-secondary" type="submit">Check this purchase</button></div>
    </form>${result}</section>`;
}

function accessScreen() {
  const d = state.accessDetail;
  if (!state.access || !d) return `<div class="page-head"><h1 class="display">From purchase to access</h1></div><div class="skeleton"></div>`;
  const verified = state.accessVerified?.device === d.device.id && d.status === "ACTIVE_FEATURE_USABLE" ? state.accessVerified.check : null;
  const [tone] = ACCESS_TONE[d.status] || ["neutral"];
  const renewed = d.status === "ACTIVE_FEATURE_USABLE" ? (d.periods || []).find((p) => p.state === "active" && p.renewal) : null;
  const status = verified ? verifiedHero(d, verified) : `<div class="card access-status tone-${tone}" role="status" aria-live="polite">
      <div class="state-row" style="margin-top:0">${accessPill(d.status)}${renewed ? `<span class="apill apill-success">${icon("refresh", "icon-sm")}Renewed · runs to ${esc(longDate(renewed.ends_at))}</span>` : ""}</div>
      <h2 class="access-status-title">${esc(d.headline)}</h2>
      <p class="access-message">${esc(d.message)}</p>
      ${d.connect_preview ? `<dl class="terms"><dt>Would work here</dt><dd>${esc(d.connect_preview.usable.join(", ") || "Nothing on this mount")}</dd>
        ${d.connect_preview.hardware_limited.length ? `<dt>Limited by the mount</dt><dd>${esc(d.connect_preview.hardware_limited.join(", "))}</dd>` : ""}</dl>` : ""}
      ${actionButtons(d)}
    </div>`;
  const extra = state.accessSupport === d.device.id ? supportCard(d)
    : state.accessMountGuide === d.device.id ? `<div class="card"><h3>Fixing the mount</h3><p>Move the ${esc(d.device.label.toLowerCase())} above the crib on a Floor Stand or Wall Mount, then check again. A plan change can't fix this, so we won't suggest one.</p></div>` : "";
  return `<div class="page-head"><p class="eyebrow">Access journey</p><h1 class="display">From purchase to access</h1>
      <p class="lede">Five checks, in order. Each is a separate fact, and we don't skip ahead of what we know.</p></div>
    ${accessCameraPicker()}
    <div class="access-layout">
      <div class="access-main">${status}${extra}${storeDemo(d)}${recordedLink(d)}${periodsCard(d)}
        <section class="card" aria-labelledby="stages-title"><h2 id="stages-title">Purchase → account → camera → plan → feature</h2>${stageRail(d.stages)}</section>
      </div>
      <div class="access-side">
        <section class="card" aria-labelledby="works-title"><h2 id="works-title">On this camera</h2>
          <p class="small muted card-sub">${esc(d.device.mount_label)} · ${esc(planText(d.plan))}</p>${featureRows(d)}</section>
        <div class="btn-row"><button type="button" class="btn btn-ghost" data-action="xray-open">${icon("scan", "icon-sm")}Behind the decision</button></div>
      </div>
    </div>
    ${lookupForm()}`;
}

function openConnectSheet() {
  const d = state.accessDetail;
  const p = d?.focus_purchase;
  if (!p) return;
  const sheet = document.getElementById("access-sheet");
  sheet.dataset.key = "bind-ui-" + crypto.randomUUID();      // one key per attempt; a retry of this attempt reuses it
  sheet.dataset.device = d.device.id;
  sheet.dataset.revision = d.state_revision;
  // The dialog connects the purchase it was opened for. A poll may replace
  // state.accessDetail meanwhile (another tab connected it): the server then
  // refuses the stale revision, and the dialog says so.
  sheet.dataset.channel = p.channel;
  sheet.dataset.ref = p.external_ref;
  sheet.innerHTML = `<form method="dialog" class="sheet-body" id="connect-form">
    <h2 id="access-sheet-title">Connect your plan</h2>
    <dl class="terms">
      <dt>Purchase</dt><dd>${esc(purchaseLine(p))}</dd>
      <dt>Store status</dt><dd>Confirmed by the store</dd>
      <dt>Camera</dt><dd>${esc(d.device.label)} · ${esc(d.device.mount_label)}</dd>
      <dt>Will work here</dt><dd>${esc(d.connect_preview?.usable.join(", ") || "Nothing on this mount")}</dd>
      ${d.connect_preview?.hardware_limited.length ? `<dt>Limited by the mount</dt><dd>${esc(d.connect_preview.hardware_limited.join(", "))}</dd>` : ""}
    </dl>
    <p class="small mt-4"><span class="chip chip-sim">Simulated. Nothing is charged.</span> Connecting doesn't buy anything; it attaches what you already paid for to this camera. It isn't moved later without support.</p>
    <div id="connect-error"></div>
    <div class="btn-row"><button class="btn btn-primary" type="submit">Connect to ${esc(d.device.label)}</button>
      <button class="btn btn-ghost" type="button" data-action="access-sheet-cancel">Cancel</button></div>
  </form>`;
  sheet.showModal();
  sheet.querySelector("button[type=submit]").focus();
  sheet.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); connectPurchase(); });
}

async function connectPurchase() {
  const sheet = document.getElementById("access-sheet");
  const p = { channel: sheet.dataset.channel, external_ref: sheet.dataset.ref };
  const save = sheet.querySelector("button[type=submit]");
  if (save) save.disabled = true;
  const r = await api("/v1/access/bind", { method: "POST", headers: { "Idempotency-Key": sheet.dataset.key },
    body: { device_id: sheet.dataset.device, channel: p.channel, external_ref: p.external_ref, expected_state_revision: Number(sheet.dataset.revision) } });
  log(`POST access/bind ${p.external_ref} → ${sheet.dataset.device}: ${r.status} ${r.data?.code || r.data?.operation || ""}${r.data?.replayed ? " (replayed)" : ""}`);
  if (!r.ok) {
    const stale = r.data?.code === "STALE_ACCESS_VIEW";
    const box = sheet.querySelector("#connect-error");
    if (box) box.innerHTML = `<div class="alert alert-warning mt-4" role="alert">${icon("alert")}<div><p class="alert-title">Not connected</p>
      <p>${esc(stale ? "Your family's plans changed since this screen loaded. Review the latest status, then connect." : r.data?.message || "Nothing was changed.")}</p>
      ${stale ? `<div class="btn-row" style="margin-top:8px"><button class="btn btn-secondary" type="button" data-action="access-sheet-refresh">Show the latest status</button></div>` : ""}</div></div>`;
    if (save) save.disabled = false;
    return;
  }
  sheet.close();
  // Operation completed. The capability is verified by a separate check.
  await refreshAccess();
  render();
  toast(`Connected to ${state.accessDetail.device.label}. Checking the camera…`);
  await verifyAccess();
  showAccessResult();
}

// The access status after an action: scrolled into view, with focus on its heading.
function showAccessResult() {
  const heading = document.querySelector("#main .verified-card h2, #main .access-status-title");
  if (!heading) return;
  heading.tabIndex = -1;
  heading.scrollIntoView({ behavior: smooth(), block: "center" });
  heading.focus({ preventScroll: true });
}

async function verifyAccess() {
  const device = state.accessDevice;
  const r = await api("/v1/access/verify", { method: "POST", body: { device_id: device } });
  log(`POST access/verify ${device}: ${r.status} ${r.data?.check?.kind || r.data?.code || ""} → ${r.data?.check?.status || ""}`);
  if (r.ok) {
    if (r.data.check.status === "ACTIVE_FEATURE_USABLE") state.accessVerified = { device, check: r.data.check };
    announce(`${r.data.access.headline}.`);
  }
  await refreshAccess();
  render();
}

async function lookupPurchase(form) {
  state.lookupChannel = form.channel.value;
  state.lookupRef = form.ref.value.trim();
  if (state.lookupRef.length < 3) {           // the field's minlength counts spaces the trim removes
    toast("Enter at least 3 characters of the code or order number.");
    return;
  }
  const r = await api("/v1/access/check", { method: "POST", body: { device_id: state.accessDevice, channel: state.lookupChannel, external_ref: state.lookupRef } });
  state.accessLookup = r.ok ? { ...r.data, lookedUp: { channel: state.lookupChannel, ref: state.lookupRef } } : null;
  if (!r.ok) toast(r.data?.message || "We couldn't check that purchase.");
  else announce(`${r.data.headline}.`);
  render();
}

// Studio, window and outcome are engineering surfaces over the same API.

async function loadStudio() {
  if (!state.studio) {
    const r = await api("/v1/dev/access-scenarios");
    if (r.ok) state.studio = r.data;
  }
}

function deviceName(id) {
  return state.access?.cameras.find((c) => c.device.id === id)?.device.label || id;
}

function studioScreen() {
  const list = state.studio || [];
  const run = state.studioRun;
  const cards = list.map((s) => {
    const active = run?.scenario === s.key;
    const got = active ? run.actual : null;
    const after = active ? run.after : null;
    return `<article class="card studio-card${active ? " active" : ""}" aria-labelledby="sc-${s.key}">
      <h2 id="sc-${s.key}">${esc(s.title)}</h2>
      <p class="small">${esc(s.story)}</p>
      <ol class="studio-steps">${s.steps.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>
      <ul class="studio-expect">${Object.entries(s.expected).map(([dev, st]) => `<li><span>${esc(deviceName(dev))}${s.focus.external_ref && s.focus.device_id === dev ? ` · checking ${esc(s.focus.external_ref)}` : ""}</span>
        <span class="expect-pair">${accessPill(st)}${got ? (got[dev] === st ? `<span class="st-ok small">${icon("check", "icon-sm")}matches</span>` : `<span class="st-changed small">got ${esc(got[dev])}</span>`) : ""}</span>
        ${after ? `<span class="expect-after small">${s.key === "worker_crash" ? "Now" : "After the store's event"}: ${accessPill(after[dev])}</span>` : ""}</li>`).join("")}</ul>
      ${active && run.workerNote ? `<p class="small" role="status">${esc(run.workerNote)}</p>` : ""}
      <p class="small muted">${esc(s.resolution)}</p>
      <div class="btn-row"><button type="button" class="btn ${active ? "btn-ghost" : "btn-secondary"}" data-action="studio-run" data-key="${s.key}">${active ? "Rebuild" : "Build this scenario"}</button>
        ${active ? `<a class="btn btn-ghost" href="#/access/${s.focus.device_id}">Open the parent journey${icon("arrow", "icon-sm")}</a>` : ""}
        ${active && s.key === "evidence_journey" && ROUTES.some((r) => r.id === "recorded") ? `<a class="btn btn-ghost" href="#/recorded">Open the recorded evidence${icon("arrow", "icon-sm")}</a>` : ""}</div>
      ${active && STORE_NEXT[s.key] ? (run.renewed ? `<p class="small muted">The store's event was applied. Rebuild to start the scenario again.</p>`
        : `<div class="btn-row"><button type="button" class="btn btn-secondary" data-action="studio-renewal">${esc(STORE_NEXT[s.key].then)}</button></div>`) : ""}
      ${active && s.key === "worker_crash" ? `<div class="btn-row"><button type="button" class="btn btn-secondary" data-action="studio-worker" data-crash="1">Run worker: crash after provider</button>
        <button type="button" class="btn btn-secondary" data-action="studio-worker">Restart worker</button></div>` : ""}
    </article>`;
  }).join("");
  return `<div class="page-head"><p class="eyebrow">For engineers <span class="chip chip-sim">synthetic</span></p><h1 class="display">Scenario Studio</h1>
      <p class="lede">Purchase-to-access situations, each built through the app's own code paths, then checked against the status the server returns.</p>
      <div class="btn-row"><a class="btn btn-secondary" href="#/customers">Customer evidence${icon("arrow", "icon-sm")}</a><a class="btn btn-secondary" href="#/growth">Growth Lab${icon("arrow", "icon-sm")}</a></div></div>
    ${list.length ? compareCard() : ""}
    <div class="studio-grid">${cards || `<div class="card"><p class="muted" style="margin:0">Scenario controls are off on this server.</p></div>`}</div>
    ${accessMap()}`;
}

const ACTION_WORDS = { connect: "Connect the purchase to this camera", verify: "Check the camera again", check_again: "Wait, then check again",
  contact_support: "Contact support with a summary", mount_guide: "See how to move the camera" };
const DIFFERS_WORDS = { status: "the status", reason: "the cause", allowed_actions: "what the parent can do",
  usable_features: "what works", offer: "whether a plan is offered", stages: "what was checked" };
const STATE_WORD = { verified: "Verified", action: "Needs an action", waiting: "Waiting", blocked: "Blocked", conflicting: "Conflicting",
  unknown: "Not yet known", not_needed: "Not needed", unchecked: "Not checked yet" };
const PROBE_WORDS = { PAYMENT_RETRYING: "the store is still retrying the payment", HARDWARE_INCOMPATIBLE: "a plan can't fix the mount",
  MOUNT_UNSUPPORTED: "a plan can't fix the mount" };
const probeAction = (a) => a.replace(/^Buy a plan for nextnap$/i, "Sell a plan for NextNap").replace(/^Connect the (\w+) purchase to this camera$/,
  (_, ch) => `Connect the ${CHANNEL_LABEL[ch] || ch} purchase again`);
function probeAnswer(p) {
  if ("offer_made" in p) return p.offer_made ? "Offered a plan" : `No offer: ${p.blockers.map((b) => PROBE_WORDS[b] || b.toLowerCase().replaceAll("_", " ")).join(", ")}`;
  if (p.refused) return `Refused: ${(PROBE_WORDS[p.code?.replace(/^PURCHASE_/, "")] || p.code?.toLowerCase().replaceAll("_", " ") || "not allowed")}`;
  return p.answer?.startsWith("already") ? "Already on this camera. Nothing changes" : "Connected";
}

// Same symptom, two causes: two real builds, the backend's decision in each,
// and the backend's answer to the same two unsafe attempts.
const COMPARABLE = ["billing_retry", "incompatible_hardware", "plan_ended", "paid_not_active", "billing_grace",
  "access_lost_after_refund", "purchase_uncertain", "paid_wrong_camera", "renewal_reflected"];

function compareCard() {
  const c = state.compare;
  const pick = (id, chosen) => `<select id="${id}">${COMPARABLE.map((k) => `<option value="${k}"${k === chosen ? " selected" : ""}>${esc((state.studio || []).find((s) => s.key === k)?.title || k)}</option>`).join("")}</select>`;
  // Each side tells the same five-step story, from the server's own answer:
  // what the parent sees, what the backend found, what it allows, what it
  // refuses (run for real, then rolled back), and what works now.
  const side = (x) => `<div class="cmp-side">
      <p class="eyebrow">${esc(x.title)}</p>
      <ol class="cmp-steps">
        <li><h3>What the parent sees</h3>
          <div class="state-row">${accessPill(x.status)}<strong>${esc(x.headline)}</strong></div>
          <p class="small">${esc(x.message)}</p></li>
        <li><h3>What the backend found</h3>
          <ul class="cmp-facts small">${x.stages.map((st) => `<li><span class="stage-dot st-${esc(st.state)}" aria-hidden="true"></span><span><strong>${esc(st.label)}.</strong> ${esc(st.fact || STATE_WORD[st.state] || st.state)}</span></li>`).join("")}</ul></li>
        <li><h3>What it lets the parent do</h3>
          <p class="small">${esc(x.allowed_actions.map((a) => ACTION_WORDS[a] || a).join(". ") || "Nothing on this screen. No purchase is offered")}.</p></li>
        <li><h3>If the parent tries anyway</h3>
          <ul class="probe-list small">${x.probes.map((p) => `<li><span>${esc(probeAction(p.action))}</span><strong class="${p.refused || p.offer_made === false ? "st-ok" : "st-none"}">${esc(probeAnswer(p))}</strong></li>`).join("")}</ul></li>
        <li><h3>What works on the camera now</h3>
          <p class="small">${esc(x.usable_features.map(featureLabel).join(", ") || "Nothing")}.</p></li>
      </ol>
      <details class="cmp-eng small"><summary>Engineering detail</summary>
        <dl class="facts"><dt>Decision</dt><dd><code>${esc(x.status)}</code> · <code>${esc(x.reason)}</code></dd>
          <dt>Probe answers</dt><dd>${x.probes.map((p) => `<code>${esc(p.answer)}</code>`).join(" ")}</dd>
          <dt>Fingerprint</dt><dd><code>${esc(x.fingerprint)}</code> at revision ${esc(x.state_revision)}</dd>
          <dt>Written by the probes</dt><dd>${x.probes_persisted ? "rows were written (unexpected)" : "nothing: each ran inside a transaction that was rolled back"}</dd></dl>
      </details></div>`;
  return `<section class="card cmp-card" id="compare" aria-labelledby="cmp-title"><p class="eyebrow">The standout case</p><h2 id="cmp-title">Same problem. Different cause.</h2>
    <p class="small muted card-sub">Two parents write the same thing: "NextNap doesn't work." The backend builds both situations for real, finds a different cause in each, and allows and refuses different things. Pick two and compare.</p>
    <div class="cmp-pick"><label>First ${pick("cmp-a", state.comparePick?.a || c?.a?.scenario || "billing_retry")}</label>
      <label>Second ${pick("cmp-b", state.comparePick?.b || c?.b?.scenario || "incompatible_hardware")}</label>
      <button type="button" class="btn btn-primary" data-action="studio-compare">Compare</button></div>
    ${state.compareError ? `<p class="small" role="alert">${esc(state.compareError)}</p>` : ""}
    ${c ? `<div class="cmp-grid" role="status">${side(c.a)}${side(c.b)}</div>
      <p class="small">The backend's answers differ in: <strong>${esc(c.differs_in.map((k) => DIFFERS_WORDS[k] || k).join(", ") || "nothing")}</strong>. ${esc(c.note)}</p>` : ""}</section>`;
}

async function runCompare() {
  state.compareError = null;
  const a = document.getElementById("cmp-a").value, b = document.getElementById("cmp-b").value;
  state.comparePick = { a, b };                  // the choices stay as picked, whatever the outcome
  state.compare = null;                          // the old result isn't shown as if it were the new one
  const grid = document.querySelector(".cmp-grid");
  if (grid) grid.outerHTML = `<p class="small muted" role="status">Building both cases on the backend…</p>`;
  if (a === b) { state.compare = null; state.compareError = "Choose two different scenarios to compare."; return; }
  state.resetting = true;                        // both builds reset the demo: not "reset elsewhere"
  const r = await api("/v1/dev/compare", { method: "POST", body: { a, b }, viewer: VIEWERS[0].id });
  try { if (r.ok) await afterReset(); } finally { state.resetting = false; }
  if (r.ok) { state.compare = r.data; state.studioRun = null; log(`compare ${a} vs ${b} → ${r.data.a.status} / ${r.data.b.status}`); }
  else { state.compare = null; state.compareError = "The comparison didn't run. " + (r.data?.message || ""); }
}

// Expected (what the store says) vs actual (what the camera record says) vs
// effective (what the camera can do), per purchase.
function accessMap() {
  const a = state.access;
  if (!a?.purchases_visible) return "";
  const cam = (id) => a.cameras.find((c) => c.device.id === id);
  const rows = a.purchases.map((p) => {
    const c = p.binding.device_id ? cam(p.binding.device_id) : null;
    const actual = p.binding.bound ? (p.binding.elsewhere ? "outside this family" : `${deviceName(p.binding.device_id)}: ${p.binding.claim_statuses.join(" + ")}`) : "no camera claim";
    const effective = c ? `${planText(c.plan)} · ${c.usable_features.length ? c.usable_features.map(featureLabel).join(", ") : "nothing usable"}` : "—";
    // A billing state keeps the purchase on its camera by design: that is consistent, not an error.
    const agree = (p.state === "verified" && p.binding.bound && p.binding.claim_statuses.join() === "active")
      || (BILLING_STATES.has(p.state) && p.binding.bound && !p.binding.claim_statuses.includes("revoked"))
      || (p.state === "refunded" && p.binding.claim_statuses.includes("revoked")) || (!p.binding.bound && p.state !== "verified");
    return `<tr><th scope="row">${esc(purchaseLine(p))}<span class="mono-ref muted" style="display:block">${esc(p.external_ref)}</span></th>
      <td>${esc(p.state)} · v${p.provider_version}</td><td>${esc(actual)}</td><td>${esc(effective)}</td>
      <td>${agree ? `<span class="st-ok">${icon("check", "icon-sm")}consistent</span>` : `<span class="st-changed">${icon("alert", "icon-sm")}needs action</span>`}</td></tr>`;
  }).join("");
  return `<section class="card plans-section" aria-labelledby="map-title"><h2 id="map-title">Cross-channel access map</h2>
    <p class="small muted card-sub">What the store says, what the camera record says, and what the camera can actually do. Trials and in-app purchases are recorded straight on a camera and are not listed.</p>
    <div class="table-scroll" tabindex="0" role="region" aria-label="Cross-channel access map"><table class="plain compare">
      <thead><tr><th scope="col">Purchase</th><th scope="col">Expected (store)</th><th scope="col">Actual (camera record)</th><th scope="col">Effective (camera can do)</th><th scope="col">Agreement</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="muted">No store purchases on this account.</td></tr>`}</tbody></table></div></section>`;
}

// The server's status for each camera a scenario expects something of.
async function measureScenario(scenario) {
  const actual = {};
  for (const dev of Object.keys(scenario.expected)) {
    const focused = scenario.focus.external_ref && scenario.focus.device_id === dev;
    const s = focused ? await api("/v1/access/check", { method: "POST", viewer: VIEWERS[0].id, body: { device_id: dev, channel: scenario.focus.channel, external_ref: scenario.focus.external_ref } })
      : await api(`/v1/access/cameras/${dev}`, { viewer: VIEWERS[0].id });
    actual[dev] = s.data?.status;
  }
  return actual;
}

async function runStudio(key) {
  state.resetting = true;
  const r = await api("/v1/dev/access-scenario", { method: "POST", body: { scenario: key }, viewer: VIEWERS[0].id });
  if (!r.ok) { state.resetting = false; toast(r.data?.message || "The scenario couldn't be built."); return; }
  try { await afterReset(); } finally { state.resetting = false; }
  const scenario = state.studio.find((s) => s.key === key);
  const actual = await measureScenario(scenario);
  state.studioRun = { scenario: key, actual, log: r.data.log };
  state.accessDevice = scenario.focus.device_id;
  if (scenario.focus.external_ref) {
    Object.assign(state, { lookupChannel: scenario.focus.channel, lookupRef: scenario.focus.external_ref });
    const s = await api("/v1/access/check", { method: "POST", viewer: VIEWERS[0].id, body: { device_id: scenario.focus.device_id, channel: scenario.focus.channel, external_ref: scenario.focus.external_ref } });
    state.accessLookup = s.ok ? s.data : null;
  }
  await loadAccessDetail();
  r.data.log.forEach((l) => log(`scenario ${key}: ${l.step}`));
  announce(`${scenario.title}: built.`);
}

// Everything that cached ids from the old seed is reloaded.
async function afterReset() {
  observer.disconnect();
  state.receipts.clear();
  state.resetElsewhere = false;
  Object.assign(state, { decision: null, purchase: null, purchaseKey: null, purchaseNote: null, decisionNote: null, crashNext: false,
    accessVerified: null, accessLookup: null, accessSupport: null, accessMountGuide: null, window: null, outcome: null });
  await loadNight();
  await Promise.all([refreshLatest(), loadCommercial({ restorePurchase: true }), refreshAccess()]);
}

async function studioWorker(crash) {
  const w = await api("/v1/dev/worker", { method: "POST", body: { crash_after_provider: crash } });
  log(`worker ${crash ? "(crash after provider) " : ""}exit=${w.data?.exit_code}${w.data?.crashed ? ": died between the provider commit and the app commit" : ""}`);
  await refreshAccess();
  const s = state.accessDetail;
  if (state.studioRun) {
    state.studioRun.after = await measureScenario(state.studio.find((x) => x.key === state.studioRun.scenario));
    state.studioRun.workerNote = w.data?.crashed
      ? `The worker process exited (code ${w.data.exit_code}) after the provider recorded the charge and before the app did.`
      : `The worker ran (exit code ${w.data?.exit_code ?? "?"}).${/reconciled/.test(w.data?.stdout || "") ? " It found the provider's record and finished the purchase once." : ""}`;
  }
  announce(`${s.device.label}: ${s.headline}.`);
}

async function loadGrowth() {
  const device = state.accessDevice;
  if (!device) return;
  const [w, o, p, f] = await Promise.all([api(`/v1/access/cameras/${device}/window`), api(`/v1/access/cameras/${device}/outcome`),
    api("/v1/proof"), api("/v1/access/metrics")]);
  state.window = w.ok ? w.data : null;
  state.outcome = o.ok ? o.data : null;
  state.funnel = f.ok ? f.data : null;
  if (p.ok) state.certification = p.data.certification;
}

// Seconds as a reader says them; record times, not estimates.
function duration(s) {
  if (s === null || s === undefined) return "not yet";
  if (s < 60) return `${Math.round(s * 10) / 10} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

// Purchase to use, five steps apart: each counted from its own rows, over the
// same denominator, never inferred from the step before.
function ladderCard(l) {
  if (!l) return "";
  const of = l.steps[0]?.of || 0;
  const bar = (s) => {
    const share = of ? Math.round((s.reached / of) * 100) : 0;
    return `<li class="ladder-step">
      <div class="ladder-head"><span>${esc(s.label)}</span><strong>${s.reached} of ${s.of}</strong></div>
      <div class="ladder-track" aria-hidden="true"><span style="width:${share}%"></span></div>
      <p class="small muted">${esc(s.source)}${s.median_s_from_previous != null ? ` · median ${esc(duration(s.median_s_from_previous))} after the step before` : ""}</p></li>`;
  };
  return `<h3 class="mt-6">From purchase to use, five steps apart</h3>
    <p class="small muted">Each step counts only purchases that reached the step before it. Denominator: ${esc(l.denominator)}. A successful API response is step 1 or 2; the customer value is steps 4 and 5.</p>
    <ol class="ladder">${l.steps.map(bar).join("")}</ol>`;
}

function funnelCard() {
  const f = state.funnel;
  if (!f) return "";
  const t = f.time_to_usable, b = f.billing_recovery, v = f.verification_outcomes;
  const refused = Object.entries(f.refused_before_payment);
  const row = (label, value, note = "") => `<li><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<span class="small muted">${esc(note)}</span>` : ""}</li>`;
  return `<section class="card" aria-labelledby="funnel-title"><h2 id="funnel-title">Access funnel <span class="chip chip-sim">synthetic</span></h2>
    <p class="small muted card-sub">From this family's stored records, computed when you open this page. ${esc(f.caveat)}</p>
    ${ladderCard(f.ladder)}
    <h3 class="mt-6">Timings and exceptions</h3>
    <ul class="funnel">
      ${row("Store confirmed → a check found a feature usable", t.confirmed_to_usable.count ? `median ${duration(t.confirmed_to_usable.median_s)}` : "none yet", `${t.confirmed_to_usable.count} of ${t.journeys.length} purchases`)}
      ${row("Connected → first usable check", t.connected_to_usable.count ? `median ${duration(t.connected_to_usable.median_s)}` : "none yet", `${t.connected_to_usable.count} reached a usable check`)}
      ${row("Paid, not usable yet", String(f.unresolved.count), f.unresolved.count ? `${f.unresolved.not_connected} not connected · oldest ${duration(f.unresolved.oldest_s)}` : "")}
      ${row("Renewal payment failed → recovered", `${b.recovered} of ${b.purchases.length}`, b.failure_to_recovery.count ? `median ${duration(b.failure_to_recovery.median_s)} to recover` : "")}
      ${row("Refused before any payment", String(refused.reduce((n, [, c]) => n + c, 0)), refused.map(([code, c]) => `${code} ${c}`).join(" · "))}
      ${row("Access checks that found a usable feature", `${v.usable} of ${v.checks}`, Object.entries(v.by_status).filter(([s]) => s !== "ACTIVE_FEATURE_USABLE").map(([s, c]) => `${ACCESS_SHORT[s] || s} ${c}`).join(" · "))}
    </ul>
    ${f.video ? `<h3 class="mt-6">Recorded evidence</h3><p class="small muted">${esc(f.video.source)}. Each line is its own event.</p><ul class="funnel">
      ${row("Video history verified for the evidence view", `${f.video.access.checked_allowed}`, `refused ${f.video.access.checked_refused}`)}
      ${row("Evidence view opened after it rendered", `${f.video.access.evidence_opened}`)}
      ${row("Clip analyses completed", `${f.video.runs.completed}`, `failed ${f.video.runs.failed}${Object.keys(f.video.runs.failure_codes).length ? " (" + Object.entries(f.video.runs.failure_codes).map(([c, n]) => `${c} ${n}`).join(", ") + ")" : ""} · in progress ${f.video.runs.in_progress}`)}
      ${row("Analysis time", f.video.runs.analysis_s.count ? `median ${duration(f.video.runs.analysis_s.median_s)}` : "none yet")}
      ${row("Intervals without enough evidence", `${f.video.intervals.without_enough_evidence} of ${f.video.intervals.measured}`)}
      ${row("Reports added by the family", `${f.video.reports.added}`, `corrections ${f.video.reports.corrections}`)}
    </ul>` : ""}</section>`;
}

const VERDICT = { still_verified: ["st-ok", "check", "still verified"], changed: ["st-changed", "alert", "changed"], access_lost: ["st-changed", "xoct", "access lost"] };

function windowCard() {
  const w = state.window;
  if (!w) return `<div class="card"><p class="muted" style="margin:0">Owners and admins can see the verification window.</p></div>`;
  const tone = { HOLDING: "alert-success", CHANGED: "alert-warning", ACCESS_LOST: "alert-warning", NOT_STARTED: "alert-unknown", NOTHING_USABLE: "alert-unknown" }[w.decision.state];
  const strip = w.observations.map((o) => { const v = VERDICT[o.verdict.state]; return `<li class="obs obs-${o.verdict.state}" title="${esc(o.kind)} ${clock(o.at)}">
    <span class="obs-kind">${esc(o.kind)}</span><span class="${v[0]}">${icon(v[1], "icon-sm")}${v[2]}</span><span class="small muted">${clock(o.at)} · ${esc(o.usable_features.map(featureLabel).join(", ") || "nothing usable")}</span></li>`; }).join("");
  const purchase = state.access?.purchases.find((p) => p.binding.device_id === state.accessDevice && p.state === "verified");
  const other = state.access?.cameras.find((c) => c.device.id !== state.accessDevice)?.device;
  const ev = (event, label, extra = "") => `<button type="button" class="btn btn-secondary" data-action="window-event" data-event="${event}" ${extra}>${esc(label)}</button>`;
  return `<section class="card" aria-labelledby="window-title"><h2 id="window-title">Verification window</h2>
    <p class="small muted card-sub">After access is verified, later events are checked against what was verified.</p>
    <div class="alert ${tone}">${icon(w.decision.state === "HOLDING" ? "check" : ["NOT_STARTED", "NOTHING_USABLE"].includes(w.decision.state) ? "unknown" : "alert")}<div><p class="alert-title">${esc({ HOLDING: "Holding", CHANGED: "Changed since verification", ACCESS_LOST: "Access lost", NOT_STARTED: "Not verified yet", NOTHING_USABLE: "Nothing usable to hold" }[w.decision.state])}</p><p>${esc(w.decision.reason)}</p></div></div>
    ${w.activation ? `<p class="small mt-4">Activation check ${clock(w.activation.at)}: ${esc(ACCESS_SHORT[w.activation.status] || w.activation.status)} · ${esc(w.activation.usable_features.map(featureLabel).join(", ") || "nothing usable")}</p>` : ""}
    <ol class="obs-strip">${strip}</ol>
    <p class="small">Live now: ${esc(ACCESS_SHORT[w.live.status] || w.live.status)}${w.live.changed_since_last_check ? " · changed since the last check" : ""}</p>
    <h3 class="mt-6">Apply an event</h3>
    <div class="btn-row">${ev("recheck", "Recheck now")}${ev("mount-flex", "Camera moved to a Flex Stand")}${ev("mount-floor", "Camera back on the Floor Stand")}
      ${purchase ? ev("refund", "Store refunds the purchase", `data-ref="${esc(purchase.external_ref)}" data-channel="${purchase.channel}" data-plan="${purchase.plan_code}" data-version="${purchase.provider_version + 1}"`) : ""}
      ${purchase && other ? ev("reassign", `Try connecting it to the ${other.label.toLowerCase()}`, `data-ref="${esc(purchase.external_ref)}" data-channel="${purchase.channel}" data-other="${other.id}"`) : ""}</div>
    ${state.windowNote ? `<p class="small mt-4" role="status">${esc(state.windowNote)}</p>` : ""}
  </section>`;
}

function outcomeCard() {
  const o = state.outcome;
  if (!o) return "";
  return `<section class="card" aria-labelledby="outcome-title"><h2 id="outcome-title">Outcome review</h2>
    <p class="small muted card-sub">Three outcomes, three kinds of evidence. None is inferred from another.</p>
    <ol class="tiers">${o.tiers.map((t) => `<li class="tier tier-${t.state}"><span class="tier-dot" aria-hidden="true">${icon(t.state === "observed" ? "check" : "unknown", "icon-sm")}</span>
      <span><strong>${esc(t.label)}</strong><span class="small muted" style="display:block">${t.state === "observed" ? `Observed ${clock(t.at)}` : esc(t.missing)}</span></span></li>`).join("")}</ol>
    <p class="small muted">${esc(o.caveat)}</p>
    <div class="btn-row"><button type="button" class="btn btn-secondary" data-action="outcome-open">Parent opens NextNap (synthetic click)</button></div>
  </section>`;
}

function certificationCard() {
  const c = state.certification;
  if (!c) return "";
  const st = { passed: ["st-ok", "check", "passed"], failed: ["st-changed", "xoct", "failed"], not_run: ["st-none", "unknown", "not run"], skipped: ["st-none", "minus", "skipped"] };
  const pill = (s) => `<span class="${st[s][0]} cert-state">${icon(st[s][1], "icon-sm")}${st[s][2]}</span>`;
  const open = state.certOpen;
  return `<section class="card" aria-labelledby="cert-title"><h2 id="cert-title">Growth reliability certification</h2>
    <p class="small muted card-sub">${esc(c.origin)}</p>
    ${c.build ? `<p class="small build-line">Running code <code>${esc(c.build.commit || "unknown")}</code>${c.build.uncommitted_app_changes ? " (with uncommitted changes)" : ""} · test report from ${esc((c.build.report_generated_at || "never").slice(0, 16).replace("T", " "))}${c.build.report_commit ? ` on <code>${esc(c.build.report_commit)}</code>` : ""}${c.build.report_matches_code ? " · ran on exactly this code" : ` · <strong class="st-changed">${c.build.report_matches_code === false ? "ran on different code" : "doesn't say which code it ran on"}: rerun before trusting it</strong>`}</p>` : ""}
    <div class="alert ${c.verdict === "CERTIFIED" ? "alert-success" : "alert-warning"}">${icon(c.verdict === "CERTIFIED" ? "check" : "alert")}<div>
      <p class="alert-title">${c.verdict === "CERTIFIED" ? "Certified: every blocking invariant passed on this code" : c.verdict === "UNVERIFIED" ? "Not verified: the test report wasn't produced on this code" : `Blocked by ${c.blocked_by.length} invariant${c.blocked_by.length === 1 ? "" : "s"}`}</p>
      <p>${esc(c.scope)}</p></div></div>
    <ul class="cert-list">${c.invariants.map((i) => `<li><button type="button" class="cert-row" data-action="cert-open" data-id="${i.id}" aria-expanded="${open === i.id}">
        ${pill(i.state)}<span>${esc(i.statement)}</span></button>
      ${open === i.id ? `<div class="cert-detail"><p class="small"><strong>Enforced in</strong></p><ul class="small">${i.code.map((r) => `<li><code>${esc(r.exists ? `${r.file}:${r.line}` : `${r.ref} (missing)`)}</code></li>`).join("")}</ul>
        <p class="small"><strong>Proved by</strong></p><ul class="small">${i.tests.map((t) => `<li>${pill(t.state)} <code>${esc(t.name)}</code>${t.cases > 1 ? ` (${t.cases} cases)` : ""}</li>`).join("")}</ul></div>` : ""}</li>`).join("")}</ul>
    <h3 class="mt-6">Release gates</h3>
    <ul class="cert-list">${c.release_gates.map((g) => `<li><div class="cert-row static">${pill(g.state)}<span>${esc(g.statement || g.id)}${g.measured ? `<span class="small muted" style="display:block">${g.measured.requests} requests · ${g.measured.client_timeouts} client timeouts · p95 ${g.measured.p95_ms} ms · ${esc(g.file)}</span>` : ""}
      ${g.note ? `<span class="small" style="display:block">${esc(g.note)}</span>` : ""}</span></div></li>`).join("")}</ul>
  </section>`;
}

function growthScreen() {
  const cams = state.access?.cameras || [];
  return `<div class="page-head"><p class="eyebrow">For engineers</p><h1 class="display">Growth Lab</h1>
      <p class="lede">Which rules must pass before a release, and did they? Did access hold after it was verified? What do we actually know about the outcome?</p></div>
    <div class="segmented" role="radiogroup" aria-label="Camera">${cams.map((c) => `<button type="button" role="radio" aria-checked="${c.device.id === state.accessDevice}" data-action="growth-camera" data-device="${c.device.id}">${esc(c.device.label)}</button>`).join("")}</div>
    <div class="mt-6">${certificationCard()}</div>
    <div class="growth-grid mt-6"><div>${windowCard()}</div><div>${outcomeCard()}</div></div>
    ${funnelCard()}`;
}

// ------------------------------------------------------------------ recorded evidence (video)
// One real clip, measured by the worker from decoded frames. The screen shows
// what was measured, what is missing, and what people reported, from stored
// rows. It never says whether the baby slept.

const KIND = {
  movement: ["Movement measured", "check", "kind-movement"],
  small_change: ["Small change (uncertain)", "unknown", "kind-small"],
  no_significant_movement: ["No significant movement", "minus", "kind-still"],
  camera_motion: ["Camera moved", "alert", "kind-camera"],
  low_quality: ["Too dark or blurred to measure", "unknown", "kind-quality"],
  insufficient_evidence: ["Not enough video", "unknown", "kind-missing"],
};
const LABEL_SAYS = {
  caregiver_present: "A caregiver was beside the crib", infant_moving: "The baby was moving",
  infant_still: "The baby was still", camera_moved: "The camera was moved", not_sure: "Not sure what happened here",
};
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

async function loadRecorded({ keepSelection = false } = {}) {
  const v = state.video || (state.video = {});
  const list = await api("/v1/video/assets");
  v.list = list.ok ? list.data : null;
  const asset = v.list?.assets.find((a) => a.device_id === (state.accessDevice || "dev_cam_demo")) || v.list?.assets[0];
  // Coming back to the same clip keeps the selected interval; another clip starts fresh.
  const sameClip = v.assetId && v.assetId === asset?.id;
  v.assetId = asset?.id || null;
  v.view = null; v.refused = null;
  if (!keepSelection) { v.note = null; v.result = null; v.play = null; if (!sameClip) v.sel = null; }
  if (!asset) return;
  const r = await api(`/v1/video/assets/${asset.id}`);
  if (r.ok) v.view = r.data;
  if (v.view && v.sel != null && !v.view.observations.some((o) => o.seq === v.sel)) v.sel = null;
  else if (r.data?.code === "FEATURE_NOT_IN_PLAN") v.refused = r.data.detail?.gate || { label: "7-day video history" };
  const done = ["COMPLETED", "FAILED"].includes(v.view?.run?.state);
  if (v.view && v.view.run?.state === "COMPLETED" && (v.mediaFor !== `${asset.id}:${state.viewer}` || Date.now() / 1000 > v.mediaExpires - 30)) {
    const link = await api(`/v1/video/assets/${asset.id}/media-link`, { method: "POST" });
    if (link.ok) { v.mediaUrl = link.data.url; v.mediaExpires = link.data.expires_at; v.mediaFor = `${asset.id}:${state.viewer}`; }
  }
  clearTimeout(v.timer);
  if (v.view && !done && state.route === "recorded") v.timer = setTimeout(async () => { await loadRecorded({ keepSelection: true }); render(); }, 1500);
}

function recordedScreen() {
  const v = state.video || {};
  const head = `<div class="page-head"><p class="eyebrow">Your family</p><h1 class="display">Recorded evidence</h1>
    <p class="lede">What the video shows, what it can't show, and what your family said about it. Measured from real frames; nothing here says whether your baby slept.</p></div>`;
  if (!v.assetId) {
    return head + `<section class="card"><h2>No clip on this camera yet</h2>
      <p>The demo uses one sample clip. It is stored privately and measured by the worker, not in your browser.</p>
      <div class="demo-bar" role="region" aria-label="Simulation"><span class="dev-chip">simulation</span><span>Not part of the parent app: store the demo clip and queue its analysis.</span>
        <button type="button" data-action="video-sample">Load the demo clip</button></div></section>`;
  }
  if (v.refused) {
    return head + `<section class="card access-status" aria-labelledby="gate-title"><div class="state-row">${accessPill("PLAN_EXCLUDES_FEATURE")}</div>
      <h2 id="gate-title">${esc(v.refused.label)} isn't on this camera's plan</h2>
      <p>Recorded evidence rests on ${esc(v.refused.label.toLowerCase())}. This camera has ${esc(v.refused.plan_label || "no plan")}. ${esc(v.refused.blockers?.[0]?.parent_message || "")}</p>
      <p class="small muted">If you paid for it, the access journey shows where it stands. Nothing here sells a plan.</p>
      <div class="btn-row"><a class="btn btn-primary" href="#/access/${esc(state.accessDevice || "dev_cam_demo")}">Check my plan${icon("arrow", "icon-sm")}</a></div>
      <div class="demo-bar" role="region" aria-label="Simulation"><span class="dev-chip">simulation</span><span>Not part of the parent app: build the scenario where this camera's plan, renewed by the store, includes recorded history and the clip is stored.</span>
        <button type="button" data-action="evidence-scenario">Build it</button></div></section>`;
  }
  const d = v.view;
  if (!d) return head + `<section class="card"><p class="muted">Loading the evidence…</p></section>`;
  const run = d.run;
  return head + `<div class="clip-grid">
      <section class="card clip-card" aria-labelledby="clip-title"><h2 id="clip-title" class="sr-only">The clip</h2>
        ${run?.state === "COMPLETED" && v.mediaUrl
          ? `<video id="clip" class="clip-video" controls muted playsinline preload="metadata" src="${esc(v.mediaUrl)}" aria-describedby="clip-caption"></video>`
          : `<div class="clip-placeholder">${icon("camera", "icon-lg")}<span>${run?.state === "FAILED" ? "The clip couldn't be analysed" : "The clip plays once it has been analysed"}</span></div>`}
        <p id="clip-caption" class="small clip-caption">${esc(d.asset.provenance.caption)}</p>
        ${runStatus(run)}
        ${run?.state === "COMPLETED" ? clipSummary(d) : ""}
      </section>
      <section class="card explain-card" aria-labelledby="explain-title" aria-live="polite">${explainPanel(d)}</section>
    </div>
    ${run?.state === "COMPLETED" ? timelineCard(d) : ""}
    ${run?.state === "COMPLETED" ? notesCard(d) : ""}
    ${behindEvidence(d)}`;
}

// The clip in one sentence per kind, from the stored measurements: how long
// each kind of interval lasted. It answers "what does this clip show?" before
// any number about the algorithm.
function clipSummary(d) {
  const secs = {};
  for (const o of d.observations) {
    const k = ["low_quality", "insufficient_evidence"].includes(o.kind) ? "unmeasured" : o.kind;
    secs[k] = (secs[k] || 0) + (o.t_end - o.t_start);
  }
  const words = { movement: "Movement measured", small_change: "Small change (uncertain)", no_significant_movement: "No significant movement",
    camera_motion: "Camera moved", unmeasured: "Couldn't be measured (dark, blurred, frozen or missing video)" };
  const rows = Object.keys(words).filter((k) => secs[k]).map((k) => `<dt>${esc(words[k])}</dt><dd>${Math.round(secs[k])} s</dd>`).join("");
  return `<div class="clip-summary"><h3 class="small">What this clip shows</h3><dl class="facts small">${rows}</dl>
    <p class="small muted">Changed pixels, measured second by second. Not a sleep record, and not who or what moved.</p></div>`;
}

// Play one interval: renew the media link if it is about to lapse, seek, play,
// and pause on the interval's end (checked every frame, not every timeupdate).
// Every outcome is said in #play-status; a failure is never silent.
async function playPart(t0, t1) {
  const v = state.video, clip = document.getElementById("clip");
  if (!clip || !v?.view) return;
  const say = (text) => { v.play = { seq: v.sel, text }; const el = document.getElementById("play-status"); if (el) el.textContent = text; };
  const span = `${mmss(t0)}–${mmss(t1)}`;
  if (!v.mediaExpires || Date.now() / 1000 > v.mediaExpires - 15) {
    const link = await api(`/v1/video/assets/${v.assetId}/media-link`, { method: "POST" });
    if (!link.ok) { say(`Can't play ${span}: ${link.data?.message || "the clip isn't available"}. Nothing was changed.`); return; }
    v.mediaExpires = link.data.expires_at;
  }
  cancelAnimationFrame(v.stopFrame);
  say(`Playing ${span}…`);
  try {
    clip.currentTime = t0;
    await clip.play();
  } catch {
    say(`Couldn't play ${span}. The clip didn't load, or the link has expired. Nothing was changed; try again.`);
    return;
  }
  const watch = () => {
    if (clip.currentTime >= t1 || clip.ended) { clip.pause(); say(`Played ${span}. Stopped at ${mmss(t1)}.`); return; }
    if (!clip.paused) v.stopFrame = requestAnimationFrame(watch);
  };
  v.stopFrame = requestAnimationFrame(watch);
}

// A media element fails without bubbling; catch it so the page can say so.
document.addEventListener("error", (e) => {
  if (e.target?.id !== "clip" || !state.video) return;
  state.video.play = { seq: state.video.sel, text: "The clip couldn't be loaded. The link may have expired, or the server is unavailable. Nothing was changed." };
  const el = document.getElementById("play-status");
  if (el) el.textContent = state.video.play.text;
}, true);

function runStatus(run) {
  if (!run) return "";
  const st = { REGISTERED: ["pending", "clock", "Queued for analysis"], VALIDATING: ["pending", "clock", "Checking the file"],
    ANALYZING: ["pending", "clock", "Measuring the frames"], COMPLETED: ["success", "check", "Analysed"],
    FAILED: ["warning", "alert", "Analysis failed"] }[run.state];
  const frames = run.summary?.frames;
  const dev = ["REGISTERED", "FAILED"].includes(run.state) ? `<div class="demo-bar" role="region" aria-label="Simulation"><span class="dev-chip">simulation</span>
      <span>${run.state === "FAILED" ? "Not part of the parent app: queue the same run again." : "Not part of the parent app: start the worker as a separate process."}</span>
      <button type="button" data-action="${run.state === "FAILED" ? "video-retry" : "video-worker"}">${run.state === "FAILED" ? "Retry the analysis" : "Run the worker"}</button></div>` : "";
  const stalled = run.state === "REGISTERED" && state.video?.workerStartedAt && Date.now() - state.video.workerStartedAt > 45000;
  const stall = stalled ? `<p class="small" role="status">The worker hasn't picked this up yet. Run it again; if it stays queued, reload the page.</p>` : "";
  return `${stall}<div class="run-status"><span class="apill apill-${st[0]}">${icon(st[1], "icon-sm")}${esc(st[2])}</span>
    ${run.state === "COMPLETED" ? `<span class="small muted">${mmss(Math.round(run.summary.input.duration_s ?? frames.last_t))} clip</span>` : ""}
    ${run.state === "FAILED" ? `<span class="small">Reason: <code>${esc(run.error_code)}</code></span>` : ""}</div>${dev}`;
}

// Below 600px a second of video is narrower than a 44px target, so the
// proportional timeline becomes a picture and the full-width interval list
// (open by default) is the control.
const NARROW = window.matchMedia("(max-width: 599px)");
NARROW.addEventListener("change", () => { if (state.route === "recorded") render(); });

function timelineCard(d) {
  const total = d.asset.duration_s || d.run.summary.frames.last_t;
  const narrow = NARROW.matches;
  const at = (t0, t1) => `left:${(100 * t0 / total).toFixed(3)}%;width:${Math.max(0.6, 100 * (t1 - t0) / total).toFixed(3)}%`;
  const sel = state.video?.sel;
  const segs = d.observations.map((o) => { const [label, ic, cls] = KIND[o.kind]; return narrow
    ? `<span class="seg ${cls}${sel === o.seq ? " selected" : ""}" style="${at(o.t_start, o.t_end)}">${icon(ic, "icon-sm")}</span>`
    : `<button type="button" class="seg ${cls}${sel === o.seq ? " selected" : ""}" style="${at(o.t_start, o.t_end)}"
      data-action="video-select" data-seq="${o.seq}" aria-pressed="${sel === o.seq}" aria-label="${mmss(o.t_start)} to ${mmss(o.t_end)}: ${esc(label)}">${icon(ic, "icon-sm")}</button>`; }).join("");
  const notes = d.annotations.map((a) => `<span class="note-mark${a.superseded_by ? " corrected" : ""}" style="${at(a.t_start, a.t_end)}" title="${esc(a.says)} (${esc(a.author)})">${esc(a.superseded_by ? "corrected" : a.author)}</span>`).join("");
  const kinds = [...new Set(d.observations.map((o) => o.kind))];
  return `<section class="card" aria-labelledby="timeline-title"><h2 id="timeline-title">What the video shows, second by second</h2>
    <p class="small muted card-sub">Computed by the server from the stored measurements (revision ${d.evidence_revision}). ${narrow ? "Select an interval in the list to see its evidence." : "Select a part to see its evidence."}</p>
    <div class="timeline-track" ${narrow ? 'aria-hidden="true"' : 'role="group" aria-label="Measured intervals"'}>${segs}</div>
    <div class="timeline-notes" aria-label="What people reported">${notes || '<span class="small muted">No reports from people yet. The bars above are measurements.</span>'}</div>
    <div class="timeline-axis small muted"><span>0:00</span><span>${mmss(total)}</span></div>
    <ul class="kind-legend small">${kinds.map((k) => `<li><span class="legend-swatch ${KIND[k][2]}" aria-hidden="true"></span>${esc(KIND[k][0])}</li>`).join("")}
      <li><span class="legend-swatch note-swatch" aria-hidden="true"></span>Reported by a person</li></ul>
    <details class="interval-details"${state.video?.listOpen || narrow ? " open" : ""}><summary>List every interval (${d.observations.length})</summary>
      <ol class="interval-list">${d.observations.map((o) => `<li><button type="button" class="interval-row${sel === o.seq ? " selected" : ""}" data-action="video-select" data-seq="${o.seq}" aria-pressed="${sel === o.seq}">
        <span class="legend-swatch ${KIND[o.kind][2]}" aria-hidden="true"></span><span>${mmss(o.t_start)}–${mmss(o.t_end)}</span><strong>${esc(KIND[o.kind][0])}</strong></button></li>`).join("")}</ol></details></section>`;
}

function explainPanel(d) {
  const v = state.video;
  const o = d.observations.find((x) => x.seq === v.sel);
  if (!o) {
    return `<h2 id="explain-title">Explain an interval</h2><p>Select a part of the timeline. You'll see what was measured there, how sure we are, and what we don't know.</p>
      <ul class="small not-claimed">${d.not_claimed.map((n) => `<li>${icon("minus", "icon-sm")}Not measured: ${esc(n)}</li>`).join("")}</ul>`;
  }
  const [label, ic, cls] = KIND[o.kind];
  const reported = d.annotations.filter((a) => a.t_start < o.t_end && o.t_start < a.t_end);
  const mine = state.video.correcting ? d.annotations.find((a) => a.id === state.video.correcting) : null;
  return `<h2 id="explain-title">${mmss(o.t_start)}–${mmss(o.t_end)} of the clip</h2>
    <p class="kind-line"><span class="legend-swatch ${cls}" aria-hidden="true"></span><strong>${esc(label)}</strong></p>
    <p>${esc(o.says)}</p>
    <dl class="facts small"><dt>How much of the picture changed</dt><dd>${pct(o.local_change_mean)} on average, ${pct(o.local_change_max)} at most</dd>
      <dt>Video available</dt><dd>${o.coverage_min >= 0.995 ? "All of it" : `${pct(o.coverage_min)} of it`} (${o.frames} frames)</dd>
      <dt>Light level</dt><dd>${o.brightness_mean === null ? "—" : `${Math.round(o.brightness_mean)} of 255`}</dd>
      <dt>Measured by</dt><dd>The server, from the decoded frames (method ${esc(d.run.processor_version)})</dd></dl>
    <p class="small muted">How sure: this measures changed pixels, not who or what moved, and not sleep. The thresholds weren't validated against labelled video.</p>
    <div class="btn-row"><button type="button" class="btn btn-secondary" data-action="video-play" data-t0="${o.t_start}" data-t1="${o.t_end}">${icon("play", "icon-sm")}Play this part</button></div>
    <p id="play-status" class="small" role="status">${esc(v.play?.seq === o.seq ? v.play.text : "")}</p>
    ${reported.length ? `<h3>Reported here</h3><ul class="notes-list">${reported.map(noteItem).join("")}</ul>` : ""}
    ${d.viewer.can_annotate ? annotateForm(d, o, mine) : ""}
    ${v.result ? beforeAfter(v.result) : ""}
    ${v.note ? `<p class="small" role="status">${esc(v.note)}</p>` : ""}`;
}

function noteItem(a) {
  return `<li class="${a.superseded_by ? "corrected" : ""}"><span class="small">${esc(a.author)} reported:</span> <strong>${esc(a.says)}</strong>
    <span class="small muted">${esc(a.author)} · revision ${a.revision}${a.superseded_by ? " · corrected later" : ""}${a.supersedes ? " · a correction" : ""}${a.contradicts.length ? " · disagrees with the measurement; both are kept" : ""}</span>
    ${a.author_id === state.viewer && !a.superseded_by ? `<button type="button" class="link-button small" data-action="video-correct" data-id="${a.id}">Correct this</button>` : ""}</li>`;
}

function annotateForm(d, o, correcting) {
  const who = VIEWERS.find((x) => x.id === state.viewer)?.short || "You";
  const staged = d.asset.source_kind === "staged_sample";
  return `<fieldset class="annotate"><legend>${correcting ? `Correct "${esc(correcting.says)}"` : `What did you see between ${mmss(o.t_start)} and ${mmss(o.t_end)}?`}</legend>
    ${staged ? `<p class="small muted">This test clip shows shapes, not a baby. A report here tests the reporting flow; it says nothing about a child.</p>` : ""}
    ${Object.entries(LABEL_SAYS).map(([k, t]) => `<label class="radio-row"><input type="radio" name="video-label" value="${k}"> ${esc(t)}</label>`).join("")}
    <p class="small muted">Saved as what ${esc(who)} reported, next to the measurement. A report doesn't change the measurement, and the measurement doesn't overrule the report.</p>
    <div class="btn-row"><button type="button" class="btn btn-primary" data-action="video-annotate" data-seq="${o.seq}">${correcting ? "Save the correction" : "Add my report"}</button>
      ${correcting ? `<button type="button" class="btn btn-ghost" data-action="video-correct-cancel">Cancel</button>` : ""}</div></fieldset>`;
}

function beforeAfter(r) {
  const side = (title, s) => `<div><h4>${esc(title)} · revision ${s.revision}</h4>
    <p class="small"><strong>Measured:</strong> ${s.measured.map((m) => esc(KIND[m.kind][0])).join(", ") || "nothing"}</p>
    <p class="small"><strong>Reported:</strong> ${s.reported.map((x) => esc(LABEL_SAYS[x.label])).join("; ") || "nothing"}</p></div>`;
  return `<div class="before-after" role="status"><h3>What changed</h3><div class="ba-grid">${side("Before", r.before)}${side("After", r.after)}</div>
    <p class="small muted">The measurement didn't change. Your report is a new revision; anyone who saw the old one keeps that record.</p></div>`;
}

function notesCard(d) {
  const me = d.acknowledgments.find((a) => a.member_id === state.viewer);
  return `<section class="card" aria-labelledby="notes-title"><h2 id="notes-title">Reports and who has seen them</h2>
    ${d.annotations.length ? `<ul class="notes-list">${d.annotations.map(noteItem).join("")}</ul>` : `<p class="muted">No one has reported anything about this clip yet.</p>`}
    <h3>Seen by</h3>
    <ul class="seen-list small">${d.acknowledgments.map((a) => `<li>${esc(a.name)}: revision ${a.revision}${a.changed_since ? ` · changed since (now ${d.evidence_revision})` : " · up to date"}</li>`).join("") || "<li>No one yet</li>"}</ul>
    ${me && !me.changed_since ? "" : `<div class="btn-row"><button type="button" class="btn btn-secondary" data-action="video-ack" data-revision="${d.evidence_revision}">Mark revision ${d.evidence_revision} as seen</button></div>`}</section>`;
}

function behindEvidence(d) {
  const r = d.run, f = r?.summary?.frames, inp = r?.summary?.input;
  const row = (k, val) => `<tr><th scope="row">${esc(k)}</th><td><code>${esc(val)}</code></td></tr>`;
  return `<details class="card behind"><summary><strong>Behind this evidence</strong> <span class="small muted">stored rows, for engineers</span></summary>
    <div class="table-scroll" tabindex="0" role="region" aria-label="Clip and analysis records"><table class="plain">
      ${row("Clip", `${d.asset.id} · ${d.asset.source_kind}`)}${row("SHA-256", d.asset.sha256)}
      ${row("File", `${d.asset.size_bytes} bytes · ${d.asset.container} · ${d.asset.codec} ${d.asset.width}×${d.asset.height} @ ${d.asset.fps} fps`)}
      ${row("Provenance", `${d.asset.provenance.title} · ${d.asset.provenance.license}`)}
      ${row("Retention", `until ${d.asset.retention_until.slice(0, 10)} · audio not decoded or served`)}
      ${r ? row("Run", `${r.id} · ${r.state} · attempt ${r.attempts} · ${r.processor_version} · config ${r.config_digest}`) : ""}
      ${f ? row("Frames", `${f.decoded} decoded · first ${f.first_t}s · last ${f.last_t}s · ${f.timestamp_gap_count} timestamp gaps · measured in ${(r.elapsed_ms / 1000).toFixed(1)} s`) : ""}
      ${inp ? row("Decoder", `PyAV ${r.summary.library.av} · numpy ${r.summary.library.numpy}`) : ""}
      ${r ? row("Trace", r.trace_id || "—") : ""}
      ${row("Access gate", `${d.gate.label}: ${d.gate.allowed ? "included" : "not included"} (plan ${d.gate.plan_code}) · ${d.gate.rule}`)}
      ${row("Evidence revision", d.evidence_revision)}
    </table></div>
    ${r?.state === "COMPLETED" ? `<div class="table-scroll" tabindex="0" role="region" aria-label="Measured intervals"><table class="plain compare"><thead><tr><th>#</th><th>Interval</th><th>Kind</th><th>Reason</th><th>Frames</th><th>Change mean</th></tr></thead>
      <tbody>${d.observations.map((o) => `<tr><td>${o.seq}</td><td>${o.t_start}–${o.t_end}s</td><td>${esc(o.kind)}</td><td>${esc(o.reason)}</td><td>${o.frames}</td><td>${pct(o.local_change_mean)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <p class="small muted">Tests: <code>tests/test_video_evidence.py</code>. Measurements are append-only rows; reports and acknowledgments are separate append-only tables.</p></details>`;
}

async function videoAction(action, el) {
  const v = state.video || (state.video = {});
  const d = v.view;
  if (action === "video-select") { v.sel = Number(el.dataset.seq); v.listOpen = el.classList.contains("interval-row"); v.result = null; v.note = null; v.correcting = null; render(); return; }
  if (action === "video-correct") { v.correcting = el.dataset.id; const a = d.annotations.find((x) => x.id === v.correcting);
    v.sel = d.observations.find((o) => o.t_start < a.t_end && a.t_start < o.t_end)?.seq ?? v.sel; render(); return; }
  if (action === "video-correct-cancel") { v.correcting = null; render(); return; }
  if (action === "video-play") { await playPart(Number(el.dataset.t0), Number(el.dataset.t1)); return; }
  el.disabled = true;
  if (action === "video-sample") {
    const r = await api("/v1/dev/video/sample", { method: "POST", body: { device_id: state.accessDevice || "dev_cam_demo" } });
    log(`video sample → ${r.status} ${r.data?.asset?.id || r.data?.code || ""}`);
  } else if (action === "video-worker") {
    const r = await api("/v1/dev/video/worker", { method: "POST" });
    log(`worker process started → ${r.status} pid ${r.data?.pid ?? "?"}`);
    if (r.ok) { v.workerStartedAt = Date.now(); toast("The worker started. It measures the frames in its own process."); }
    else toast(r.data?.message || "The worker couldn't start. Try again in a moment.");
  } else if (action === "video-retry") {
    const r = await api(`/v1/video/assets/${v.assetId}/analysis/retry`, { method: "POST" });
    log(`analysis retry → ${r.status} ${r.data?.code || ""}`);
  } else if (action === "video-ack") {
    await api(`/v1/video/assets/${v.assetId}/acknowledge`, { method: "POST", body: { revision: Number(el.dataset.revision) } });
  } else if (action === "video-annotate") {
    const o = d.observations.find((x) => x.seq === Number(el.dataset.seq));
    // Nothing is pre-selected: a report says only what the person chose.
    const label = document.querySelector('input[name="video-label"]:checked')?.value;
    if (!label) { v.note = "Choose one answer first. Nothing was saved."; render(); return; }
    v.key = v.key || crypto.randomUUID();
    const corr = v.correcting ? d.annotations.find((a) => a.id === v.correcting) : null;
    const r = await api(`/v1/video/assets/${v.assetId}/annotations`, { method: "POST", headers: { "Idempotency-Key": v.key },
      body: { label, t_start: corr ? corr.t_start : o.t_start, t_end: corr ? corr.t_end : o.t_end, expected_revision: d.evidence_revision,
              supersedes: corr ? corr.id : null } });
    v.key = null;
    if (r.ok) { v.result = r.data; v.correcting = null; v.note = `Saved as revision ${r.data.revision}.`; }
    else v.note = r.data?.code === "STALE_EVIDENCE_VIEW" ? "Someone changed this evidence since you opened it. The newer revision is now shown; nothing was saved." : (r.data?.message || "Not saved.");
  }
  await loadRecorded({ keepSelection: true });
  render();
}

// evidence_review_opened: once per clip and member, after the evidence actually rendered.
function trackRecordedView() {
  const v = state.video;
  if (state.route !== "recorded" || !v?.view || v.view.run?.state !== "COMPLETED") return;
  const key = `${v.assetId}:${state.viewer}`;
  if (state.trackedRecorded === key) return;
  state.trackedRecorded = key;
  track("evidence_review_opened", { device_id: v.view.asset.device_id, asset_id: v.assetId });
}

async function windowEvent(el) {
  const device = state.accessDevice;
  const owner = VIEWERS[0].id;
  let note;
  switch (el.dataset.event) {
    case "recheck": break;
    case "mount-flex": case "mount-floor": {
      const mount = el.dataset.event === "mount-flex" ? "flex_stand" : "floor_stand";
      await api("/v1/dev/mount", { method: "POST", viewer: owner, body: { mount, device_id: device } });
      note = `Camera moved to ${mount === "flex_stand" ? "a Flex Stand" : "the Floor Stand"}.`;
      break;
    }
    case "refund": {
      const r = await api("/v1/dev/purchase-evidence", { method: "POST", viewer: owner, body: { channel: el.dataset.channel, external_ref: el.dataset.ref,
        plan_code: el.dataset.plan, status: "refunded", provider_version: Number(el.dataset.version), starts_at: "2026-10-01T00:00:00+00:00" } });
      note = r.ok ? `Store refunded ${el.dataset.ref}; the camera's claim was revoked.` : `Refund refused: ${r.data?.code}`;
      break;
    }
    case "reassign": {
      const rev = state.access.state_revision;
      const r = await api("/v1/access/bind", { method: "POST", viewer: owner, headers: { "Idempotency-Key": "reassign-ui-" + crypto.randomUUID() },
        body: { device_id: el.dataset.other, channel: el.dataset.channel, external_ref: el.dataset.ref, expected_state_revision: rev } });
      note = r.ok ? "Connected to the other camera (unexpected)." : `Refused: ${r.data?.code}. Access on this camera is unchanged.`;
      break;
    }
  }
  const v = await api("/v1/access/verify", { method: "POST", body: { device_id: device } });
  state.windowNote = `${note ? note + " " : ""}Rechecked: ${v.data?.check?.status ?? v.data?.code}.`;
  log(`window event ${el.dataset.event} on ${device}: ${state.windowNote}`);
  await refreshAccess();
  await loadGrowth();
  announce(state.windowNote);
}

// Customer evidence: public reports (paraphrased) → the synthetic scenario and
// the tests behind our answer. "Try it" builds the scenario through the API and
// opens the parent journey on it.

async function loadCustomerEvidence() {
  const r = await api("/v1/customer-evidence");
  state.customers = r.ok ? r.data : null;
}

const CONF_TONE = { high: "apill-success", medium: "apill-pending", low: "apill-neutral" };

function customerCard(c, demo) {
  const st = { passed: ["st-ok", "check", "passed"], failed: ["st-changed", "xoct", "failed"], not_run: ["st-none", "unknown", "not run"], skipped: ["st-none", "minus", "skipped"] };
  const conf = (c.confidence || "").split(" ")[0];
  const sources = c.sources.length
    ? `<ul class="customer-sources" aria-label="Sources">${c.sources.map((s) => `<li>${s.url ? `<a href="${esc(s.url)}" rel="noopener noreferrer" target="_blank">${esc(s.ref)}</a>` : esc(s.ref)} · ${esc(s.date)} · ${esc(s.where)}</li>`).join("")}</ul>`
    : `<p class="small muted customer-sources-note">${esc(state.customers?.sources_note || "")}</p>`;
  return `<article class="card customer-card${demo ? " demo" : ""}" aria-labelledby="cc-${c.id}">
    <div class="card-head"><h2 id="cc-${c.id}">${esc(c.title)}</h2>${c.confidence ? `<span class="apill ${CONF_TONE[conf] || "apill-neutral"}">Confidence: ${esc(c.confidence)}</span>` : `<span class="apill apill-neutral">Hypothesis</span>`}</div>
    ${demo ? `<p class="eyebrow" style="margin:0 0 var(--pl-space-2)">Shown in the walkthrough</p>` : ""}
    <p class="customer-symptom">${esc(c.symptom)}</p>
    ${sources}
    <dl class="terms customer-terms">
      <dt>Likely cause</dt><dd>${esc(c.diagnosis)}</dd>
      <dt>Nanit today</dt><dd>${esc(c.existing_solution)}</dd>
      <dt>What we do</dt><dd>${esc(c.improvement)}</dd>
      <dt>Engineering</dt><dd>${esc(c.engineering)}</dd>
      <dt>Still unknown</dt><dd>${esc(c.unknowns.join("; "))}</dd>
    </dl>
    ${c.confidence_note ? `<p class="small muted">${esc(c.confidence_note)}</p>` : ""}
    <div class="btn-row">${c.scenarios.filter((s) => s.exists).map((s, i) => `<button type="button" class="btn ${i === 0 ? "btn-primary" : "btn-secondary"}" data-action="customer-try" data-key="${s.key}">Try: ${esc(s.title)}</button>`).join("")}</div>
    <details class="customer-tests"><summary>Tests behind this answer (${c.tests.filter((t) => t.state === "passed").length} of ${c.tests.length} passed in the latest report${state.customers?.report?.matches_code ? "" : `, which <strong class="st-changed">ran on other code: rerun before trusting it</strong>`})</summary>
      <ul>${c.tests.map((t) => `<li><span class="${st[t.state][0]} cert-state">${icon(st[t.state][1], "icon-sm")}${st[t.state][2]}</span> <code>${esc(t.name)}</code>${t.file ? ` <span class="muted small">${esc(t.file)}</span>` : ""}</li>`).join("")}</ul></details>
  </article>`;
}

function customerScreen() {
  const c = state.customers;
  if (!c) return `<div class="page-head"><h1 class="display">Customer evidence</h1></div><div class="skeleton"></div>`;
  const ordered = [...c.cards].sort((a, b) => (b.id === c.demo_card) - (a.id === c.demo_card));
  return `<div class="page-head"><p class="eyebrow">Why this exists <span class="chip chip-sim">public reports, paraphrased</span></p><h1 class="display">Customer evidence</h1>
      <p class="lede">What parents reported, what Nanit already documents, and the working scenario and tests behind our answer.</p></div>
    <div class="alert alert-unknown">${icon("unknown")}<div><p class="alert-title">How to read this</p><p>${esc(c.caveat)}</p></div></div>
    <div class="customer-grid mt-6">${ordered.map((card) => customerCard(card, card.id === c.demo_card)).join("")}</div>
    <p class="small muted mt-6">${esc(c.origin)}</p>`;
}

async function tryScenario(key) {
  await loadStudio();
  await runStudio(key);
  location.hash = `#/access/${state.accessDevice}`;
}

// ------------------------------------------------------------------ main render

function resetNotice() {
  return `<div class="alert alert-error" role="alert">${icon("xoct")}<div><p class="alert-title">This demo was reset from another screen.</p>
    <p>The night you were looking at was replaced. Reload to continue with the fresh data.</p>
    <div class="btn-row" style="margin-top:8px"><button class="btn btn-primary" type="button" data-action="reload">Reload</button></div></div></div>`;
}

function render() {
  const main = document.getElementById("main");
  if (state.resetElsewhere) { main.innerHTML = resetNotice(); return; }
  let html;
  if (state.route === "home") {
    html = homeScreen();
  } else if (state.route === "access") {
    html = accessScreen();
  } else if (state.route === "studio") {
    html = studioScreen();
  } else if (state.route === "growth") {
    html = growthScreen();
  } else if (state.route === "customers") {
    html = customerScreen();
  } else if (state.route === "recorded") {
    html = recordedScreen();
  } else if (state.route === "briefing") {
    html = state.twoScreens
      ? `<div class="screens-wrap"><div class="screens two">${VIEWERS.map((v) => `<div class="screen-frame">
          <div class="frame-label"><span class="avatar" aria-hidden="true">${v.short.split(" ").map((w) => w[0]).join("")}</span>
            <span class="frame-who"><strong>${v.short}'s screen</strong><span class="muted small">Kept exactly as displayed until they ask for the newer version</span></span></div>
          ${briefingScreen(v.id)}</div>`).join("")}</div></div>`
      : briefingScreen(state.viewer);
  } else if (state.route === "changes") {
    html = changesScreen();
  } else {
    html = featuresScreen(state.viewer);
  }
  if (EVIDENCE_ROUTES.includes(state.route)) html = evidenceTabs() + html;
  main.innerHTML = html;
  main.dataset.route = state.route;
  trackAccessView();
  trackRecordedView();
  document.getElementById("dev-drawer-body").innerHTML = devControls();
  renderChrome();
  observeRenders();
}

function evidenceTabs() {
  const tab = (r) => `<a href="#/${r.id}" ${state.route === r.id ? 'aria-current="page"' : ""}>${esc(r.label)}</a>`;
  return `<nav class="lab-tabs" aria-label="Family evidence lab">${ROUTES.filter((r) => r.group === "evidence").map(tab).join("")}</nav>`;
}

// access_check_rendered: once per camera and status actually shown.
function trackAccessView() {
  const d = state.accessDetail;
  if (state.route !== "access" || !d) return;
  const key = `${d.device.id}:${d.status}`;
  if (state.accessTracked.has(key)) return;
  state.accessTracked.add(key);
  track("access_check_rendered", { device_id: d.device.id, status: d.status.toLowerCase() });
}

// Exposure: a render receipt is sent only when the surface is actually in view.
const observer = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    const el = e.target; observer.unobserve(el);
    const surface = el.dataset.render, subject = el.dataset.subject, viewer = el.dataset.viewer || state.viewer;
    // One exposure per member, surface and subject: a refresh or retry of the
    // same screen is the same exposure, so the id is deterministic.
    const renderId = `${viewer}-${surface}-${subject}`;
    if (state.receipts.has(renderId)) continue;
    state.receipts.add(renderId);
    const body = { render_id: renderId, surface, subject_id: subject };
    if (el.dataset.variant) Object.assign(body, { experiment_key: "availability_explanation", variant: el.dataset.variant });
    api("/v1/render-receipts", { method: "POST", body, viewer });
  }
}, { threshold: 0.5 });
function observeRenders() { document.querySelectorAll("[data-render]").forEach((el) => observer.observe(el)); }

// ------------------------------------------------------------------ actions

// Product analytics: allow-listed properties only (the server rejects anything
// else); one event id per user action so a retried send is counted once.
function track(name, properties, viewer = state.viewer) {
  api("/v1/analytics", { method: "POST", viewer, body: { event_id: "ui_" + crypto.randomUUID(), name, properties } });
}

async function acknowledge(viewer) {
  const screen = state.screens[viewer];
  track("acknowledge_clicked", { revision: screen.brief.revision, stale: state.latest.revision > screen.brief.revision }, viewer);
  const r = await api(`/v1/windows/${state.windowId}/acknowledgments`, {
    method: "POST", viewer, body: { revision_id: screen.brief.revision_id, displayed_facts: FACTS },
  });
  if (!r.ok) { toast(r.data?.message || "We couldn't record your acknowledgment. Nothing was changed."); log(`acknowledgment refused: ${r.data?.code}`); return; }
  screen.ack = r.data;
  log(`${name(viewer)} acknowledged rev ${r.data.acknowledged_revision} (current ${r.data.current_revision}) ${r.data.newer_available ? "→ stale: " + r.data.changed_since.join(", ") : ""}`);
  announce(r.data.message);
  await refreshLatest();
  render();
}

async function showLatest(viewer) {
  const screen = state.screens[viewer];
  const previous = screen.brief;
  screen.brief = await loadBrief(viewer);
  screen.previous = previous;
  screen.ack = null;
  screen.week = await loadWeek(viewer);
  const changed = FACTS.filter((k) => previous.fingerprints[k] !== screen.brief.fingerprints[k]);
  announce(`Revision ${screen.brief.revision}. ` + changed.map((k) => `${FACT_LABEL[k]} changed from ${factValue(k, previous.facts[k])} to ${factValue(k, screen.brief.facts[k])}.`).join(" "));
  render();
}

function openCorrection(viewer) {
  const b = state.screens[viewer].brief;
  const last = [...b.facts.recorded_sleep.intervals].sort((x, y) => x.end.localeCompare(y.end)).pop();
  const sheet = document.getElementById("correction-sheet");
  sheet.dataset.key = "corr-ui-" + crypto.randomUUID();
  sheet.dataset.viewer = viewer;
  sheet.innerHTML = `<form method="dialog" class="sheet-body" id="correction-form">
      <h2 id="correction-title">Correct final wake</h2>
      <p class="small muted" style="margin:0">Current: ${clock(last.end)} · ${SOURCE_LABEL[last.source]}</p>
      <div class="field"><label for="wake-time">Final wake</label>
        <input id="wake-time" type="time" step="300" value="06:35" min="${clock(last.start)}" max="07:00" required></div>
      <p class="small mt-4">Your correction is labelled <strong>parent reported</strong>. The camera's record is kept, and time the camera didn't observe stays unknown. The server recalculates the briefing when you save.</p>
      <div id="correction-error"></div>
      <div class="btn-row"><button class="btn btn-primary" type="submit" value="save">Save correction</button>
        <button class="btn btn-ghost" type="button" data-action="correct-cancel">Cancel</button></div>
    </form>`;
  sheet.dataset.target = last.event_id;
  sheet.showModal();
  sheet.querySelector("#wake-time").focus();
  // A new form is built each time the sheet opens, so this listener never stacks.
  sheet.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); submitCorrection(); });
}

function wakeIso(hhmm) {
  // The window's morning is 2026-10-10 in America/New_York (EDT, UTC-4) for this synthetic night.
  return `2026-10-10T${hhmm}:00-04:00`;
}

async function submitCorrection(viewer = document.getElementById("correction-sheet").dataset.viewer, hhmm) {
  const sheet = document.getElementById("correction-sheet");
  const screen = state.screens[viewer];
  const time = hhmm || sheet.querySelector("#wake-time")?.value || "06:35";
  const target = sheet.dataset.target || [...screen.brief.facts.recorded_sleep.intervals].sort((x, y) => x.end.localeCompare(y.end)).pop().event_id;
  const key = sheet.dataset.key || "corr-ui-" + crypto.randomUUID();
  const save = sheet.querySelector("button[type=submit]");
  if (save) save.disabled = true;
  const r = await api(`/v1/windows/${state.windowId}/corrections`, {
    method: "POST", viewer,
    body: { target_event_id: target, corrected_end: wakeIso(time), expected_revision: screen.brief.revision, client_event_id: key },
  });
  if (!r.ok) {
    const conflict = r.data?.code === "REVISION_CONFLICT";
    const msg = conflict ? "The briefing changed since this screen loaded. Show the newer revision, then correct it." : (r.data?.message || "We couldn't save the correction. Nothing was changed.");
    const box = sheet.querySelector("#correction-error");
    if (box) box.innerHTML = `<div class="alert alert-error mt-4" role="alert">${icon("xoct")}<div><p class="alert-title">Not saved</p><p>${esc(msg)}</p>
      ${conflict ? `<div class="btn-row" style="margin-top:8px"><button class="btn btn-secondary" type="button" data-action="correct-refresh" data-viewer="${viewer}">Show the newer revision</button></div>` : ""}</div></div>`;
    // A retry is a new request: the same key with different content would be refused.
    if (conflict) sheet.dataset.key = "corr-ui-" + crypto.randomUUID();
    if (save) save.disabled = false;
    log(`correction rejected: ${r.data?.code} (trace ${r.trace})`);
    return r;
  }
  if (sheet.open) sheet.close();
  delete sheet.dataset.key; delete sheet.dataset.target;
  log(`${name(viewer)} corrected final wake → ${time}: rev ${r.data.previous_revision} → ${r.data.revision}${r.data.replayed ? " (replay)" : ""}`);
  await refreshLatest();
  await showLatest(viewer);          // the parent's own screen shows the saved revision first
  toast(`Correction saved as revision ${r.data.revision}. The camera's record is kept.`);
  await api("/v1/dev/worker", { method: "POST", body: {} });   // deliver the caregiver notification
  return r;
}

async function decide() {
  state.decisionNote = null; state.purchaseNote = null; clearSettledPurchase();
  const r = await api("/v1/capability-decisions", { method: "POST", body: { feature: state.feature, device_id: state.device } });
  if (!r.ok) { state.decisionNote = `<div class="alert alert-error mt-4" role="alert">${icon("xoct")}<div><p class="alert-title">We couldn't check availability</p><p>${esc(r.data?.message)}</p></div></div>`; render(); return; }
  state.decision = r.data;
  await loadCommercial();
  log(`decision ${r.data.decision_id}: ${r.data.status} [${r.data.blockers.map((b) => b.code).join(", ")}] offer=${r.data.offer ? r.data.offer.plan_code : "none"} setup rev ${r.data.state_revision}`);
  announce(`${featureLabel(state.feature)}: ${r.data.status === "AVAILABLE" ? "available" : r.data.offer ? "needs a plan; an upgrade is offered" : r.data.status === "UNKNOWN" ? "unknown" : "blocked by hardware; no upgrade offered"}.`);
  render();
}

async function buy({ concurrent = 1 } = {}) {
  const d = state.decision;
  if (!d?.offer) return;
  track("offer_cta_clicked", { feature: d.feature, variant: d.experiment.variant });
  state.purchaseKey = state.purchaseKey || "buy-ui-" + crypto.randomUUID();
  const body = { decision_id: d.decision_id, offer_id: d.offer.offer_id, expected_amount_minor: d.offer.amount_minor, expected_currency: d.offer.currency };
  const calls = Array.from({ length: concurrent }, () => api("/v1/purchase-intents", { method: "POST", body, headers: { "Idempotency-Key": state.purchaseKey } }));
  const results = await Promise.all(calls);
  results.forEach((r) => log(`POST purchase-intents key=${state.purchaseKey.slice(0, 16)}… → ${r.status} ${r.data?.code || r.data?.intent_id || ""}${r.data?.replayed ? " (replayed)" : ""}`));
  const ok = results.find((r) => r.ok);
  if (!ok) {
    const r = results[0];
    const reasons = (r.data?.detail?.reasons || []).map((x) => STALE_TEXT[x] || x).join(" ");
    state.purchaseNote = `<div class="card"><div class="alert alert-warning" role="status">${icon("alert")}<div>
      <p class="alert-title">${r.data?.code === "NO_ELIGIBLE_OFFER" ? "There's no upgrade to buy here." : r.data?.code === "UPGRADE_IN_PROGRESS" ? "An upgrade is already being processed." : "We didn't submit this offer."}</p>
      <p>${r.data?.code === "STALE_DECISION" ? `It was based on an earlier eligibility check. ${esc(reasons)} Nothing was charged.` : esc(r.data?.message || "Nothing was charged.")}</p></div></div>
      <div class="btn-row"><button class="btn btn-secondary" type="button" data-action="decide">${icon("refresh", "icon-sm")}Check eligibility again</button></div></div>`;
    announce("We didn't submit this offer. Nothing was charged.");
    render();
    return;
  }
  state.purchase = ok.data;
  render();
  const crash = state.crashNext; state.crashNext = false;
  const w = await api("/v1/dev/worker", { method: "POST", body: { crash_after_provider: crash } });
  log(`worker ${crash ? "(crash after provider) " : ""}exit=${w.data?.exit_code}${w.data?.crashed ? ": the process died between the provider commit and the app commit" : ""}`);
  await refreshPurchase();
  render();
}

async function refreshPurchase() {
  if (!state.purchase) return;
  const r = await api(`/v1/purchase-intents/${state.purchase.intent_id}`);
  // A demo reset removes the purchase it was showing: drop it instead of asking again.
  if (r.status === 404) { state.purchase = null; return; }
  if (r.ok) {
    const before = state.purchase.state;
    state.purchase = r.data;
    if (before !== r.data.state) {
      announce({ CONFIRMED: "Confirmed. Simulated subscription active. No money moved.", FAILED: "Failed. Not charged.", UNKNOWN: "Outcome unknown. Checking." }[r.data.state] || `Status: ${r.data.state}`);
      if (r.data.state === "CONFIRMED") await loadCommercial();
    }
  }
}

// ------------------------------------------------------------------ X-Ray

function engLinks() {
  return `<nav class="eng-links" aria-label="Engineering pages">${ROUTES.filter((r) => r.group === "engineering").map((r) =>
    `<a href="#/${r.id}">${icon(r.icon, "icon-sm")}${esc(r.label)}</a>`).join("")}
    <a href="/architecture">${icon("compare", "icon-sm")}System architecture</a></nav>`;
}

function accessXray(x) {
  const d = state.accessDetail;
  const trace = d?.trace ? table(["step", "rule", "result"], d.trace.map((t) => [t.step, esc(t.rule), `<code>${esc(JSON.stringify(t.result))}</code>`])) : "<p>Open a camera in the access journey to see its decision.</p>";
  return xsec("access", "Access decision", (d ? `<p class="id-line">${esc(d.device.id)} · <code>${esc(d.status)}</code> · revision ${d.state_revision} · fingerprint <code>${esc(d.fingerprint.slice(0, 12))}</code>${copyBtn(d.fingerprint, "fingerprint")}</p>` : "") + trace,
    { open: ["home", "access", "studio", "growth"].includes(state.route), note: "Deterministic and read-only: the twelve steps in app/access.py. No step writes.", count: d?.trace?.length ?? null })
    + xsec("periods", "Plan periods on this camera", d?.periods?.length
        ? table(["plan", "channel", "starts", "ends", "state", "renewal"], d.periods.map((p) => [esc(p.plan_label), esc(p.channel), esc(p.starts_at.slice(0, 10)), esc(p.ends_at?.slice(0, 10) || "—"), esc(p.state), p.renewal ? '<span class="yes">yes</span>' : "—"]))
        : "<p>Open a camera in the access journey.</p>",
      { open: ["access"].includes(state.route), note: "Claim periods derived from append-only entitlement claims. A renewal is a second period of the same purchase (migrations 0007, 0008).", count: d?.periods?.length ?? null })
    + xsec("access-records", "Purchase evidence, connects, checks",
      table(["ref", "channel", "status", "version", "period", "trace"], (x.purchase_evidence || []).map((e) => [esc(e.external_ref), esc(e.channel), esc(e.status) + (e.provider_event ? `<br><span class="small muted">${esc(e.provider_event)}</span>` : ""), e.provider_version, `${esc(e.starts_at.slice(0, 10))} → ${esc(e.ends_at?.slice(0, 10) || "—")}`, esc(e.trace_id)]))
      + table(["camera", "ref", "key", "outcome"], (x.access_actions || []).map((a) => [esc(a.device_id), esc(a.external_ref), esc(a.idempotency_key.slice(0, 18)), esc(a.outcome.already_connected ? "already connected" : a.outcome.camera_plan_before + " → " + a.outcome.camera_plan_after)]))
      + table(["camera", "kind", "status", "usable"], (x.access_checks || []).map((c) => [esc(c.device_id), esc(c.kind), esc(c.status), esc(c.usable_features.join(", "))])),
      { note: "All three tables are append-only (database triggers).", count: (x.purchase_evidence || []).length + (x.access_actions || []).length + (x.access_checks || []).length });
}

function table(cols, rows, latestIndex = -1) {
  return `<div class="xray-scroll" tabindex="0" role="region"><table><thead><tr>${cols.map((c) => `<th scope="col">${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr class="${i === latestIndex ? "latest" : ""}">${r.map((c, j) => `<td class="${j === 0 ? "key" : ""}">${c}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${cols.length}">none</td></tr>`}</tbody></table></div>`;
}

// One X-Ray section. Open/closed state survives the live refresh.
function xsec(id, title, body, { open = false, note = "", count = null } = {}) {
  const isOpen = state.xsecOpen[id] ?? open;
  return `<details class="xsec" data-sec="${id}" ${isOpen ? "open" : ""}><summary><span class="xsec-title">${title}</span>${count != null ? `<span class="xsec-count">${count}</span>` : ""}</summary>
    <div class="xsec-body">${note ? `<p>${note}</p>` : ""}${body}</div></details>`;
}
const copyBtn = (text, label) => `<button type="button" class="copy-btn" data-copy="${esc(text)}" aria-label="Copy ${esc(label)}">${icon("copy", "icon-sm")}</button>`;

function xraySwitch() {
  const views = [["live", "Live trace"], ["proof", "Data & proof"]];
  return `<div class="segmented xray-switch" role="radiogroup" aria-label="X-Ray view">${views.map(([id, label]) =>
    `<button type="button" role="radio" aria-checked="${state.xrayTab === id}" data-action="xray-tab" data-tab="${id}">${label}</button>`).join("")}</div>`;
}

async function loadXray() {
  const body = document.getElementById("xray-body");
  if (state.xrayTab === "proof") { body.innerHTML = engLinks() + xraySwitch() + await proofHtml(); return; }
  const viewer = state.viewer;
  const [x, revs, ev] = await Promise.all([api("/v1/xray", { viewer }), api(`/v1/windows/${state.windowId}/revisions`, { viewer }), api(`/v1/windows/${state.windowId}/evidence`, { viewer })]);
  if (!x.ok) { body.innerHTML = xraySwitch() + `<p>${esc(x.data?.message)}</p>`; return; }
  const screen = state.screens[viewer];
  const latest = state.latest;
  const fp = FACTS.map((k) => [k, screen.brief.fingerprints[k].slice(0, 12), latest.fingerprints[k].slice(0, 12),
    screen.brief.fingerprints[k] === latest.fingerprints[k] ? '<span class="yes">same</span>' : '<span class="no">changed</span>']);
  const purchase = state.purchase;
  body.innerHTML = engLinks() + xraySwitch() + `<p class="xray-lede">Viewing as ${esc(name(viewer))}. Screen shows rev ${screen.brief.revision}; current is rev ${latest.revision}.</p>
    ${xsec("revisions", "Briefing revisions", table(["rev", "status", "sleep min", "coverage", "cause", "evidence", "trace"], revs.data.map((r) => [r.revision, r.status, r.recorded_sleep_minutes ?? "unknown", r.coverage_pct ?? "unknown", esc(r.cause.kind), r.evidence_ids.length, esc(r.trace_id)]), revs.data.length - 1), { open: true, note: "Append-only: a correction adds a revision; none is rewritten.", count: revs.data.length })}
    ${xsec("fingerprints", "Fact fingerprints", table(["fact", `rev ${screen.brief.revision}`, `rev ${latest.revision}`, ""], fp), { open: true, note: "sha256 over value + provenance + uncertainty. Displayed revision vs current." })}
    ${xsec("evidence", "Evidence", table(["id", "kind", "source", "interval", "superseded by", "corrects"], ev.data.map((e) => [esc(e.id), esc(e.kind), esc(e.source), `${clock(e.started_at)}–${clock(e.ended_at)}${e.payload?.usable === false ? " (unusable)" : ""}`, e.superseded_by ? esc(e.superseded_by) : "—", e.supersedes_event_id ? esc(e.supersedes_event_id) : "—"])), { note: "Corrections are new rows; originals are retained (UPDATE/DELETE rejected by trigger).", count: ev.data.length })}
    ${xsec("acks", "Acknowledgments", table(["member", "acked rev", "current", "needs review"], (state.changes?.members || []).map((m) => [esc(m.display_name), m.acknowledged_revision ?? "—", m.current_revision, m.needs_review.join(", ") || "—"])), { open: true })}
    ${xsec("decision", "Capability decision", state.decision ? `<div class="xray-scroll"><pre>${esc(JSON.stringify({ decision_id: state.decision.decision_id, status: state.decision.status, blockers: state.decision.blockers.map((b) => b.code), offer: state.decision.offer && { offer_id: state.decision.offer.offer_id, amount_minor: state.decision.offer.amount_minor, resolves: state.decision.offer.resolves }, inputs: { plan: state.decision.inputs.plan_code, mounts: state.decision.inputs.mounts }, state_revision: state.decision.state_revision, catalog: state.decision.catalog_version, policy: state.decision.policy_version, expires_at: state.decision.expires_at, experiment: state.decision.experiment, trace_id: state.decision.trace_id }, null, 2))}</pre></div>` : "<p>No decision yet.</p>", { open: Boolean(state.decision) })}
    ${xsec("purchase", "Purchase operation", purchase ? `<p class="id-line">intent <code>${esc(purchase.intent_id)}</code>${copyBtn(purchase.intent_id, "intent id")} · provider key <code>${esc(purchase.provider_operation_key)}</code>${copyBtn(purchase.provider_operation_key, "provider key")} · state ${esc(purchase.state)}</p>
      ${table(["from", "to", "reason", "actor", "trace"], (purchase.transitions || []).map((t) => [t.from ?? "∅", t.to, esc(t.reason), esc(t.actor), esc(t.trace_id)]), (purchase.transitions || []).length - 1)}
      <p class="mt-4">Provider ledger record (separate database):</p><div class="xray-scroll"><pre>${esc(JSON.stringify(purchase.provider_record, null, 2))}</pre></div>` : "<p>No purchase yet.</p>", { open: Boolean(purchase) })}
    ${accessXray(x.data)}
    ${(x.data.video_assets || []).length ? xsec("video", "Video evidence", table(["clip", "camera", "source", "sha256", "revision"], x.data.video_assets.map((a) => [esc(a.id), esc(a.device_id), esc(a.source_kind), `<code>${esc(a.sha256.slice(0, 16))}…</code>`, a.evidence_revision + (a.deleted ? " (deleted)" : "")]))
      + table(["run", "state", "attempts", "frames", "ms", "processor", "trace"], (x.data.video_runs || []).map((r) => [esc(r.id), esc(r.state) + (r.error_code ? ` <code>${esc(r.error_code)}</code>` : ""), r.attempts, r.frames ?? "—", r.elapsed_ms ?? "—", `${esc(r.processor_version)} · ${esc(r.config_digest)}`, esc(r.trace_id || "—")]))
      + table(["report", "rev", "label", "interval", "corrects"], (x.data.video_annotations || []).map((a) => [esc(a.id), a.revision, esc(a.label), `${a.t_start}–${a.t_end}s`, a.supersedes ? esc(a.supersedes) : "—"])),
      { note: "Measurements and reports are separate append-only tables; a report never edits a measurement.", count: (x.data.video_runs || []).length }) : ""}
    ${xsec("claims", "Entitlement claims", table(["camera", "channel", "ref", "status", "plan", "period"], (x.data.entitlement_claims || []).map((c) => [esc(c.device_id), esc(c.channel), esc(c.external_ref), esc(c.event_status), esc(c.plan_code), `${esc(c.starts_at.slice(0, 10))} → ${esc(c.ends_at?.slice(0, 10) || "—")}`])), { note: "Append-only; each camera's plan is derived from these.", count: (x.data.entitlement_claims || []).length })}
    ${xsec("ledger", "Provider ledger", table(["operation key", "status", "amount", "attempts"], x.data.provider_ledger.map((o) => [esc(o.operation_key), esc(o.status), `${o.amount_minor} ${o.currency}`, o.attempts])), { note: "Simulated provider, separate database.", count: x.data.provider_ledger.length })}
    ${xsec("outbox", "Outbox", table(["id", "kind", "state", "attempts", "dedupe key", "last error"], x.data.outbox.map((j) => [j.id, esc(j.kind), esc(j.state), j.attempts, esc(j.dedupe_key), esc(j.last_error || "—")]), 0), { count: x.data.outbox.length })}
    ${xsec("authz", "Authorization decisions", table(["member", "action", "resource", "allowed", "reason", "trace"], x.data.authorization.slice(0, 25).map((a) => [esc(a.member_id), esc(a.action), esc(a.resource), a.allowed ? '<span class="yes">yes</span>' : '<span class="no">no</span>', esc(a.reason), esc(a.trace_id)]), 0), { note: "Latest 25.", count: x.data.authorization.length })}
    ${xsec("experiments", "Experiment & exposure", table(["experiment", "version", "variant"], x.data.experiments.map((e) => [esc(e.experiment_key), esc(e.config_version), esc(e.variant)])) + table(["render id", "surface", "subject", "variant"], x.data.render_receipts.map((r) => [esc(r.render_id), esc(r.surface), esc(r.subject_id), esc(r.variant || "—")])), { note: "Assignment is sticky per household; exposure is a render receipt sent when the surface is in view, never on fetch." })}
    ${xsec("analytics", "Analytics", table(["name", "properties", "trace"], x.data.analytics.slice(0, 20).map((a) => [esc(a.name), esc(JSON.stringify(a.properties)), esc(a.trace_id)])), { note: "Allow-listed, idempotent.", count: x.data.analytics.length })}
    <div class="mobile-dev" style="margin-top:24px">${devPanel()}</div>`;
}

// ------------------------------------------------------------------ Data & Proof Explorer
// Everything here comes from /v1/proof: committed artifacts, the live database
// catalog, or code locations resolved at runtime. Each section names its origin.

const PROOF_TABS = [["flows", "Flows"], ["sources", "Sources"], ["availability", "Data availability"],
  ["schema", "Domain model"], ["stress", "Stress"], ["reliability", "Reliability"], ["tests", "Tests"], ["unknowns", "Unknowns"]];
const origin = (o) => `<span class="chip xray-chip origin-chip">${esc(o)}</span>`;
const ms = (v) => (v == null ? "—" : Math.round(v).toLocaleString());

function proofFlows(p) {
  const flow = p.flows.find((f) => f.id === state.proofFlow) || p.flows[0];
  const picker = `<div class="proof-nav flow-picker" role="radiogroup" aria-label="Flow">${p.flows.map((f) =>
    `<button type="button" role="radio" aria-checked="${f.id === flow.id}" data-action="proof-flow" data-flow="${f.id}">${esc(f.title)}</button>`).join("")}</div>`;
  const steps = flow.steps.length ? `<ol class="flow-steps">${flow.steps.map((s) => `<li class="flow-step ${s.exists ? "" : "missing"}">
      <span class="boundary">${esc(s.boundary)}</span><span class="step-label">${esc(s.label)}</span>
      <span class="ref-line"><code>${s.exists ? `${esc(s.file)}:${s.line}` : `${esc(s.ref)} (missing in code)`}</code>${s.exists ? copyBtn(`${s.file}:${s.line}`, "file reference") : ""}</span>
      <p>${esc(s.note)}</p></li>`).join("")}</ol>` : "";
  const tests = flow.tests.length ? `<details class="evidence"><summary>${flow.tests.length} tests guard this flow</summary>
      <ul>${flow.tests.map((t) => `<li><code>${esc(t.name)}</code> <span class="muted">${t.exists ? esc(t.file) : "missing"}</span></li>`).join("")}</ul></details>` : "";
  return `${picker}<p>${esc(flow.summary)}</p>${origin(flow.origin)}${steps}${tests}`;
}

function proofSources(p) {
  const s = p.sources;
  return `${origin(s.origin)}<p>Public research that informed the product. Nothing here is a Nanit family's record.</p>
    ${s.sources.map((x) => `<details class="evidence"><summary><span class="chip xray-chip">${esc(x.access_status)}</span> ${esc(x.title)}</summary>
      <dl class="kv"><dt>Publisher</dt><dd>${esc(x.publisher)}</dd><dt>Published</dt><dd>${esc(x.publication_date)}</dd>
      <dt>Verified</dt><dd>${esc(x.last_verified)}</dd><dt>Participants</dt><dd>${esc(x.participant_count)}</dd>
      <dt>Proves</dt><dd>${esc(x.proves)}</dd><dt>Can't prove</dt><dd>${esc(x.cannot_prove)}</dd>
      <dt>Source</dt><dd>${esc(x.source_url)}</dd></dl></details>`).join("")}`;
}

function proofAvailability(p) {
  const s = p.sources;
  const yes = s.row_level_public === true ? "yes" : s.row_level_public === false ? "no" : "not stated";
  return `${origin(s.origin)}<p>Is any row-level Nanit dataset publicly downloadable? <strong>${yes}</strong>. Every family, night and purchase in this app is synthetic.</p>
    ${table(["source", "class", "records", "use here", "redistribute"], s.sources.map((x) => [esc(x.id), esc(x.access_status),
      esc(x.record_count), esc(x.can_use_in_prototype), esc(x.can_redistribute)]))}`;
}

function proofSchema(p) {
  const s = p.schema;
  return `${origin(s.origin)}<p>Migration ${esc(s.migration)}. Sizes are planner estimates for the whole database, not one household.</p>
    ${table(["table", "rows (est.)", "size", "append-only", "FKs", "unique", "partial unique"], s.tables.map((t) => [esc(t.name),
      Number(t.estimated_rows).toLocaleString(), `${(t.bytes / 1024).toFixed(0)} kB`, t.append_only ? '<span class="yes">yes</span>' : "—",
      t.foreign_keys.length, t.unique.length, t.partial_unique.length]))}
    ${s.tables.filter((t) => t.foreign_keys.length || t.partial_unique.length).map((t) => `<details class="evidence"><summary>${esc(t.name)} constraints</summary>
      <ul>${[...t.foreign_keys, ...t.unique, ...t.partial_unique].map((c) => `<li><code>${esc(c.name)}</code> <span class="muted">${esc(c.definition)}</span></li>`).join("")}</ul></details>`).join("")}`;
}

function proofStress(p) {
  const b = p.benchmarks;
  const rows = b.runs.filter((r) => r.measured).map((r) => [esc(`${r.scenario} · ${r.label}`), r.concurrency ?? "—",
    r.measured.throughput_rps ?? "—", ms(r.measured.p50_ms), ms(r.measured.p95_ms), ms(r.measured.p99_ms),
    r.measured.error_rate != null ? `${(r.measured.error_rate * 100).toFixed(2)}%` : "—",
    r.invariant ? (r.invariant.held ? '<span class="yes">held</span>' : '<span class="no">broken</span>') : "—"]);
  const plans = b.runs.filter((r) => r.explain).map((r) => `<details class="evidence"><summary>Query plans · ${esc(r.label)}</summary>
    ${table(["query", "ms", "plan"], Object.entries(r.explain).map(([q, e]) => [esc(q), esc(e.execution_ms), esc(e.summary)]))}</details>`).join("");
  const opt = b.runs.filter((r) => r.baseline && r.after).map((r) => `<details class="evidence" open><summary>Before / after · ${esc(r.scenario)}</summary>
    <p>${esc(r.workload?.history_done_jobs?.toLocaleString())} finished jobs, ${esc(r.workload?.pending_jobs)} waiting. Change: <code>${esc(r.change)}</code></p>
    ${table(["", "median ms", "plan", "same job claimed"], [["before", r.baseline.median_ms, esc(r.baseline.plan), "—"],
      ["after", r.after.median_ms, esc(r.after.plan), r.identical_result ? '<span class="yes">yes</span>' : '<span class="no">no</span>']])}
    <p>${r.speedup}× faster. Cost: inserting and finishing 10,000 jobs took ${esc(r.write_cost_10k_jobs_ms?.without_index)} ms without the index, ${esc(r.write_cost_10k_jobs_ms?.with_index)} ms with it.</p></details>`).join("");
  const sets = b.runs.filter((r) => r.dataset).map((r) => [esc(r.dataset.profile), r.dataset.households.toLocaleString(),
    r.dataset.evidence.toLocaleString(), r.dataset.revisions.toLocaleString(), `${r.dataset.database_mb} MB`, `${r.dataset.seconds} s`]);
  return `${origin(b.origin)}<p>One laptop, synthetic data. Relative numbers, not capacity claims.</p>
    ${sets.length ? `<h3>Generated datasets</h3>${table(["profile", "households", "evidence rows", "revisions", "size", "built in"], sets)}` : ""}
    <h3>Load runs</h3>${table(["run", "conc.", "req/s", "p50 ms", "p95 ms", "p99 ms", "errors", "invariant"], rows)}${opt}${plans}`;
}

function proofReliability(p) {
  const r = p.recovery;
  const traces = r.traces.map((t) => `<li><code>${esc(t.file.split("/").pop())}</code> → <strong>${esc(t.state)}</strong>
    <span class="muted">${t.transitions.map((x) => `${esc(x.to)} (${esc(x.reason)})`).join(" → ")}
    · provider ${esc(t.provider_record.status)}, attempts ${esc(t.provider_record.attempts)}</span></li>`).join("");
  const lab = r.lab ? table(["fault", "expected", "observed", "result"], r.lab.scenarios.map((s) => [esc(s.fault), esc(s.expected), esc(s.observed),
    s.held === null || s.held === undefined ? '<span class="muted">not run</span>' : s.held ? '<span class="yes">held</span>' : '<span class="no">broken</span>'])) : "<p>Failure lab not run yet.</p>";
  return `${origin(r.origin)}<h3>Recovered crashes</h3><ul class="plain">${traces || "<li>none</li>"}</ul><h3>Failure lab</h3>${lab}`;
}

function proofTests(p) {
  const t = p.tests, m = p.mutation;
  const mutants = (m.results || []).filter((r) => r.id !== "BASELINE");
  const killed = mutants.filter((r) => r.verdict === "KILLED").length;
  const baseline = (m.results || []).find((r) => r.id === "BASELINE");
  const mutationNote = !baseline ? "<p>Mutation testing hasn't been run.</p>"
    : baseline.verdict !== "GREEN" ? `<p class="no">The unmutated baseline failed (${esc(baseline.summary)}), so no mutants were run. These results mean nothing until it's green.</p>`
    : `<p>Known defects planted one at a time: the suite caught ${killed} of ${mutants.length}.</p>`;
  return `${origin(t.origin)}${table(["report", "tests", "failures", "errors", "skipped", "seconds", "run at"], t.reports.map((r) => [esc(r.file.split("/").pop()),
      r.tests, r.failures, r.errors, r.skipped, Number(r.time).toFixed(0), esc((r.timestamp || "").slice(0, 16))]))}
    <h3>Mutation testing</h3>${origin(m.origin)}${mutationNote}
    ${table(["mutant", "guarantee", "verdict", "caught by"], mutants.map((r) => [esc(r.id), esc(r.guarantee),
      r.verdict === "KILLED" ? '<span class="yes">killed</span>' : `<span class="no">${esc(r.verdict.toLowerCase())}</span>`,
      esc((r.first_failures[0] || "—").split("::").pop())]))}`;
}

function proofUnknowns(p) {
  return `${origin(p.unknowns.origin)}<p>What public information can't settle. Stated, not hidden.</p>
    <dl class="kv">${p.unknowns.items.map((u) => `<dt>${esc(u.topic)}</dt><dd>${esc(u.detail)}</dd>`).join("")}</dl>`;
}

async function proofHtml() {
  if (!state.proof) {
    const r = await api("/v1/proof");
    if (!r.ok) return `<p>${esc(r.data?.message || "The proof explorer could not load.")}</p>`;
    state.proof = r.data;
  }
  const p = state.proof;
  const nav = `<div class="proof-nav" role="radiogroup" aria-label="Evidence">${PROOF_TABS.map(([id, label]) =>
    `<button type="button" role="radio" aria-checked="${state.proofTab === id}" data-action="proof-tab" data-tab="${id}">${label}</button>`).join("")}</div>`;
  const views = { flows: proofFlows, sources: proofSources, availability: proofAvailability, schema: proofSchema,
    stress: proofStress, reliability: proofReliability, tests: proofTests, unknowns: proofUnknowns };
  return `<h3 class="xray-section-title">Data & Proof Explorer</h3>${nav}<section class="proof-view" aria-label="${esc(PROOF_TABS.find(([id]) => id === state.proofTab)?.[1] || "Flows")}">${(views[state.proofTab] || proofFlows)(p)}</section>`;
}

async function openXray() {
  state.xrayOpener = document.activeElement;
  state.xrayOpen = true;
  document.body.classList.add("xray-is-open");     // >= 1280px the page reflows beside it
  const x = document.getElementById("xray");
  x.classList.add("open"); x.setAttribute("aria-hidden", "false");
  const modal = !matchMedia("(min-width: 1280px)").matches;   // docked beside the page from 1280px; a dialog below
  if (modal) { x.setAttribute("role", "dialog"); x.setAttribute("aria-modal", "true"); } else { x.removeAttribute("role"); x.removeAttribute("aria-modal"); }
  document.getElementById("xray-scrim").classList.add("open");
  document.getElementById("xray-body").innerHTML = `<p>Loading…</p>`;
  document.getElementById("xray-close").focus();
  state.proof = null;   // re-read evidence each time the drawer opens
  await loadXray();
}
function closeXray() {
  state.xrayOpen = false;
  document.body.classList.remove("xray-is-open");
  const x = document.getElementById("xray");
  x.classList.remove("open"); x.setAttribute("aria-hidden", "true");
  document.getElementById("xray-scrim").classList.remove("open");
  // Back to the control that opened it; if that's gone or wasn't focusable, to the page.
  const opener = state.xrayOpener;
  if (opener?.isConnected && opener !== document.body && !opener.closest("#xray")) opener.focus();
  else document.getElementById("main").focus();
  state.xrayOpener = null;
}

// ------------------------------------------------------------------ developer scenario panel

// Scenario controls: one list of real actions, shown in the desktop drawer and,
// below 1024px, inside the X-Ray sheet.
function devPanel() {
  return `<details class="dev-panel" ${state.devOpen ? "open" : ""} data-dev>
    <summary>Scenario controls <span class="dev-chip">developer</span></summary>${devControls()}</details>`;
}

function devControls() {
  const b = (action, label, extra = "") => `<button type="button" data-action="${action}" ${extra}>${label}</button>`;
  return `<div class="dev-groups">
    <h4>// present mode (drives the real app)</h4>
    <button type="button" data-present="start:recruiter">present · recruiter (about 70 s)</button>
    <button type="button" data-present="start:product">present · product (~1 min)</button>
    <button type="button" data-present="start:engineering">present · engineering (~1.5 min)</button>
    <h4>// story 1–3: corrections</h4>
    ${b("dev-reset", "reset demo (night at rev 4)")}
    ${b("dev-two", state.twoScreens ? "show one screen" : "show both caregivers' screens")}
    ${b("dev-correct", "Parent A corrects final wake → 06:35")}
    ${b("dev-ack-b", "Caregiver B acknowledges on their screen")}
    ${b("dev-past", "Parent A corrects a past night (Oct 8 → 06:15)")}
    <h4>// story 4: truthful upgrade</h4>
    ${b("dev-mount", "camera moved → Flex Stand", 'data-mount="flex_stand"')}
    ${b("dev-mount", "camera back → Floor Stand", 'data-mount="floor_stand"')}
    <h4>// renewal: a plan ended, then the store renews it</h4>
    <button type="button" data-action="studio-run" data-key="plan_ended">build: plan ended, no renewal</button>
    <button type="button" data-action="studio-renewal">store sends the renewal</button>
    <h4>// story 6: purchase channels (simulated store events)</h4>
    ${b("dev-claim", "Apple purchase pending (Milestones)", 'data-channel="apple" data-ref="apple-demo-1" data-status="pending" data-plan="milestones"')}
    ${b("dev-claim", "Apple confirms it late", 'data-channel="apple" data-ref="apple-demo-1" data-status="active" data-plan="milestones"')}
    ${b("dev-claim", "same Google event delivered twice", 'data-channel="google" data-ref="gpa-demo-1" data-status="active" data-plan="memories" data-twice="1"')}
    ${b("dev-claim", "activation code on Nursery", 'data-channel="activation_code" data-ref="CODE-DEMO-1" data-status="active" data-plan="milestones" data-device="dev_cam_demo"')}
    ${b("dev-claim", "same code on Playroom", 'data-channel="activation_code" data-ref="CODE-DEMO-1" data-status="active" data-plan="milestones" data-device="dev_cam_playroom"')}
    ${b("dev-claim", "refund the Apple purchase", 'data-channel="apple" data-ref="apple-demo-1" data-status="revoked" data-plan="milestones"')}
    <h4>// story 5: failure & recovery</h4>
    ${b("dev-concurrent", "send 2 simultaneous requests (same key)")}
    ${b("dev-crash", "interrupt worker after provider success")}
    ${b("dev-worker", "restart worker (reconcile from ledger)")}
    ${b("dev-provider", "next provider reply: decline", 'data-behavior="decline"')}
    ${b("dev-provider", "next provider reply: pending", 'data-behavior="pending"')}
    ${b("dev-provider", "next provider reply: response lost", 'data-behavior="lose_response"')}
    ${b("dev-resolve", "provider settles pending → succeeded", 'data-status="SUCCEEDED"')}
    ${b("dev-resolve", "provider settles pending → failed", 'data-status="FAILED"')}
    <div class="dev-log" role="log" aria-live="polite">${esc(state.log.join("\n"))}</div>
  </div>`;
}

function setScenario(open) {
  state.scenarioOpen = open;
  const d = document.getElementById("dev-drawer");
  d.classList.toggle("open", open);
  d.setAttribute("aria-hidden", String(!open));
  if (open) d.querySelector("button")?.focus();
}

async function devAction(action, el) {
  switch (action) {
    case "dev-reset": {
      observer.disconnect();                 // screens from before the reset are gone
      state.receipts.clear();
      state.resetting = true;
      try {
        await api("/v1/dev/reset", { method: "POST", viewer: VIEWERS[0].id });
        state.studioRun = null;
        await afterReset();
      } finally { state.resetting = false; }
      log("demo reset: night 2026-10-09 at revision 4; no acknowledgments");
      break;
    }
    case "dev-two": state.twoScreens = !state.twoScreens; if (state.twoScreens) location.hash = "#/briefing"; break;
    case "dev-correct": {
      const parent = state.screens[VIEWERS[0].id];
      if (parent.brief.revision < state.latest.revision) await showLatest(VIEWERS[0].id);
      document.getElementById("correction-sheet").dataset.key = "corr-dev-" + crypto.randomUUID();
      await submitCorrection(VIEWERS[0].id, "06:35");
      return;
    }
    case "dev-ack-b": await acknowledge(VIEWERS[1].id); return;
    case "dev-past": {
      const me = await api("/v1/me", { viewer: VIEWERS[0].id });
      const night = me.data.windows.find((w) => w.local_date === "2026-10-08");
      const brief = (await api(`/v1/windows/${night.window_id}/brief`, { viewer: VIEWERS[0].id })).data;
      const target = brief.facts.recorded_sleep.intervals.at(-1);
      const r = await api(`/v1/windows/${night.window_id}/corrections`, { method: "POST", viewer: VIEWERS[0].id, body: {
        target_event_id: target.event_id, corrected_end: "2026-10-09T06:15:00-04:00",
        expected_revision: brief.revision, client_event_id: "past-ui-" + crypto.randomUUID() } });
      log(r.ok ? `Oct 8 corrected: rev ${r.data.previous_revision} → ${r.data.revision}; the week view reads it on next open`
               : `past-night correction refused: ${r.data?.code}`);
      for (const v of VIEWERS) state.screens[v.id].week = await loadWeek(v.id);
      break;
    }
    case "dev-mount": {
      const r = await api("/v1/dev/mount", { method: "POST", viewer: VIEWERS[0].id, body: { mount: el.dataset.mount } });
      log(`camera mount → ${el.dataset.mount}; household setup rev ${r.data?.state_revision} (open offers are now stale)`);
      await loadCommercial();
      break;
    }
    case "dev-concurrent": {
      if (!state.decision?.offer) { log("no open offer: check availability first (camera on Floor Stand)"); return; }
      await buy({ concurrent: 2 });
      return;
    }
    case "dev-crash": {
      if (!state.decision?.offer) { log("no open offer: check availability first"); return; }
      state.crashNext = true;
      log("next worker run will die after the provider commits");
      await buy();
      return;
    }
    case "dev-worker": {
      const r = await api("/v1/dev/worker", { method: "POST", body: {} });
      log(`worker restarted exit=${r.data?.exit_code} ${(r.data?.stdout || "").trim().replace(/\s+/g, " ").slice(0, 200)}`);
      await refreshPurchase();
      break;
    }
    case "dev-claim": {
      const body = { channel: el.dataset.channel, external_ref: el.dataset.ref, event_status: el.dataset.status,
        plan_code: el.dataset.plan, device_id: el.dataset.device || state.device || undefined };
      const times = el.dataset.twice ? 2 : 1;
      for (let i = 0; i < times; i++) {
        const r = await api("/v1/dev/claims", { method: "POST", viewer: VIEWERS[0].id, body });
        if (!r.ok) { log(`${body.channel} ${body.event_status} → refused: ${r.data?.code}`); continue; }
        const a = r.data.after, bf = r.data.before;
        log(`${body.channel} ${body.event_status} on ${r.data.device_id}${r.data.replayed ? " (duplicate: no-op)" : ""}: `
          + `${bf.plan_code}/${bf.state} → ${a.plan_code}/${a.state}, team up to ${a.member_cap}`);
      }
      await loadCommercial();
      break;
    }
    case "dev-provider": {
      await api("/v1/dev/provider/next", { method: "POST", body: { behavior: el.dataset.behavior } });
      log(`provider: next reply will be "${el.dataset.behavior}"`);
      return;
    }
    case "dev-resolve": {
      if (!state.purchase) { log("no purchase yet"); return; }
      const r = await api("/v1/dev/provider/resolve", { method: "POST", body: { operation_key: state.purchase.provider_operation_key, status: el.dataset.status } });
      log(`provider ledger: ${state.purchase.provider_operation_key} → ${r.data?.status}. The app learns this only by reconciling.`);
      return;
    }
  }
  render();
  if (state.xrayOpen) await loadXray();
}

// ------------------------------------------------------------------ events

document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  try {
    await onAction(e, el, el.dataset.action);
  } catch (err) {
    console.error(err);
    if ("disabled" in el) el.disabled = false;
    toast("That didn't work. Reload the page to see where things stand.");
  }
});
window.addEventListener("unhandledrejection", (e) => {
  console.error(e.reason);
  toast("Something didn't load. Reload the page to see where things stand.");
});

async function onAction(e, el, action) {
  if (action === "reload") { location.reload(); return; }
  if (action.startsWith("dev-")) { e.preventDefault(); state.devOpen = true; await devAction(action, el); return; }
  if (action.startsWith("video-")) { e.preventDefault(); await videoAction(action, el); return; }
  switch (action) {
    case "viewer": state.viewer = el.dataset.viewer; if (state.route === "recorded") await loadRecorded(); render(); if (state.xrayOpen) await loadXray(); break;
    case "ack": await acknowledge(el.dataset.viewer); break;
    case "show-latest": await showLatest(el.dataset.viewer); break;
    case "correct-open": openCorrection(el.dataset.viewer); break;
    case "correct-cancel": document.getElementById("correction-sheet").close(); break;
    case "correct-refresh": document.getElementById("correction-sheet").close(); await showLatest(el.dataset.viewer); break;
    case "feature": state.feature = el.dataset.feature; state.decision = null; clearSettledPurchase(); state.purchaseNote = null; render(); break;
    case "camera": state.device = el.dataset.device; state.decision = null; clearSettledPurchase(); state.purchaseNote = null; await loadPreview(); render(); break;
    case "decide": await decide(); break;
    case "buy": el.disabled = true; await buy(); break;
    case "xray-open": e.preventDefault(); await openXray(); break;
    case "scenario-open": setScenario(!state.scenarioOpen); return;
    case "scenario-close": setScenario(false); document.querySelector('[data-action="scenario-open"]')?.focus(); return;
    case "xray-tab": state.xrayTab = el.dataset.tab; break;
    case "proof-tab": state.proofTab = el.dataset.tab; break;
    case "proof-flow": state.proofFlow = el.dataset.flow; break;
    case "access-camera": location.hash = `#/access/${el.dataset.device}`; return;
    case "access-connect-open": openConnectSheet(); return;
    case "access-sheet-cancel": document.getElementById("access-sheet").close(); return;
    case "access-sheet-refresh": document.getElementById("access-sheet").close(); await refreshAccess(); render(); return;
    case "access-verify": el.disabled = true; await verifyAccess(); break;
    case "access-check-again": await refreshAccess(); render(); announce(`${state.accessDetail.headline}.`); break;
    case "access-support": state.accessSupport = el.closest(".lookup-result") ? "lookup" : state.accessDevice; state.accessMountGuide = null; render(); break;
    case "access-mount-guide": state.accessMountGuide = state.accessDevice; state.accessSupport = null; render(); break;
    case "studio-run": el.disabled = true; await runStudio(el.dataset.key); render(); break;
    case "evidence-scenario": {       // the recorded-evidence page's own way out of "not on this plan"
      el.disabled = true;
      await loadStudio();
      if (state.studio?.some((s) => s.key === "evidence_journey")) { await runStudio("evidence_journey"); await storeNext(); }
      else toast("Scenario controls are off on this server.");
      await loadRecorded(); render(); break;
    }
    case "studio-renewal": el.disabled = true; await storeNext(); render(); break;
    case "studio-compare": el.disabled = true; el.textContent = "Building both…"; await runCompare(); render(); break;
    case "compare-open":          // Home → Studio, with the standout comparison built and in view
      el.disabled = true; el.textContent = "Building both cases…";
      if (state.route === "studio") await openComparison();
      else { state.openComparison = true; location.hash = "#/studio"; }      // the route change finishes it
      break;
    case "studio-worker": el.disabled = true; await studioWorker(Boolean(el.dataset.crash)); render(); break;
    case "growth-camera": state.accessDevice = el.dataset.device; state.windowNote = null; await refreshAccess(); await loadGrowth(); render(); break;
    case "window-event": el.disabled = true; await windowEvent(el); render(); break;
    case "customer-try": el.disabled = true; await tryScenario(el.dataset.key); return;
    case "cert-open": state.certOpen = state.certOpen === el.dataset.id ? null : el.dataset.id; render(); break;
    case "outcome-open": {
      await api("/v1/analytics", { method: "POST", body: { event_id: "ui_" + crypto.randomUUID(), name: "access_feature_opened",
        properties: { device_id: state.accessDevice, feature: "nextnap" } } });
      await loadGrowth(); render(); break;
    }
  }
  if (state.xrayOpen && action !== "xray-open") await loadXray();
}
// Radio groups (ARIA radiogroup of buttons): one tab stop per group, arrow keys
// move and select, as a native radio group does.
function syncRadioTabStops(root = document) {
  root.querySelectorAll('[role="radiogroup"]').forEach((group) => {
    const radios = [...group.querySelectorAll('[role="radio"]')];
    const current = radios.find((r) => r.getAttribute("aria-checked") === "true") || radios[0];
    radios.forEach((r) => { r.tabIndex = r === current ? 0 : -1; });
  });
}
new MutationObserver(() => syncRadioTabStops()).observe(document.body, { childList: true, subtree: true });
document.addEventListener("keydown", (e) => {
  const radio = e.target.closest?.('[role="radiogroup"] [role="radio"]');
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
  if (!radio || !step) return;
  e.preventDefault();
  const radios = [...radio.closest('[role="radiogroup"]').querySelectorAll('[role="radio"]')];
  const next = radios[(radios.indexOf(radio) + step + radios.length) % radios.length];
  // The click may re-render the group after an await: find the chosen radio by
  // its data attributes and focus it once it is the checked one.
  const selector = '[role="radio"]' + Object.entries(next.dataset)
    .map(([k, v]) => `[data-${k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}="${CSS.escape(v)}"]`).join("");
  next.click();
  const deadline = performance.now() + 3000;
  (function focusChosen() {
    const el = document.querySelector(selector);
    if (el?.getAttribute("aria-checked") === "true") el.focus();
    else if (performance.now() < deadline) requestAnimationFrame(focusChosen);
  })();
});
document.addEventListener("click", (e) => {
  const summary = e.target.closest("#nav-eng > summary");
  if (summary) state.navEngOpen = !summary.parentElement.open;    // the click is about to toggle it
});
document.addEventListener("submit", (e) => {
  if (e.target.id !== "access-lookup") return;
  e.preventDefault();
  lookupPurchase(e.target);
});
document.addEventListener("toggle", (e) => {
  if (e.target.matches?.("[data-dev]")) state.devOpen = e.target.open;
  if (e.target.matches?.("details.xsec")) state.xsecOpen[e.target.dataset.sec] = e.target.open;
}, true);
document.addEventListener("click", async (e) => {
  const c = e.target.closest("[data-copy]");
  if (!c) return;
  try { await navigator.clipboard.writeText(c.dataset.copy); toast("Copied to the clipboard."); } catch { toast("Copy isn't available here."); }
});
// The sticky chrome (disclosure + Present controls) changes height; everything
// pinned below it reads its height from one variable.
new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty("--chrome-top", `${Math.round(entry.target.getBoundingClientRect().height)}px`);
}).observe(document.getElementById("top-stack"));
document.getElementById("xray-close").addEventListener("click", closeXray);
document.getElementById("xray-scrim").addEventListener("click", closeXray);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.scenarioOpen) { setScenario(false); return; }
  if (e.key === "Escape" && state.xrayOpen) { closeXray(); return; }
  // Below 1280 px the drawer covers the page (with a scrim), so Tab stays inside it.
  if (e.key === "Tab" && state.xrayOpen && !matchMedia("(min-width: 1280px)").matches) {
    const items = [...document.getElementById("xray").querySelectorAll("button, a[href], summary, [tabindex='0']")]
      .filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  // D15: links acting as buttons respond to Space as well as Enter.
  if (e.key === " " && e.target.matches?.('a[role="button"]')) { e.preventDefault(); e.target.click(); }
});
const isDark = () => (document.documentElement.dataset.theme
  ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
function syncThemeToggle() {
  const toggle = document.getElementById("theme-toggle");
  const dark = isDark();
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute("content", dark ? "#0E1124" : "#F7F4EE"));
  toggle.setAttribute("aria-label", dark ? "Switch to the light theme" : "Switch to the dark theme");
  toggle.innerHTML = dark
    ? '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    : '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z"/></svg>';
}
document.getElementById("theme-toggle").addEventListener("click", () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.setItem("pl-theme", document.documentElement.dataset.theme); } catch { /* storage unavailable */ }
  syncThemeToggle();
});
// The saved theme is applied before first paint by a small script in index.html.
syncThemeToggle();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncThemeToggle);

// ------------------------------------------------------------------ Present Mode
// A guided walkthrough that drives the real product. Every step calls the same
// actions a person would (corrections, acknowledgments, decisions, purchases,
// the worker), captions quote live API state, and a step's timer starts only
// once the backend has actually reached the state the caption describes.

const sleepText = () => fmtMinutes(state.latest?.facts.recorded_sleep.minutes);
const coverText = () => `${state.latest?.facts.observation_coverage.pct}%`;
const gapText = () => {
  const g = state.latest?.facts.unobserved.intervals?.[0];
  return g ? `${fmtMinutes(g.minutes)} (${clock(g.start)}–${clock(g.end)})` : "no time";
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function setMount(mount) {
  await api("/v1/dev/mount", { method: "POST", viewer: VIEWERS[0].id, body: { mount } });
  await loadCommercial();
}

async function presentScenario(key) {
  await loadStudio();
  await runStudio(key);
  location.hash = `#/access/${state.accessDevice}`;
  await enterRoute();
}

const PRESENT_STEPS = [
  { pending: "Building the scenario: a plan that ended five days ago…", id: "ended", title: "Renewed, but the app says subscribe?",
    line: () => `${state.accessDetail?.headline}. ${state.accessDetail?.message}`,
    run: async () => { await presentScenario("plan_ended"); },
    until: () => state.accessDetail?.status === "PLAN_EXPIRED", hold: 8000, audiences: ["recruiter", "product", "engineering"] },
  { pending: "The store's renewal arrives…", id: "renewed", title: "The renewal reaches the same camera",
    line: () => `${state.accessDetail?.headline}: ${state.accessDetail?.usable_features.map(featureLabel).join(", ")}. Same camera, no new purchase, no new connect.`,
    run: async () => { await storeNext(); render(); document.getElementById("periods-title")?.scrollIntoView({ behavior: smooth(), block: "center" }); },
    until: () => state.accessDetail?.status === "ACTIVE_FEATURE_USABLE", hold: 8000, audiences: ["recruiter", "product", "engineering"] },
  { pending: "Building the scenario: paid on the website, not on any camera…", id: "paid", title: "I paid for this. Why can't I use it?",
    line: () => `${state.accessDetail?.device.label}: ${state.accessDetail?.headline}. ${state.accessDetail?.stages.filter((s) => s.state === "verified").length} of 5 checks verified; the camera check needs an action.`,
    run: async () => { await presentScenario("paid_not_active"); },
    until: () => state.accessDetail?.status === "VERIFIED_UNBOUND", hold: 7000, audiences: ["recruiter", "product", "engineering"] },
  { pending: "Connecting the purchase, then checking the camera…", id: "connect", title: "Connect, then verify",
    line: () => `Connected without a new purchase, then verified separately: ${state.accessDetail?.usable_features.map(featureLabel).join(", ")} work on the ${state.accessDetail?.device.label.toLowerCase()}.`,
    run: async () => { openConnectSheet(); await wait(fastPresent() ? 100 : 1200); await connectPurchase(); },
    until: () => state.accessVerified && state.accessDetail?.status === "ACTIVE_FEATURE_USABLE", hold: 7000, audiences: ["recruiter", "product", "engineering"] },
  { pending: "Building the scenario: a code used on the wrong camera…", id: "wrong-camera", title: "Paid, wrong camera",
    line: () => `${state.accessLookup?.headline}. Nothing is moved automatically and a second redemption is refused.`,
    run: async () => { await presentScenario("paid_wrong_camera"); document.getElementById("lookup-title")?.scrollIntoView({ behavior: smooth(), block: "center" }); },
    until: () => state.accessLookup?.status === "ALREADY_ASSIGNED", hold: 6000, audiences: ["product", "engineering"] },
  { pending: "Opening the certification lab…", id: "certify", title: "What the release certification says",
    line: () => `${state.certification?.verdict}: ${state.certification?.invariants.filter((i) => i.state === "passed").length} of ${state.certification?.invariants.length} invariants passed in the latest report${state.certification?.build?.report_matches_code ? ", on this exact code" : ", which ran on other code"}. The 4-process soak stays failed and visible.`,
    run: async () => { location.hash = "#/growth"; await enterRoute(); },
    until: () => Boolean(state.certification), hold: 7000, audiences: ["engineering"] },
  { pending: "Loading the synthetic night…", id: "family", title: "One family, one night",
    line: () => `Revision ${state.latest.revision}: ${sleepText()} of recorded sleep, ${coverText()} camera observation.`,
    run: async () => { await devAction("dev-reset"); state.twoScreens = true; location.hash = "#/briefing"; render(); },
    until: () => state.latest?.revision === 4, hold: 6000, audiences: ["product", "engineering"] },
  { id: "gap", title: "Some information is unavailable",
    line: () => `${gapText()} weren't observed. That time stays unknown; nothing is invented to fill it.`,
    run: async () => document.querySelector(".timeline")?.scrollIntoView({ behavior: smooth(), block: "center" }),
    until: () => true, hold: 6000, audiences: ["product", "engineering"] },
  { pending: "Saving the correction…", id: "correction", title: "Parent A corrects the wake time",
    line: () => present.outcome?.ok
      ? `Revision ${present.outcome.data.revision}: recorded sleep is now ${sleepText()}. Coverage is still ${coverText()}. The camera's record is kept.`
      : `Not saved (${present.outcome?.data?.code || "error"}): the night changed on another screen, so this correction was refused. Nothing is claimed for it.`,
    run: async () => { document.getElementById("correction-sheet").dataset.key = "present-" + crypto.randomUUID();
      present.outcome = await submitCorrection(VIEWERS[0].id, "06:35"); },
    until: () => Boolean(present.outcome) && (!present.outcome.ok || state.latest?.revision === present.outcome.data.revision),
    hold: 7000, audiences: ["product", "engineering"] },
  { pending: "Recording Caregiver B's acknowledgment…", id: "caregiver", title: "Caregiver B still has the old screen open",
    // The screen speaks to Caregiver B; the narration speaks about them.
    line: () => state.screens[VIEWERS[1].id]?.ack?.message?.replace(/^You /, "Caregiver B ")
      ?? "Caregiver B acknowledges what their screen shows.",
    run: async () => { await acknowledge(VIEWERS[1].id);
      document.querySelectorAll(".screen-frame")[1]?.querySelector(".ack-card")?.scrollIntoView({ behavior: smooth(), block: "center" }); },
    until: () => Boolean(state.screens[VIEWERS[1].id]?.ack), hold: 8000, audiences: ["product", "engineering"] },
  { pending: "Checking eligibility…", id: "offer", title: "A feature only a plan blocks",
    line: () => state.decision?.offer
      ? `${featureLabel(state.decision.feature)}: the mount is fine, only the plan blocks it. This is the one case that shows an offer: ${state.decision.offer.label}.`
      : "Checking eligibility…",
    run: async () => { state.twoScreens = false; state.viewer = VIEWERS[0].id; location.hash = "#/features";
      state.feature = "developmental_milestones"; await decide();
      document.querySelector(".result-section")?.scrollIntoView({ behavior: smooth(), block: "center" }); },
    until: () => Boolean(state.decision?.offer), hold: 6000, audiences: ["recruiter", "product", "engineering"] },
  { pending: "Moving the camera, then submitting the old offer…", id: "stale-offer", title: "The camera moves before the purchase",
    line: () => "The old offer is refused before any payment call: a plan can't fix a Flex Stand. Nothing was charged.",
    run: async () => { await setMount("flex_stand"); await buy();
      document.querySelector(".result-section")?.scrollIntoView({ behavior: smooth(), block: "center" }); },
    until: () => Boolean(state.purchaseNote), hold: 7000, undo: () => setMount("floor_stand"),
    audiences: ["recruiter", "product", "engineering"] },
  { pending: "Submitting; the worker will be killed after the provider commits…", id: "crash", title: "The worker dies after the provider succeeds",
    line: () => `Intent ${state.purchase?.state ?? "…"}. The simulated provider recorded the charge; the worker process exited before the app recorded it.`,
    run: async () => { await setMount("floor_stand"); await decide(); state.crashNext = true; await buy(); },
    until: () => state.purchase?.state === "SUBMITTED", hold: 7000, audiences: ["engineering"] },
  { pending: "Waiting for the crashed worker's lease, then restarting it…", id: "recover", title: "Restart and reconcile",
    line: () => `${state.purchase?.state === "CONFIRMED" ? "Confirmed" : state.purchase?.state} from the provider's own ledger: one operation, provider attempts ${state.purchase?.provider_record?.attempts ?? "?"}.`,
    run: async () => {
      for (let attempt = 0; attempt < 4 && state.purchase?.state !== "CONFIRMED"; attempt++) {
        await wait(attempt === 0 ? 3500 : 2000);           // the crashed worker's lease must expire first
        await api("/v1/dev/worker", { method: "POST", body: {} });
        await refreshPurchase();
      }
      render(); await openXray();
    },
    until: () => state.purchase?.state === "CONFIRMED", hold: 9000, audiences: ["engineering"] },
  { id: "close", title: "When the facts change",
    line: () => "When the facts change, the answer changes, and the history stays. These cases come from parents' public reports: see Customer evidence.",
    run: async () => { closeXray(); await setMount("floor_stand"); render(); }, until: () => true, hold: 6000,
    audiences: ["recruiter", "product", "engineering"] },
];

const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");
document.addEventListener("click", (e) => {
  const current = e.target.closest?.('.tab-bar a[aria-current="page"]');
  if (current) window.scrollTo({ top: 0, behavior: smooth() });
});
document.addEventListener("click", (e) => {      // on a phone the narration shows two lines; a tap shows it all
  const line = e.target.closest?.(".present-line");
  if (line) line.classList.toggle("expanded");
});
const fastPresent = () => new URLSearchParams(location.search).has("fast");   // automated tests only
const present = { active: false, paused: false, index: 0, steps: [], skip: false, audience: "recruiter" };

function presentBar() {
  let bar = document.getElementById("present-bar");
  if (!bar) {
    bar = document.createElement("section");
    bar.id = "present-bar";
    bar.className = "present-bar";
    bar.setAttribute("aria-label", "Present mode");
    bar.innerHTML = `<div class="present-progress" aria-hidden="true"><span></span></div>
      <div class="present-inner">
        <p class="present-count"></p>
        <div class="present-body"><p class="present-title"></p><p class="present-line" role="status" aria-live="polite" title="Tap to read it all"></p></div>
        <div class="present-controls">
          <button type="button" class="pbtn" data-present="pause"><span class="pbtn-icon">${icon("pause", "icon-sm")}</span><span class="pbtn-text">Pause</span></button>
          <button type="button" class="pbtn" data-present="next">${icon("next", "icon-sm")}<span class="pbtn-text">Next</span></button>
          <button type="button" class="pbtn pbtn-quiet" data-present="exit">${icon("close", "icon-sm")}<span class="pbtn-text">Exit</span></button></div>
      </div>`;
    document.getElementById("present-slot").appendChild(bar);
  }
  return bar;
}

function paintPresent(step, waiting) {
  const bar = presentBar();
  bar.querySelector(".present-count").innerHTML = `<span class="present-step">${present.index + 1}<span class="muted"> / ${present.steps.length}</span></span><span class="present-aud">${esc(present.audience)}</span>`;
  bar.querySelector(".present-title").textContent = step.title;
  // While the backend hasn't reached the step's state, show what is happening,
  // never a caption built from the previous state.
  let line;
  // A step that failed says so; its success caption is never shown for it.
  try {
    line = present.failed ? `This step didn't complete (${present.failed}). Nothing is claimed for it.`
      : waiting && step.pending ? step.pending : step.line();
  } catch { line = "…"; }
  bar.querySelector(".present-line").textContent = line;
  const pause = bar.querySelector("[data-present=pause]");
  pause.querySelector(".pbtn-text").textContent = present.paused ? "Resume" : "Pause";
  pause.querySelector(".pbtn-icon").innerHTML = icon(present.paused ? "play" : "pause", "icon-sm");
  pause.setAttribute("aria-pressed", String(present.paused));
  bar.querySelector(".present-progress span").style.width = `${((present.index + 1) / present.steps.length) * 100}%`;
  bar.classList.toggle("waiting", waiting);
}

async function runPresent(audience = "recruiter") {
  if (present.active) return;
  Object.assign(present, { active: true, paused: false, index: 0, audience,
    steps: PRESENT_STEPS.filter((s) => s.audiences.includes(audience)) });
  document.body.classList.add("presenting");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fast = fastPresent();
  while (present.active && present.index < present.steps.length) {
    const step = present.steps[present.index];
    present.skip = false; present.outcome = null; present.failed = null;
    paintPresent(step, true);
    try { await step.run(); } catch (e) {
      present.failed = e?.data?.code ?? e?.message ?? e?.status ?? "error";
      log(`present: ${step.id} failed (${present.failed})`);
    }
    for (let t = 0; present.active && !present.failed && !step.until() && t < 60; t++) { await wait(500); paintPresent(step, true); }
    paintPresent(step, false);
    const hold = fast ? 300 : reduced ? Math.min(step.hold, 2500) : step.hold;
    for (let left = hold; present.active && !present.skip && left > 0;) {
      await wait(200);
      if (!present.paused) left -= 200;
      paintPresent(step, false);
    }
    if (!present.active) { await step.undo?.(); break; }
    present.index += 1;
  }
  exitPresent();
}

function exitPresent() {
  present.active = false;
  document.body.classList.remove("presenting");
  document.getElementById("present-bar")?.remove();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-present]");
  if (!b) return;
  const action = b.dataset.present;
  if (action === "pause") present.paused = !present.paused;
  else if (action === "next") present.skip = true;
  else if (action === "exit") present.active = false;
  else if (action.startsWith("start:")) runPresent(action.slice(6));
});
document.addEventListener("keydown", (e) => {
  if (!present.active || e.target.closest("input, textarea, select, dialog")) return;
  if (e.key === "ArrowRight") present.skip = true;
  else if (e.key === "Escape") present.active = false;
  else if (e.key === " " && !e.target.closest("button")) { e.preventDefault(); present.paused = !present.paused; }
});

boot().catch((err) => {
  console.error(err);
  document.getElementById("main").innerHTML = `<div class="alert alert-error" role="alert">${icon("xoct")}<div><p class="alert-title">We couldn't load the prototype.</p><p>${["localhost", "127.0.0.1"].includes(location.hostname)
       ? "The server or its database isn't answering. Start it with <code>python scripts/demo.py</code>, then reload this page."
       : "The backend isn't answering. It runs on free infrastructure; wait a minute, then reload this page."}</p></div></div>`;
});
