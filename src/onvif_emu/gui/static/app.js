/* ONVIF Emulator GUI: sign-in, role-aware navigation, RU/EN dictionaries, pages, hotkeys */
const NAV = [
  {group: "overview", items: ["connect", "live"]},
  {group: "camera", items: ["device", "channels", "ptz", "imaging", "osd", "impair"]},
  {group: "events", items: ["events", "analytics", "diag", "fleet"]},
  {group: "access", items: ["users", "security"]},
  {group: "system", items: ["system", "wizard", "help", "cheats"]},
];
const ICONS = {connect: "🔗", live: "🎥", device: "📷", channels: "🎞", ptz: "🕹", imaging: "🌗", osd: "🅰", impair: "🌧",
  events: "🔔", analytics: "🧠", diag: "🧪", fleet: "🗂", users: "👥", security: "🔒", system: "⚙", wizard: "✨",
  help: "❔", cheats: "📋"};
const ROLE_PAGES = {Operator: ["connect", "live", "ptz", "help"], Viewer: ["connect", "live", "help"]};
let SECTIONS = NAV.flatMap(g => g.items);
const GUI_HEADERS = {"X-Emu-Gui": "1"};
let cfg = null;
let status = {};
let me = null;
let section = "connect";
let lang = "ru";
let dict = {};
let videos = {dir: "", files: []};
let pollTimer = null;

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const tip = (text, onvif) => `title="${text}${onvif ? " | ONVIF: " + onvif : ""}"`;
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function lookup(obj, key) {
  return key.split(".").reduce((a, k) => (a == null ? a : a[k]), obj);
}
function t(key, fallback) {
  const v = lookup(dict, key);
  return (typeof v === "string" && v) ? v : (fallback != null ? fallback : key);
}
const isAdmin = () => !me || me.role === "Administrator";
const canPtz = () => !me || me.role !== "Viewer";

function storedLang() { try { return localStorage.getItem("emu_lang") || ""; } catch (e) { return ""; } }
function storeLang(v) { try { localStorage.setItem("emu_lang", v); } catch (e) { /* private mode */ } }

function applyChrome() {
  document.documentElement.lang = lang;
  document.title = t("app");
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  $$("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  $$("select.lang").forEach((el) => { el.value = lang; });
}

async function loadDict(next) {
  lang = next || "ru";
  try { dict = await (await fetch("/api/locales/" + lang)).json(); } catch (e) { dict = {}; }
  applyChrome();
}

/* every API call: session cookie, never the browser's own password pop-up; 401 -> sign-in screen */
async function api(url, opts = {}) {
  const headers = Object.assign({}, GUI_HEADERS, opts.headers || {});
  const r = await fetch(url, Object.assign({credentials: "same-origin"}, opts, {headers}));
  if (r.status === 401 && !url.startsWith("/api/login")) {
    showLogin(t("login.expired"));
    throw new Error("unauthorized");
  }
  if (r.status === 403) toast(t("login.forbidden"), "err");
  return r;
}

/* ------------------------------------------------------------ sign-in screen */
function showLogin(message) {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  $("#app").hidden = true;
  $("#login-screen").hidden = false;
  $("#login-err").textContent = message || "";
  applyChrome();
  setTimeout(() => ($("#login-user").value ? $("#login-pass") : $("#login-user")).focus(), 30);
}

async function doLogin(e) {
  e.preventDefault();
  const btn = $("#login-go");
  btn.disabled = true;
  $("#login-err").textContent = "";
  try {
    const r = await fetch("/api/login", {method: "POST", credentials: "same-origin",
      headers: Object.assign({"Content-Type": "application/json"}, GUI_HEADERS),
      body: JSON.stringify({username: $("#login-user").value.trim(), password: $("#login-pass").value})});
    const j = await r.json().catch(() => ({}));
    if (!j.ok) {
      $("#login-err").textContent = j.error === "too_many" ? t("login.too_many").replace("{s}", j.wait_s) : t("login.bad");
      $("#login-pass").select();
      return;
    }
    $("#login-pass").value = "";
    me = j.me;
    await startApp();
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  await fetch("/api/logout", {method: "POST", credentials: "same-origin", headers: GUI_HEADERS}).catch(() => {});
  me = null;
  showLogin(t("login.signed_out"));
}

/* ------------------------------------------------------------ app start */
async function boot() {
  let meta = {};
  try { meta = await (await fetch("/api/i18n")).json(); } catch (e) { /* offline */ }
  await loadDict(storedLang() || meta.current || "ru");
  if (meta.device) {
    $("#login-device").textContent = [meta.device.name, meta.device.model].filter(Boolean).join(" · ");
    $("#brand-model").textContent = meta.device.model || "";
  }
  if (meta.version) $$(".app-version").forEach(el => { el.textContent = "v" + meta.version; });
  const r = await fetch("/api/me", {credentials: "same-origin", headers: GUI_HEADERS});
  if (r.status === 401) { showLogin(); return; }
  me = (await r.json()).me;
  await startApp();
}

async function startApp() {
  cfg = await (await api("/api/config")).json();
  status = await (await api("/api/status")).json();
  await loadVideos();
  $("#login-screen").hidden = true;
  $("#app").hidden = false;
  SECTIONS = NAV.flatMap(g => g.items).filter(s => isAdmin() || (ROLE_PAGES[me.role] || []).includes(s));
  renderNav();
  renderUserChip();
  $("#btn-save").hidden = !isAdmin();
  $("#pw-banner").hidden = !(me && me.default_password);
  section = SECTIONS.includes(section) ? section : SECTIONS[0];
  if (isAdmin() && !status.wizard_done) section = "wizard";
  const wanted = location.hash.slice(1);  // /#connect, /#diag ...
  if (wanted && SECTIONS.includes(wanted)) section = wanted;
  render();
  poll();
}

async function loadVideos() {
  if (!isAdmin()) { videos = {dir: "", files: []}; return; }
  try { videos = await (await api("/api/videos")).json(); } catch (e) { videos = {dir: "", files: []}; }
}

function renderNav() {
  $("#nav").innerHTML = NAV.map(g => {
    const items = g.items.filter(s => SECTIONS.includes(s));
    if (!items.length) return "";
    return `<div class="nav-group"><div class="nav-title">${t("navgroup." + g.group)}</div>
      ${items.map(s => `<button data-section="${s}" class="${s === section ? "active" : ""}"><span class="ico">${ICONS[s] || "•"}</span><span>${t("nav." + s)}</span></button>`).join("")}</div>`;
  }).join("");
}

function renderUserChip() {
  const name = me && !me.anonymous ? me.username : t("login.anonymous");
  const role = me ? t("roles." + me.role) : "";
  $("#user-chip").innerHTML = `<span class="avatar">${esc((name || "?").slice(0, 1).toUpperCase())}</span>
    <span class="who"><b>${esc(name)}</b><small>${esc(role)}</small></span>`;
  $("#menu-password").hidden = !me || me.anonymous;
  $("#menu-logout").hidden = !me || me.anonymous;
}

function poll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try { status = await (await api("/api/status")).json(); refreshLive(); } catch (e) { /* signed out or offline */ }
  }, 1000);
}

function log(msg) {
  const el = $("#log");
  if (el) el.textContent = new Date().toLocaleTimeString() + " " + msg + "\n" + el.textContent.slice(0, 4000);
}

function toast(msg, kind) {
  log(msg);
  const box = $("#toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.classList.add("out"), 3200);
  setTimeout(() => el.remove(), 3700);
}

/* own password (every account) */
function openPasswordDialog() {
  $("#pw-dialog").hidden = false;
  $("#pw-err").textContent = "";
  ["#pw-old", "#pw-new", "#pw-new2"].forEach(s => { $(s).value = ""; });
  $("#pw-old").focus();
}
async function savePassword(e) {
  e.preventDefault();
  if ($("#pw-new").value !== $("#pw-new2").value) { $("#pw-err").textContent = t("users.mismatch"); return; }
  const r = await api("/api/me/password", {method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({old: $("#pw-old").value, new: $("#pw-new").value})});
  const j = await r.json();
  if (!j.ok) { $("#pw-err").textContent = j.error === "wrong_old_password" ? t("users.wrong_old") : (j.error || t("status.error")); return; }
  $("#pw-dialog").hidden = true;
  if (me) me.default_password = false;
  $("#pw-banner").hidden = true;
  toast(t("users.pw_changed"), "ok");
}

function field(path, label, type, extra = {}) {
  const val = getPath(cfg, path);
  const def = extra.def ?? "";
  const onvif = extra.onvif || "";
  const range = extra.range || "";
  const ph = extra.placeholder || "";
  const defHint = def !== "" ? `<span class="hint">(${t("common.default")} ${def})</span>` : "";
  return `<label ${tip(extra.tip || label, onvif)}>
    ${label} ${range ? `<span class="hint">${range}</span>` : ""} ${defHint}
    <input data-path="${path}" data-type="${type}" value="${esc(val ?? "")}" placeholder="${esc(ph)}" ${extra.min != null ? `min="${extra.min}"` : ""} ${extra.max != null ? `max="${extra.max}"` : ""}/>
    <span class="hint err" data-err="${esc(path)}"></span>
  </label>`;
}

function select(path, label, options, extra = {}) {
  const val = getPath(cfg, path);
  const opts = options.map(o => {
    const value = (o && typeof o === "object") ? o.value : o;
    const words = {"true": t("common.yes"), "false": t("common.no"), ru: "Русский", en: "English"};
    const text = (o && typeof o === "object") ? (o.label || o.value) : (words[o] || o);
    return `<option value="${esc(String(value))}" ${String(value) == String(val) ? "selected" : ""}>${esc(String(text))}</option>`;
  }).join("");
  const hint = extra.hint ? `<p class="hint">${extra.hint}</p>` : "";
  return `<label ${tip(extra.tip || label, extra.onvif || "")}>${label}<select data-path="${path}">${opts}</select></label>${hint}`;
}

function levelChecks() {
  const all = ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
  const cur = new Set((cfg.logging.levels && cfg.logging.levels.length
    ? cfg.logging.levels
    : [cfg.logging.level || "CRITICAL"]).map(String));
  const boxes = all.map(lv =>
    `<label class="chk"><input type="checkbox" data-log-level="${lv}" ${cur.has(lv) ? "checked" : ""}/> ${lv}</label>`
  ).join("");
  return `<fieldset class="log-levels">
    <legend>${t("system.log_level")}</legend>
    <p class="hint">${t("system.log_tip")}</p>
    <div class="row wrap">${boxes}</div>
    <div class="row">
      <button type="button" data-act="log-all">${t("system.log_all")}</button>
      <button type="button" data-act="log-critical-only">${t("system.log_critical_only")}</button>
    </div>
  </fieldset>`;
}

function syncLogLevelsFromDom(root) {
  const selected = [...root.querySelectorAll("[data-log-level]:checked")].map(e => e.dataset.logLevel);
  cfg.logging.levels = selected.length ? selected : ["CRITICAL"];
  cfg.logging.level = cfg.logging.levels[0];
}

function getPath(obj, path) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[k]), obj);
}
function setPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) cur = cur[parts[i]];
  cur[parts.at(-1)] = value;
}

function bindInputs(root) {
  root.querySelectorAll("[data-path]").forEach(el => {
    el.addEventListener("change", () => {
      let v = el.value;
      const ty = el.dataset.type;
      if (ty === "int") v = Number.parseInt(v, 10);
      if (ty === "float") v = Number.parseFloat(v);
      if (ty === "bool") v = el.checked;
      if (el.tagName === "INPUT" && el.type === "checkbox") v = el.checked;
      const err = validate(el.dataset.path, v, el);
      const box = root.querySelector(`[data-err="${el.dataset.path}"]`);
      if (box) box.textContent = err;
      if (!err) setPath(cfg, el.dataset.path, v);
    });
  });
  root.querySelectorAll("[data-log-level]").forEach(el => {
    el.addEventListener("change", () => syncLogLevelsFromDom(root));
  });
}

function validate(path, v, el) {
  if (el && el.min !== "" && Number(v) < Number(el.min)) return `${t("validate.min")} ${el.min}`;
  if (el && el.max !== "" && Number(v) > Number(el.max)) return `${t("validate.max")} ${el.max}`;
  if (path.endsWith("username") && !String(v).trim()) return t("validate.required");
  if (path === "onvif_port" && (v < 1 || v > 65535)) return "1..65535";
  return "";
}

function render() {
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.section === section));
  $("#page-title").textContent = t("nav." + section);
  $("#page-ico").textContent = ICONS[section] || "";
  if (location.hash.slice(1) !== section) history.replaceState(null, "", "#" + section);
  const m = $("#main");
  m.innerHTML = pages[section] ? pages[section]() : "<p>…</p>";
  bindInputs(m);
  m.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => actions[b.dataset.act](b)));
  m.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", () => {
    copyText(b.dataset.copy).then(() => log(t("connect.copied") + " " + b.dataset.copy));
  }));
  m.querySelectorAll("[data-pick]").forEach(el => {
    el.addEventListener("change", () => { if (el.value) useVideoFile(el.dataset.pick, el.value); });
  });
}

function refreshLive() {
  const el = $("#live-ptz");
  if (el && status.ptz) {
    const s = status.ptz[String(ptzChannel())] || {};
    const move = s.moving ? t("status.moving") : t("status.idle");
    el.textContent = `pan ${Number(s.pan||0).toFixed(1)}  tilt ${Number(s.tilt||0).toFixed(1)}  zoom ${Number(s.zoom||0).toFixed(2)}  ${move}`;
  }
  const ev = $("#live-events");
  if (ev && status.events) ev.textContent = JSON.stringify(status.events, null, 0);
  const an = $("#live-an");
  if (an && status.analytics) an.textContent = JSON.stringify(status.analytics, null, 2);
}

function helpEntries() {
  const h = lookup(dict, "help") || {};
  return Object.entries(h).filter(([k]) => k !== "title");
}

function authModeOptions() {
  return [
    {value: "digest", label: t("security.opt_digest")},
    {value: "basic", label: t("security.opt_basic")},
    {value: "wsse", label: t("security.opt_wsse")},
    {value: "none", label: t("security.opt_none")},
  ];
}

function vstatus(id, text, kind) {
  const el = document.querySelector(`[data-vstatus="${id}"]`);
  if (el) { el.textContent = text; el.className = "vstatus " + (kind || ""); }
  log(text);
}

function videoInfo(v) {
  if (!v) return "";
  const mins = v.duration ? ` · ${Math.floor(v.duration / 60)}:${String(Math.round(v.duration % 60)).padStart(2, "0")}` : "";
  return ` (${v.codec} ${v.width}x${v.height}${mins})`;
}

async function afterVideoChange() {
  cfg = await (await api("/api/config")).json();
  await loadVideos();
  render();
}

function uploadVideo(id) {
  const input = document.querySelector(`[data-upload="${id}"]`);
  const file = input && input.files && input.files[0];
  if (!file) { vstatus(id, t("channels.no_file"), "err"); return; }
  const fd = new FormData();
  fd.append("channel", id);
  fd.append("file", file);
  const bar = document.querySelector(`[data-vprog="${id}"]`);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/videos");
  xhr.setRequestHeader("X-Emu-Gui", "1");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round(e.loaded * 100 / e.total);
    if (bar) { bar.hidden = false; bar.value = pct; }
    vstatus(id, `${t("channels.uploading")} ${file.name}: ${pct}% (${Math.round(e.loaded / 1048576)} / ${Math.round(e.total / 1048576)} MB)`);
  };
  xhr.onerror = () => vstatus(id, t("channels.upload_failed") + " " + t("channels.net_error"), "err");
  xhr.onload = async () => {
    if (bar) bar.hidden = true;
    let j = {};
    try { j = JSON.parse(xhr.responseText); } catch (e) { j = {error: `HTTP ${xhr.status}`}; }
    if (xhr.status === 401) { showLogin(t("login.expired")); return; }
    if (!j.ok) { vstatus(id, t("channels.upload_failed") + " " + (j.error || xhr.status), "err"); return; }
    await afterVideoChange();
    vstatus(id, `${t("channels.uploaded")} ${j.file.name}${videoInfo(j.file.video)} — ${t("channels.playing")}`, "ok");
  };
  vstatus(id, `${t("channels.uploading")} ${file.name}…`);
  xhr.send(fd);
}

async function useVideoFile(id, path) {
  if (!path || !path.trim()) { vstatus(id, t("channels.no_path"), "err"); return; }
  vstatus(id, t("channels.checking") + " " + path);
  const r = await api("/api/videos/local", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel: Number(id), path})});
  const j = await r.json();
  if (!j.ok) { vstatus(id, t("channels.path_failed") + " " + (j.error || r.status), "err"); return; }
  await afterVideoChange();
  vstatus(id, `${t("channels.playing")}: ${j.path}${videoInfo(j.video)}`, "ok");
}

async function browseVideo(id, dir) {
  const box = document.querySelector(`[data-browser="${id}"]`);
  if (!box) return;
  const r = await api("/api/videos/browse?dir=" + encodeURIComponent(dir || "") + (id === "osdimg" ? "&kind=image" : ""));
  const j = await r.json();
  if (!j.ok) {
    if (dir) { browseVideo(id, ""); return; }
    vstatus(id, j.error || t("status.error"), "err");
    return;
  }
  const sep = j.dir.includes("\\") || /^[A-Z]:/i.test(j.dir) ? "\\" : "/";
  const join = (name) => (j.dir ? j.dir.replace(/[\\/]$/, "") + sep + name : name);
  const up = j.parent !== null && j.parent !== undefined
    ? `<li><a href="#" data-bdir="${esc(j.parent)}">⬑ ..</a></li>` : "";
  const dirs = j.dirs.map(d => `<li><a href="#" data-bdir="${esc(join(d))}">📁 ${esc(d)}</a></li>`).join("");
  const files = j.files.map(f => `<li><a href="#" data-bfile="${esc(f.path)}">🎞 ${esc(f.name)}</a>
      <span class="hint">${Math.max(1, Math.round(f.size / 1048576))} MB</span></li>`).join("");
  box.innerHTML = `<div class="row"><code class="uri">${esc(j.dir || t("channels.drives"))}</code>
      <button data-bclose="1">✕</button></div>
    <ul>${up}${dirs}${files || (j.dir ? `<li class="hint">${t("channels.no_videos")}</li>` : "")}</ul>`;
  box.hidden = false;
  box.querySelectorAll("[data-bdir]").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); browseVideo(id, a.dataset.bdir); }));
  box.querySelectorAll("[data-bfile]").forEach(a => a.addEventListener("click", (e) => {
    e.preventDefault();
    const el = document.querySelector(`[data-local="${id}"]`);
    if (el) el.value = a.dataset.bfile;
    box.hidden = true;
    if (id === "osdimg") useOsdImage(a.dataset.bfile); else useVideoFile(id, a.dataset.bfile);
  }));
  box.querySelector("[data-bclose]").addEventListener("click", () => { box.hidden = true; });
}

const ANCHORS = ["top-left", "top-center", "top-right", "middle-left", "center", "middle-right",
  "bottom-left", "bottom-center", "bottom-right", "custom"];
const anchorOptions = () => ANCHORS.map(a => ({value: a, label: t("anchor." + a, a)}));

function chk(path, label, tipText) {
  const v = getPath(cfg, path);
  return `<label class="chk" ${tip(tipText || label, "")}><input type="checkbox" data-path="${path}" data-type="bool" ${v ? "checked" : ""}/> ${label}</label>`;
}

function colorField(path, label) {
  const v = getPath(cfg, path) || "#FFFFFF";
  return `<label>${label}<input type="color" data-path="${path}" data-type="str" value="${esc(v)}"/></label>`;
}

function burstFields(path) {
  return `${select(path + ".mode", t("impair.when"), [{value: "bursts", label: t("impair.bursts")}, {value: "constant", label: t("impair.constant")}])}
    ${field(path + ".period_s", t("impair.period"), "float", {min: 1, max: 86400})}
    ${field(path + ".duration_s", t("impair.duration"), "float", {min: 0.1, max: 86400})}`;
}

function stampCard(kind) {
  const p = `osd.${kind}`;
  const fmts = kind === "time" ? ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%H:%M:%S  CAM 1"]
    : ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%a %d.%m.%Y"];
  return `<div class="card"><h2>${t("osd." + kind + "_title")}</h2>
    ${chk(p + ".enabled", t("osd.show"))}
    <div class="grid">
      <label>${t("osd.format")}<input data-path="${p}.format" data-type="str" value="${esc(getPath(cfg, p + ".format") || "")}" list="fmt-${kind}"/>
        <datalist id="fmt-${kind}">${fmts.map(f => `<option value="${esc(f)}">`).join("")}</datalist>
        <span class="hint">${t("osd.format_hint")}</span></label>
      ${select(p + ".position", t("osd.position"), anchorOptions())}
      ${field(p + ".x", t("osd.x"), "float", {min: 0, max: 100})}
      ${field(p + ".y", t("osd.y"), "float", {min: 0, max: 100})}
      ${field(p + ".size", t("osd.stamp_size"), "int", {min: 6, max: 300, def: "36"})}
      ${select(p + ".font", t("osd.font"), [{value: "sans", label: t("osd.font_sans")}, {value: "mono", label: t("osd.font_mono")}])}
      ${colorField(p + ".color", t("osd.text_color"))}
      ${field(p + ".opacity", t("osd.text_opacity"), "float", {min: 0, max: 1, def: "1"})}
      ${colorField(p + ".bg_color", t("osd.bg_color"))}
      ${field(p + ".bg_opacity", t("osd.bg_opacity"), "float", {min: 0, max: 1, def: "0.4", tip: t("osd.bg_tip")})}
      ${chk(p + ".outline", t("osd.outline"))}
    </div></div>`;
}

function imageCard() {
  const p = "osd.image";
  const img = cfg.osd.image || {};
  return `<div class="card"><h2>${t("osd.image_title")}</h2>
    ${chk(p + ".enabled", t("osd.show"))}
    <p>${t("osd.img_now")}: ${img.path ? `<code class="uri">${esc(img.path)}</code>` : "—"}</p>
    <p class="vstatus" data-vstatus="osdimg"></p>
    <div class="opt"><h4>${t("osd.img_opt1")}</h4>
      <div class="row"><input type="file" accept="image/*,.png,.jpg,.jpeg,.bmp,.gif,.webp,.tif,.tiff" data-imgfile="1"/>
        <button data-act="osd-img-upload">${t("osd.img_upload")}</button></div></div>
    <div class="opt"><h4>${t("osd.img_opt2")}</h4>
      <div class="row"><input class="grow" data-local="osdimg" value="${esc(img.path || "")}" placeholder="D:\\logo.png"/>
        <button data-act="osd-img-browse">${t("channels.browse")}</button>
        <button data-act="osd-img-local">${t("channels.apply")}</button></div>
      <div class="browser" data-browser="osdimg" hidden></div></div>
    <div class="grid">
      ${select(p + ".position", t("osd.position"), anchorOptions())}
      ${field(p + ".x", t("osd.x"), "float", {min: 0, max: 100})}
      ${field(p + ".y", t("osd.y"), "float", {min: 0, max: 100})}
      ${field(p + ".size", t("osd.img_size"), "float", {min: 1, max: 100, def: "15"})}
      ${field(p + ".opacity", t("osd.img_opacity"), "float", {min: 0, max: 1, def: "1"})}
      ${field(p + ".transparent_color", t("osd.img_key"), "str", {placeholder: "#FFFFFF", tip: t("osd.img_key_tip")})}
      ${field(p + ".key_similarity", t("osd.img_key_sim"), "float", {min: 0.01, max: 1, def: "0.15"})}
      ${colorField(p + ".bg_color", t("osd.bg_color"))}
      ${field(p + ".bg_opacity", t("osd.img_bg_opacity"), "float", {min: 0, max: 1, def: "0", tip: t("osd.bg_tip")})}
    </div></div>`;
}

async function useOsdImage(path) {
  if (!path || !path.trim()) { vstatus("osdimg", t("channels.no_path"), "err"); return; }
  const r = await api("/api/osd/image/local", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({path})});
  const j = await r.json();
  if (!j.ok) { vstatus("osdimg", t("channels.path_failed") + " " + (j.error || r.status), "err"); return; }
  cfg = await (await api("/api/config")).json();
  render();
  vstatus("osdimg", t("osd.img_on") + " " + j.path, "ok");
}

const IMPAIR_DEFAULT = {enabled: false, rain: 0, noise: 0, pixelate: 1, shake: 0,
  shake_burst: {mode: "bursts", period_s: 15, duration_s: 3}, cover: "off",
  cover_burst: {mode: "bursts", period_s: 60, duration_s: 5}, tamper_events: true,
  loss: 0, stutter: 0, freeze: false, freeze_burst: {mode: "bursts", period_s: 30, duration_s: 2},
  bitrate: "normal", bitrate_burst: {mode: "bursts", period_s: 20, duration_s: 4}, bitrate_cap: 0,
  outage: false, outage_unit: "minutes", outage_every_min: 1, outage_every_max: 5, outage_len_min: 5, outage_len_max: 20};
const IMPAIR_PRESETS = {
  rain: {enabled: true, rain: 0.7, noise: 0.15},
  vandal: {enabled: true, shake: 0.7, shake_burst: {mode: "bursts", period_s: 20, duration_s: 4}, cover: "spray",
    cover_burst: {mode: "bursts", period_s: 45, duration_s: 8}, tamper_events: true},
  badnet: {enabled: true, loss: 3, stutter: 15, freeze: true, freeze_burst: {mode: "bursts", period_s: 25, duration_s: 3},
    bitrate: "vbr_jumps", outage: true, outage_unit: "minutes", outage_every_min: 1, outage_every_max: 4, outage_len_min: 5, outage_len_max: 15},
  lowq: {enabled: true, noise: 0.5, pixelate: 4, bitrate_cap: 256},
  off: {},
};

async function applyImpair(i) {
  const ch = cfg.channels[i];
  vstatus("imp" + ch.id, t("impair.applying"));
  const r = await api(`/api/channels/${ch.id}/impair`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(ch.impair)});
  const j = await r.json();
  if (!j.ok) { vstatus("imp" + ch.id, t("status.error") + ": " + (j.error || r.status), "err"); return; }
  ch.impair = j.impair;
  vstatus("imp" + ch.id, j.impair.enabled ? t("impair.applied") : t("impair.cleared"), "ok");
}

let ptzCh = null;
function ptzChannels() {
  return (cfg.channels || []).filter(c => c.enabled && c.ptz_enabled !== false).map(c => c.id);
}
function ptzChannel() {
  const list = ptzChannels();
  if (!list.includes(ptzCh)) ptzCh = list.length ? list[0] : 1;
  return ptzCh;
}
document.addEventListener("change", (e) => {
  if (e.target.id === "ptz-ch") { ptzCh = Number(e.target.value); render(); }
});

/* ---------------------------------------------------------------- accounts (administrator) */
let usersData = null;
async function loadUsers() {
  usersData = await (await api("/api/users")).json();
  if (section === "users") render();
}
function userChannelsCell(u, key) {
  const chans = (usersData && usersData.channels) || [];
  const all = !u.channels || !u.channels.length;
  if (u.role === "Administrator") return `<span class="hint">${t("users.all_channels")}</span>`;
  return `<div class="chips" data-uch="${esc(key)}">
    <label class="chk"><input type="checkbox" data-uall="1" ${all ? "checked" : ""}/> ${t("users.all_channels")}</label>
    ${chans.map(c => `<label class="chk"><input type="checkbox" data-ucid="${c.id}" ${!all && u.channels.includes(c.id) ? "checked" : ""} ${all ? "disabled" : ""}/> ${c.id}</label>`).join("")}
  </div>`;
}
function roleSelect(u, attr) {
  return `<select ${attr} ${u.main ? "disabled" : ""}>${["Administrator", "Operator", "Viewer"].map(r =>
    `<option value="${r}" ${u.role === r ? "selected" : ""}>${t("roles." + r)}</option>`).join("")}</select>`;
}
function readChannels(key) {
  const box = document.querySelector(`[data-uch="${key}"]`);
  if (!box) return [];
  if (box.querySelector("[data-uall]").checked) return [];
  return [...box.querySelectorAll("[data-ucid]:checked")].map(el => Number(el.dataset.ucid));
}
document.addEventListener("change", (e) => {
  const box = e.target.closest && e.target.closest("[data-uch]");
  if (box && e.target.dataset.uall !== undefined) {
    box.querySelectorAll("[data-ucid]").forEach(el => { el.disabled = e.target.checked; });
  }
  if (e.target.dataset && e.target.dataset.urole !== undefined) {
    const row = e.target.closest("tr");
    const cell = row && row.querySelector(".uch-cell");
    if (cell) cell.innerHTML = userChannelsCell({role: e.target.value, channels: [], main: false}, e.target.dataset.urole);
  }
});
async function userCall(method, url, body) {
  const r = await api(url, {method, headers: {"Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json();
  if (!j.ok) { toast(t("status.error") + ": " + (j.error || r.status), "err"); return false; }
  usersData.users = j.users;
  render();
  return true;
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (e) { /* ignore */ }
  ta.remove();
  return Promise.resolve();
}

function uriCell(uri) {
  return `<code class="uri">${esc(uri)}</code> <button class="copy" data-copy="${esc(uri)}" title="${esc(t("connect.copy"))}">⧉</button>`;
}

function profileRows(ch) {
  return ch.profiles.map(p => `<tr>
      <td>${p.tier + 1} · ${t("connect.q_" + p.quality)}</td>
      <td><code>${esc(p.token)}</code></td>
      <td>${esc(p.codec)} ${esc(p.resolution)}@${p.fps}</td>
      <td>${uriCell(p.rtsp)}</td>
    </tr>`).join("");
}

const pages = {
  connect() {
    const c = status.connect || {channels: []};
    const noAuth = c.auth === "none";
    const user = me && !me.anonymous ? me.username : "admin";
    const chans = c.channels.map(ch => `<div class="card">
      <h2>${t("connect.channel")} ${ch.id} — ${esc(ch.name)} ${ch.ptz ? '<span class="hint">PTZ</span>' : ""}</h2>
      <table>
        <tr><th>${t("connect.profile")}</th><th>${t("connect.token")}</th><th>${t("connect.stream")}</th><th>RTSP URI</th></tr>
        ${profileRows(ch)}
        <tr><td colspan="3">${t("connect.snapshot")}</td><td>${uriCell(ch.snapshot)}</td></tr>
        <tr><td colspan="3">${t("connect.hls")}</td><td>${uriCell(ch.hls)}</td></tr>
      </table>
    </div>`).join("");
    return `<div class="card"><h1>${t("connect.title")}</h1>
      <p class="hint">${t("connect.hint")}</p>
      ${c.rtsp_error ? `<p class="err"><b>${t("connect.rtsp_off")}</b> ${esc(c.rtsp_error)}</p>` : ""}
      <table>
        <tr><th>${t("connect.device_service")}</th><td>${uriCell(c.device_service || "")}</td></tr>
        <tr><th>${t("connect.auth")}</th><td>${noAuth ? t("connect.auth_none")
          : `<b>${esc(c.auth)}</b> · ${t("connect.login")} <code>${esc(user)}</code> · ${t("connect.auth_same")}`}</td></tr>
        <tr><th>WS-Discovery</th><td>${c.discovery ? t("connect.disc_on") : t("connect.disc_off")}</td></tr>
        <tr><th>${t("connect.rtsp_creds")}</th><td>${noAuth ? t("connect.auth_none") : c.rtsp_credentials_embedded ? t("connect.creds_in_uri") : t("connect.creds_prompt")}</td></tr>
      </table>
      <h2>${t("connect.odm_title")}</h2>
      <ol class="steps">${[1, 2, 3, 4, 5].map(i => `<li>${t("connect.odm_" + i)}</li>`).join("")}</ol>
      <h2>${t("connect.vlc_title")}</h2>
      <ol class="steps">${[1, 2].map(i => `<li>${t("connect.vlc_" + i)}</li>`).join("")}</ol>
      <pre>${esc(`ffplay -rtsp_transport tcp "${c.channels[0] && c.channels[0].profiles[0] ? c.channels[0].profiles[0].rtsp : ""}"`)}</pre>
      <h2>${t("connect.faq_title")}</h2>
      <ul class="steps">${[1, 2, 3, 4].map(i => `<li>${t("connect.faq_" + i)}</li>`).join("")}</ul>
    </div>${chans}`;
  },
  wizard() {
    return `<div class="card"><h1>${t("wizard.title")}</h1>
      <p class="hint">${t("wizard.hint")}</p>
      <div class="grid">
        ${select("auth.mode", t("wizard.auth"), authModeOptions(), {onvif: "HTTP Digest / WS-UsernameToken", tip: t("wizard.tip_auth")})}
        <label ${tip(t("wizard.tip_password"), "SetUser")}>${t("wizard.admin_pw")}<input id="wiz-pw" type="password" autocomplete="new-password" placeholder="${esc(t("wizard.admin_pw_ph"))}"/></label>
        ${field("onvif_port", t("wizard.port"), "int", {min: 1025, max: 65535, range: "1025-65535", def: "8080", onvif: "GetNetworkProtocols"})}
        ${field("network.rtsp_port", t("wizard.rtsp_port"), "int", {min: 1025, max: 65535, def: "8554"})}
      </div>
      <p class="hint">${t("wizard.auth_hint")}</p>
      <div class="row">
        <button data-act="save">${t("common.save")}</button>
        <button class="primary" data-act="wizard-done">${t("common.done")}</button>
      </div></div>`;
  },
  device() {
    return `<div class="card"><h1>${t("device.title")} <button data-act="help-here">?</button></h1>
      <div class="grid">
        ${field("device.manufacturer", t("device.manufacturer"), "str", {onvif: "GetDeviceInformation", def: "Vibic"})}
        ${field("device.model", t("device.model"), "str", {def: "EMU-CAM-1"})}
        ${field("device.firmware", t("device.firmware"), "str", {def: "1.0.0"})}
        ${field("device.serial", t("device.serial"), "str")}
        ${field("device.hardware_id", t("device.hardware_id"), "str")}
        ${field("device.hostname", t("device.hostname"), "str", {onvif: "GetHostname"})}
        ${field("device.name", t("device.name"), "str")}
        ${field("device.location", t("device.location"), "str")}
        ${field("device.timezone", t("device.timezone"), "str", {placeholder: "UTC or Europe/Moscow", tip: t("device.tip_tz")})}
      </div>
      <div class="row"><button data-act="save">${t("common.save")}</button><button data-act="reset-device">${t("common.reset")}</button></div></div>`;
  },
  users() {
    if (!usersData) { loadUsers(); return `<div class="card"><p class="hint">…</p></div>`; }
    const rows = usersData.users.map(u => `<tr class="${u.enabled ? "" : "muted"}">
        <td><b>${esc(u.username)}</b>${u.main ? ` <span class="badge">${t("users.main")}</span>` : ""}</td>
        <td>${roleSelect(u, `data-urole="${esc(u.username)}"`)}</td>
        <td class="uch-cell">${userChannelsCell(u, u.username)}</td>
        <td><label class="switch"><input type="checkbox" data-uenabled="${esc(u.username)}" ${u.enabled ? "checked" : ""} ${u.main ? "disabled" : ""}/><span></span></label></td>
        <td><input type="password" autocomplete="new-password" data-upw="${esc(u.username)}" placeholder="${esc(t("users.new_pw"))}"/></td>
        <td class="actions"><button class="primary" data-act="user-save" data-name="${esc(u.username)}">${t("common.save")}</button>
          ${u.main ? "" : `<button class="danger" data-act="user-delete" data-name="${esc(u.username)}">${t("users.delete")}</button>`}</td>
      </tr>`).join("");
    const nu = {role: "Viewer", channels: [], main: false};
    return `<div class="card"><h1>${t("users.title")}</h1>
      <p class="hint">${t("users.hint")}</p>
      <div class="table-wrap"><table class="users">
        <tr><th>${t("users.user")}</th><th>${t("users.role")}</th><th>${t("users.channels")}</th><th>${t("users.enabled")}</th><th>${t("users.password")}</th><th></th></tr>
        ${rows}</table></div></div>
      <div class="card"><h2>${t("users.add")}</h2>
        <div class="grid">
          <label>${t("users.user")}<input id="nu-name" autocomplete="off" placeholder="operator1"/></label>
          <label>${t("users.password")}<input id="nu-pw" type="password" autocomplete="new-password"/></label>
          <label>${t("users.role")}${roleSelect(nu, 'id="nu-role" data-urole="__new"')}</label>
        </div>
        <div class="uch-cell" style="margin-top:8px">${userChannelsCell(nu, "__new")}</div>
        <div class="row"><button class="primary" data-act="user-add">${t("users.add_btn")}</button></div>
      </div>
      <div class="card"><h2>${t("users.roles_title")}</h2>
        <ul class="roles">${["Administrator", "Operator", "Viewer"].map(r => `<li><b>${t("roles." + r)}</b> — ${t("users.role_" + r)}</li>`).join("")}</ul>
        <p class="hint">${t("users.storage_hint")}</p></div>`;
  },
  live() {
    const conn = {};
    ((status.connect && status.connect.channels) || []).forEach(c => { conn[c.id] = c; });
    const chans = (cfg.channels || []).filter(c => c.enabled && conn[c.id]);
    if (!chans.length) return `<div class="card"><h1>${t("live.title")}</h1><p class="hint">${t("live.none")}</p></div>`;
    return `<h1>${t("live.title")}</h1><p class="hint">${t("live.hint")}</p>
      <div class="live-grid">${chans.map(c => {
        const profiles = conn[c.id].profiles;
        const path = c.rtsp_path + (profiles.length > 1 ? "_sub" : "");
        return `<div class="card live">
          <div class="live-head"><b>${t("connect.channel")} ${c.id} — ${esc(c.name)}</b>
            <select data-live="${c.id}">${profiles.map(p => `<option value="${esc(c.rtsp_path + ["", "_sub", "_third"][p.tier])}" ${c.rtsp_path + ["", "_sub", "_third"][p.tier] === path ? "selected" : ""}>${t("connect.q_" + p.quality)} · ${esc(p.resolution)}</option>`).join("")}</select></div>
          <iframe class="preview" data-live-frame="${c.id}" src="/preview/${esc(path)}/" allow="autoplay; fullscreen"></iframe>
          <div class="row">
            <a class="btn" href="/snapshot/${c.id}.jpg" target="_blank">${t("live.snapshot")}</a>
            ${canPtz() && c.ptz_enabled !== false ? `<button data-act="goto-ptz" data-channel="${c.id}">${t("nav.ptz")}</button>` : ""}
            ${uriCell(profiles[0].rtsp)}
          </div></div>`;
      }).join("")}</div>`;
  },
  channels() {
    const types = ["file", "rtsp", "http", "hls", "dash", "udp", "v4l2", "x11grab", "testsrc"];
    const lib = (videos.files || []).map(f => `<option value="${esc(f.path)}">${esc(f.name)} (${Math.max(1, Math.round((f.size || 0) / 1048576))} MB)</option>`).join("");
    const conn = {};
    ((status.connect && status.connect.channels) || []).forEach(c => { conn[c.id] = c; });
    const cards = (cfg.channels || []).map((ch, i) => `<div class="card">
      <h2>${t("connect.channel")} ${ch.id} — ${esc(ch.name)}</h2>
      ${conn[ch.id] ? `<table><tr><th>${t("connect.profile")}</th><th>${t("connect.token")}</th><th>${t("connect.stream")}</th><th>RTSP URI</th></tr>${profileRows(conn[ch.id])}</table>
        <p class="hint">${t("connect.more")}</p>` : `<p class="hint">${t("connect.disabled")}</p>`}
      <h3>${t("channels.source")}</h3>
      <p>${t("channels.now")}: <b>${esc(ch.source.type)}</b> ${ch.source.uri ? `<code class="uri">${esc(ch.source.uri)}</code>` : ""}
        <span class="hint">→ ${ch.encoder.codec} ${ch.encoder.width}x${ch.encoder.height}@${ch.encoder.fps}</span></p>
      <p class="vstatus" data-vstatus="${ch.id}"></p>
      ${((status.health && status.health.pipeline_state) || {})[ch.id] === "ended" ? `<p class="err">${t("channels.ended")}</p>` : ""}
      <div class="row">
        ${chk(`channels.${i}.source.loop`, t("channels.loop"))}
        <label>${t("channels.end_action")}<select data-path="channels.${i}.source.end_action">
          ${["stop", "freeze", "black"].map(v => `<option value="${v}" ${ch.source.end_action === v ? "selected" : ""}>${t("channels.end_" + v)}</option>`).join("")}
        </select></label>
        <button data-act="restart-channel" data-channel="${ch.id}">${t("channels.restart")}</button>
      </div>
      <p class="hint">${t("channels.loop_hint")}</p>
      <div class="opt">
        <h4>${t("channels.opt1")}</h4>
        <p class="hint">${t("channels.opt1_hint")} <code>${esc(videos.dir || "video")}</code></p>
        <div class="row">
          <input type="file" accept="video/*,.mp4,.m4v,.mov,.qt,.mkv,.webm,.avi,.divx,.wmv,.asf,.flv,.f4v,.ts,.m2ts,.mts,.m2t,.mpg,.mpeg,.mpe,.m1v,.m2v,.vob,.3gp,.3g2,.ogv,.mxf,.dv,.h264,.264,.h265,.265,.hevc,.y4m,.mjpeg,.mjpg" data-upload="${ch.id}"/>
          <button data-act="upload-video" data-channel="${ch.id}">${t("channels.upload_btn")}</button>
        </div>
        <progress data-vprog="${ch.id}" max="100" value="0" hidden></progress>
        ${lib ? `<label>${t("channels.pick")}<select data-pick="${ch.id}"><option value="">—</option>${lib}</select></label>` : ""}
      </div>
      <div class="opt">
        <h4>${t("channels.opt2")}</h4>
        <p class="hint">${t("channels.opt2_hint")}</p>
        <div class="row">
          <input class="grow" data-local="${ch.id}" value="${ch.source.type === "file" ? esc(ch.source.uri || "") : ""}" placeholder="${esc(t("channels.path_ph"))}"/>
          <button data-act="browse-video" data-channel="${ch.id}">${t("channels.browse")}</button>
          <button data-act="use-local" data-channel="${ch.id}">${t("channels.apply")}</button>
        </div>
        <div class="browser" data-browser="${ch.id}" hidden></div>
      </div>
      <details class="opt"><summary>${t("channels.opt3")}</summary>
        <div class="grid">
          ${select(`channels.${i}.source.type`, t("channels.source_type"), types)}
          <label>${t("channels.uri")}<input value="${esc(ch.source.uri || "")}" data-path="channels.${i}.source.uri" placeholder="rtsp://… | http://… | udp://…"/></label>
        </div>
        <p class="hint">${t("channels.opt3_hint")}</p>
      </details>
      <iframe class="preview" src="/preview/${esc(ch.rtsp_path)}_sub/" allow="autoplay; fullscreen" loading="lazy"></iframe>
    </div>`).join("");
    return `<h1>${t("channels.title")}</h1>
      <p class="hint">${t("channels.formats")}</p>
      ${cards}<div class="row"><button data-act="save">${t("common.save")}</button></div>`;
  },
  ptz() {
    const pcid = String(ptzChannel());
    const chOpts = ptzChannels().map(id => {
      const c = cfg.channels.find(x => x.id === id);
      return `<option value="${id}" ${id === ptzChannel() ? "selected" : ""}>${t("connect.channel")} ${id} — ${esc(c ? c.name : "")}</option>`;
    }).join("");
    if (!ptzChannels().length) return `<div class="card"><h1>${t("ptz.title")}</h1><p class="hint">${t("ptz.none")}</p></div>`;
    const presets = (status.presets && status.presets[pcid]) || [];
    const presetRows = presets.map(p => `<tr>
        <td>${esc(p.name)}</td>
        <td class="hint">${Number(p.pan).toFixed(1)}, ${Number(p.tilt).toFixed(1)}, ${Number(p.zoom).toFixed(2)}</td>
        <td>
          <button data-act="preset-goto" data-token="${esc(p.token)}">${t("ptz.goto")}</button>
          <button data-act="preset-remove" data-token="${esc(p.token)}">${t("ptz.remove")}</button>
        </td>
      </tr>`).join("");
    return `<div class="card"><h1>${t("ptz.title")}</h1>
      <div class="row"><label class="inline">${t("ptz.channel")} <select id="ptz-ch">${chOpts}</select></label></div>
      <p id="live-ptz" class="ok">…</p>
      ${isAdmin() ? `<div class="grid">
        ${field("ptz.pan_min", t("ptz.pan_min"), "float", {min: -180, max: 180, onvif: "GetNode XRange"})}
        ${field("ptz.pan_max", t("ptz.pan_max"), "float", {min: -180, max: 180})}
        ${field("ptz.tilt_min", t("ptz.tilt_min"), "float", {min: -90, max: 90})}
        ${field("ptz.tilt_max", t("ptz.tilt_max"), "float")}
        ${field("ptz.zoom_min", t("ptz.zoom_min"), "float", {min: 0, max: 1})}
        ${field("ptz.zoom_max", t("ptz.zoom_max"), "float", {min: 0, max: 1})}
        ${field("ptz.pan_speed", t("ptz.pan_speed"), "float", {min: 0.1, max: 180, def: "30"})}
      </div>` : ""}
      <p class="hint">${t("ptz.hint")}</p>
      <div class="row">
        <button data-act="ptz" data-pan="-1">◀</button>
        <button data-act="ptz" data-tilt="1">▲</button>
        <button data-act="ptz" data-tilt="-1">▼</button>
        <button data-act="ptz" data-pan="1">▶</button>
        <button data-act="ptz-stop">${t("ptz.stop")}</button>
        <button data-act="ptz" data-zoom="1">${t("ptz.zoom_in")}</button>
        <button data-act="ptz" data-zoom="-1">${t("ptz.zoom_out")}</button>
      </div>
      <div class="row">
        <button data-act="ptz-iris" data-delta="0.1">${t("ptz.iris_open")}</button>
        <button data-act="ptz-iris" data-delta="-0.1">${t("ptz.iris_close")}</button>
        <button data-act="ptz-focus" data-delta="0.1">${t("ptz.focus_far")}</button>
        <button data-act="ptz-focus" data-delta="-0.1">${t("ptz.focus_near")}</button>
        <button data-act="home-goto">${t("ptz.home_goto")}</button>
        <button data-act="home-set">${t("ptz.home_set")}</button>
      </div>
      <h2>${t("ptz.presets")}</h2>
      <div class="row">
        <input id="preset-name" placeholder="${t("ptz.preset_name")}"/>
        <button data-act="preset-set">${t("ptz.preset_save")}</button>
      </div>
      <table>
        <tr><th>${t("ptz.preset_name")}</th><th>${t("ptz.preset_pos")}</th><th></th></tr>
        ${presetRows || `<tr><td colspan="3" class="hint">${t("ptz.presets_empty")}</td></tr>`}
      </table>
      </div>`;
  },
  imaging() {
    return `<div class="card"><h1>${t("imaging.title")}</h1><div class="grid">
      ${field("imaging.brightness", t("imaging.brightness"), "int", {min: 0, max: 100, def: "50", onvif: "SetImagingSettings", tip: t("imaging.tip_eq")})}
      ${field("imaging.contrast", t("imaging.contrast"), "int", {min: 0, max: 100, def: "50"})}
      ${field("imaging.saturation", t("imaging.saturation"), "int", {min: 0, max: 100, def: "50"})}
      ${field("imaging.sharpness", t("imaging.sharpness"), "int", {min: 0, max: 100, def: "50"})}
      ${field("imaging.iris", t("imaging.iris"), "float", {min: 0, max: 1, def: "0.5"})}
      ${field("imaging.focus", t("imaging.focus"), "float", {min: 0, max: 1, def: "0.5"})}
    </div><div class="row"><button data-act="save">${t("common.save")}</button><button data-act="reset-img">${t("common.reset")}</button></div></div>`;
  },
  osd() {
    return `<h1>${t("osd.title")}</h1>
      <p class="hint">${t("osd.page_hint")}</p>
      ${stampCard("time")}${stampCard("date")}${imageCard()}
      <div class="card"><h2>${t("osd.hud_title")}</h2>
      ${select("osd.enabled", t("osd.enabled"), ["true", "false"], {tip: t("osd.tip")})}
      ${field("osd.style.color", t("osd.color"), "str", {placeholder: "#00FF88", def: "#00FF88"})}
      ${field("osd.style.opacity", t("osd.opacity"), "float", {min: 0, max: 1, def: "0.85"})}
      ${field("osd.style.size", t("osd.size"), "int", {min: 8, max: 96, def: "28"})}
      ${select("osd.style.position", t("osd.position"), ["top-left", "top-right", "bottom-left", "bottom-right", "center"].map(v => ({value: v, label: t("anchor." + v)})))}
      ${select("osd.style.event_position", t("osd.event_position"), ["top-left", "top-center", "top-right", "middle-left", "center", "middle-right", "bottom-left", "bottom-center", "bottom-right"].map(v => ({value: v, label: t("anchor." + v)})))}
      ${field("osd.style.duration_ms", t("osd.duration"), "int", {min: 100, max: 30000, def: "1500"})}
      <div class="row"><button data-act="save">${t("common.save")}</button></div></div>`;
  },
  impair() {
    const states = (status.health && status.health.pipeline_state) || {};
    const cards = (cfg.channels || []).map((ch, i) => {
      const b = `channels.${i}.impair`;
      const presets = ["rain", "vandal", "badnet", "lowq", "off"].map(p =>
        `<button data-act="impair-preset" data-channel="${i}" data-preset="${p}">${t("impair.p_" + p)}</button>`).join("");
      const st = states[ch.id] === "outage" ? `<span class="err">${t("impair.state_outage")}</span>` : "";
      return `<div class="card"><h2>${t("connect.channel")} ${ch.id} — ${esc(ch.name)} ${st}</h2>
        ${chk(b + ".enabled", t("impair.enabled"))}
        <div class="row">${presets}</div>
        <p class="vstatus" data-vstatus="imp${ch.id}"></p>
        <h3>${t("impair.optics")}</h3><div class="grid">
          ${field(b + ".rain", t("impair.rain"), "float", {min: 0, max: 1, tip: t("impair.rain_tip")})}
          ${field(b + ".noise", t("impair.noise"), "float", {min: 0, max: 1})}
          ${field(b + ".pixelate", t("impair.pixelate"), "int", {min: 1, max: 32, tip: t("impair.pixelate_tip")})}
        </div>
        <h3>${t("impair.vandal")}</h3><div class="grid">
          ${field(b + ".shake", t("impair.shake"), "float", {min: 0, max: 1})}
          ${burstFields(b + ".shake_burst")}
          ${select(b + ".cover", t("impair.cover"), ["off", "black", "blur", "spray"].map(v => ({value: v, label: t("impair.cover_" + v)})))}
          ${burstFields(b + ".cover_burst")}
          ${chk(b + ".tamper_events", t("impair.tamper_events"))}
        </div>
        <h3>${t("impair.network")}</h3><div class="grid">
          ${field(b + ".loss", t("impair.loss"), "float", {min: 0, max: 50, tip: t("impair.loss_tip")})}
          ${field(b + ".stutter", t("impair.stutter"), "float", {min: 0, max: 90})}
          ${chk(b + ".freeze", t("impair.freeze"))}
          ${burstFields(b + ".freeze_burst")}
          ${select(b + ".bitrate", t("impair.bitrate"), ["normal", "cbr", "vbr_jumps"].map(v => ({value: v, label: t("impair.br_" + v)})))}
          ${burstFields(b + ".bitrate_burst")}
          ${field(b + ".bitrate_cap", t("impair.cap"), "int", {min: 0, max: 100000, tip: t("impair.cap_tip")})}
        </div>
        <h3>${t("impair.outage_title")}</h3><div class="grid">
          ${chk(b + ".outage", t("impair.outage"))}
          ${select(b + ".outage_unit", t("impair.unit"), ["seconds", "minutes", "hours"].map(v => ({value: v, label: t("impair.u_" + v)})))}
          ${field(b + ".outage_every_min", t("impair.every_from"), "float", {min: 0.05})}
          ${field(b + ".outage_every_max", t("impair.every_to"), "float", {min: 0.05})}
          ${field(b + ".outage_len_min", t("impair.len_from"), "float", {min: 0.5})}
          ${field(b + ".outage_len_max", t("impair.len_to"), "float", {min: 0.5})}
        </div>
        <div class="row"><button data-act="impair-apply" data-channel="${i}">${t("impair.apply")}</button></div>
      </div>`;
    }).join("");
    return `<h1>${t("impair.title")}</h1><p class="hint">${t("impair.hint")}</p>${cards}`;
  },
  events() {
    return `<div class="card"><h1>${t("events.title")}</h1>
      ${select("events.mode", t("events.mode"), ["timer", "motion", "manual"].map(v => ({value: v, label: t("events.mode_" + v)})), {onvif: "CreatePullPointSubscription"})}
      ${field("events.motion_interval", t("events.motion_interval"), "float", {min: 1, max: 3600, def: "15"})}
      ${field("events.tamper_interval", t("events.tamper_interval"), "float", {min: 1, max: 3600, def: "60"})}
      <p id="live-events"></p>
      <div class="row">
        <button data-act="trig" data-kind="motion">${t("events.motion")}</button>
        <button data-act="trig" data-kind="tamper">${t("events.tamper")}</button>
        <button data-act="trig" data-kind="analytics">${t("events.analytics")}</button>
      </div></div>`;
  },
  analytics() {
    return `<div class="card"><h1>${t("analytics.title")}</h1>
      ${field("analytics.max_objects", t("analytics.max_objects"), "int", {min: 0, max: 32, def: "4"})}
      ${field("analytics.box_speed", t("analytics.box_speed"), "float", {min: 0, max: 1, def: "0.08"})}
      <pre id="live-an"></pre></div>`;
  },
  security() {
    return `<div class="card"><h1>${t("security.title")}</h1>
      ${select("auth.mode", t("security.mode"), authModeOptions(), {onvif: "HTTP/WS-Security", tip: t("security.tip"), hint: t("wizard.auth_hint")})}
      <p class="hint">${t("security.accounts_hint")} <a href="#users" data-goto="users">${t("nav.users")}</a></p>
      <label ${tip(t("security.embed_rtsp"), "")}>
        ${t("security.embed_rtsp")}
        <input type="checkbox" data-path="auth.embed_rtsp_credentials" data-type="bool" ${cfg.auth.embed_rtsp_credentials ? "checked" : ""}/>
      </label>
      <p class="hint">${t("security.embed_rtsp_hint")}</p>
      <div class="row"><button data-act="save">${t("common.save")}</button></div></div>`;
  },
  system() {
    const h = status.health || {};
    return `<div class="card"><h1>${t("system.title")}</h1>
      <p>${t("system.status")} <b class="ok">${h.status || "?"}</b> ${t("system.uptime")} ${h.uptime_s || 0}s ${t("system.version")} ${h.version || ""}</p>
      <p class="hint">${t("system.hint")}</p>
      ${field("host", t("system.host"), "str", {def: "0.0.0.0", placeholder: "0.0.0.0", tip: t("system.host_tip")})}
      ${field("onvif_port", t("wizard.port"), "int", {min: 1, max: 65535, range: "1-65535", def: "8080", tip: t("system.port_tip")})}
      ${field("network.rtsp_port", t("wizard.rtsp_port"), "int", {min: 1, max: 65535, range: "1-65535", def: "8554"})}
      ${field("public_host", t("system.public_host"), "str", {placeholder: "192.168.1.50"})}
      ${select("language", t("system.language"), ["ru", "en"])}
      ${levelChecks()}
      ${field("logging.file", t("system.log_file"), "str", {placeholder: "onvif-emulator.log", tip: t("system.log_tip")})}
      ${field("logging.dir", t("system.log_dir"), "str", {placeholder: "logs", tip: t("system.log_dir_tip")})}
      ${select("logging.console", t("system.log_console"), ["true", "false"], {tip: t("system.log_console_tip")})}
      ${field("logging.max_mb", t("system.log_max_mb"), "float", {def: "5", range: "0.1-1024"})}
      ${field("logging.backups", t("system.log_backups"), "int", {def: "3", range: "0-100"})}
      ${select("auto_port", t("system.auto_port"), ["true", "false"], {tip: t("system.auto_port_tip")})}
      ${field("video_dir", t("system.video_dir"), "str", {placeholder: "video", tip: t("system.video_tip")})}
      <div class="row">
        <button data-act="save">${t("system.save_hotkey")}</button>
        <button data-act="export">${t("system.export_hotkey")}</button>
        <button data-act="import">${t("system.import_hotkey")}</button>
      </div></div>`;
  },
  help() {
    return `<div class="card"><h1>${t("help.title")}</h1>
      <p>${t("help.hotkeys")}</p>
      ${helpEntries().filter(([k]) => k !== "hotkeys").map(([k, v]) => `<h2>${t("nav." + k)}</h2><p>${v}</p>`).join("")}
    </div>`;
  },
  cheats() {
    const checks = status.odm_checks || {};
    const items = ["discovery", "auth-digest", "auth-basic", "auth-wsse", "auth-none", "live", "snapshot", "ptz", "presets", "events", "analytics"];
    const rows = items.map(k => `<tr><td>${t("cheats.items." + k, k)}</td><td><input type="checkbox" data-odm="${k}" ${checks[k] ? "checked" : ""}/></td></tr>`).join("");
    return `<div class="card"><h1>${t("cheats.title")}</h1>
      <p class="hint">${t("cheats.cli_note")}</p>
      <pre>${esc(`onvif-emulator --no-gui --channel 1:source=./clip.mp4
onvif-emulator --auth none --headless
onvif-emulator --add-user "operator1:secret:Operator:1,2" --add-user "guard:pass:Viewer:3"
onvif-emulator --reset-admin                      # admin / admin
onvif-emulator --impair "1:rain=0.6,loss=3"
onvif-emulator doctor
onvif-emulator cheat-sheet launch`)}</pre>
      <h2>${t("cheats.web_title")}</h2>
      <ul class="roles">${["sign_in", "reset", "roles", "api"].map(k => `<li>${t("cheats.web_" + k)}</li>`).join("")}</ul>
      <h2>${t("cheats.odm")}</h2>
      <table>${rows}</table>
      <h2>${t("cheats.exit")}</h2>
      <p>${t("cheats.exit_line")}</p>
    </div>`;
  }
};

const actions = {
  async save() {
    const r = await api("/api/config", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(cfg)});
    const j = await r.json();
    if (!j.ok) toast(j.error || t("status.error"), "err");
    if (j.ok && j.apply && j.apply.port_error) {
      log(t("system.port_error") + " " + j.apply.port_error);
      cfg.onvif_port = j.apply.port;
    }
    if (j.ok && j.apply && j.apply.port_changed) {
      // the listener moved: follow it once the new socket is up
      log(t("system.port_moved") + " " + j.apply.port);
      setTimeout(() => { location.port = String(j.apply.port); }, 800);
    }
    if (j.ok) {
      toast(t("status.saved"), "ok");
      await loadVideos();
      render();
    }
  },
  "upload-video"(btn) { uploadVideo(btn.dataset.channel); },
  async "user-save"(btn) {
    const name = btn.dataset.name;
    const body = {};
    const role = document.querySelector(`[data-urole="${name}"]`);
    const en = document.querySelector(`[data-uenabled="${name}"]`);
    const pw = document.querySelector(`[data-upw="${name}"]`);
    const u = usersData.users.find(x => x.username === name);
    if (!u.main) {
      body.role = role.value;
      body.enabled = en.checked;
      body.channels = role.value === "Administrator" ? [] : readChannels(name);
    }
    if (pw && pw.value) body.password = pw.value;
    if (await userCall("PUT", "/api/users/" + encodeURIComponent(name), body)) toast(t("users.saved") + " " + name, "ok");
  },
  async "user-delete"(btn) {
    const name = btn.dataset.name;
    if (btn.dataset.confirm !== "1") { btn.dataset.confirm = "1"; btn.textContent = t("users.confirm"); return; }
    if (await userCall("DELETE", "/api/users/" + encodeURIComponent(name))) toast(t("users.deleted") + " " + name, "ok");
  },
  async "user-add"() {
    const role = $("#nu-role").value;
    const body = {username: $("#nu-name").value.trim(), password: $("#nu-pw").value, role,
      channels: role === "Administrator" ? [] : readChannels("__new"), enabled: true};
    if (!body.username || !body.password) { toast(t("users.need_name_pw"), "err"); return; }
    if (await userCall("POST", "/api/users", body)) toast(t("users.added") + " " + body.username, "ok");
  },
  "goto-ptz"(btn) { ptzCh = Number(btn.dataset.channel); section = "ptz"; render(); },
  async "restart-channel"(btn) {
    await api(`/api/channels/${btn.dataset.channel}/restart`, {method: "POST"});
    vstatus(btn.dataset.channel, t("channels.restarted"), "ok");
  },
  "impair-apply"(btn) { applyImpair(Number(btn.dataset.channel)); },
  "impair-preset"(btn) {
    const i = Number(btn.dataset.channel);
    const p = IMPAIR_PRESETS[btn.dataset.preset] || {};
    cfg.channels[i].impair = JSON.parse(JSON.stringify(Object.assign({}, IMPAIR_DEFAULT, p)));
    render();
    applyImpair(i);
  },
  async "osd-img-upload"() {
    const input = document.querySelector("[data-imgfile]");
    const file = input && input.files && input.files[0];
    if (!file) { vstatus("osdimg", t("channels.no_file"), "err"); return; }
    const fd = new FormData();
    fd.append("file", file);
    vstatus("osdimg", t("channels.uploading") + " " + file.name + "…");
    const r = await api("/api/osd/image", {method: "POST", body: fd});
    const j = await r.json();
    if (!j.ok) { vstatus("osdimg", t("channels.upload_failed") + " " + (j.error || r.status), "err"); return; }
    cfg = await (await api("/api/config")).json();
    render();
    vstatus("osdimg", t("osd.img_on") + " " + j.path, "ok");
  },
  "osd-img-local"() {
    const el = document.querySelector('[data-local="osdimg"]');
    useOsdImage(el ? el.value : "");
  },
  "osd-img-browse"() {
    const el = document.querySelector('[data-local="osdimg"]');
    browseVideo("osdimg", el && el.value ? el.value.replace(/[\\/][^\\/]*$/, "") : "");
  },
  "use-local"(btn) {
    const el = document.querySelector(`[data-local="${btn.dataset.channel}"]`);
    useVideoFile(btn.dataset.channel, el ? el.value : "");
  },
  "browse-video"(btn) {
    const el = document.querySelector(`[data-local="${btn.dataset.channel}"]`);
    const cur = el && el.value ? el.value.replace(/[\\/][^\\/]*$/, "") : (videos.dir || "");
    browseVideo(btn.dataset.channel, cur);
  },
  async "wizard-done"() {
    const pw = $("#wiz-pw") && $("#wiz-pw").value;
    if (pw) {
      const r = await api("/api/users/admin", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({password: pw})});
      const j = await r.json();
      if (!j.ok) { toast(j.error || t("status.error"), "err"); return; }
      if (me) me.default_password = false;
      $("#pw-banner").hidden = true;
    }
    await actions.save();
    await api("/api/wizard", {method: "POST"});
    section = "connect"; render();
  },
  async export() {
    const r = await api("/api/export?fmt=yaml");
    const text = await r.text();
    const blob = new Blob([text], {type: "text/yaml"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "onvif-emu.yaml";
    a.click();
    URL.revokeObjectURL(a.href);
  },
  async import() {
    const text = prompt(t("system.import_prompt"));
    if (!text) return;
    await api("/api/import", {method: "POST", headers: {"Content-Type": "application/json"}, body: text});
    await startApp();
  },
  "reset-device"() { cfg.device.manufacturer = "Vibic"; cfg.device.model = "EMU-CAM-1"; render(); },
  "reset-img"() { cfg.imaging.brightness = cfg.imaging.contrast = 50; render(); },
  "log-all"() {
    cfg.logging.levels = ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
    cfg.logging.level = "TRACE";
    render();
  },
  "log-critical-only"() {
    cfg.logging.levels = ["CRITICAL"];
    cfg.logging.level = "CRITICAL";
    render();
  },
  async ptz(btn) {
    await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
      cmd: "continuous", pan: Number(btn.dataset.pan || 0), tilt: Number(btn.dataset.tilt || 0), zoom: Number(btn.dataset.zoom || 0)
    })});
  },
  async "ptz-stop"() { await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "stop"})}); },
  async "ptz-iris"(btn) {
    await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "iris", delta: Number(btn.dataset.delta)})});
  },
  async "ptz-focus"(btn) {
    await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "focus", delta: Number(btn.dataset.delta)})});
  },
  async "home-goto"() { await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "home_goto"})}); },
  async "home-set"() { await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "home_set"})}); },
  async "preset-set"() {
    const nameEl = $("#preset-name");
    const name = (nameEl && nameEl.value.trim()) || "Preset";
    await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "preset_set", name})});
    status = await (await api("/api/status")).json();
    render();
  },
  async "preset-goto"(btn) {
    await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "preset_goto", token: btn.dataset.token})});
  },
  async "preset-remove"(btn) {
    await api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "preset_remove", token: btn.dataset.token})});
    status = await (await api("/api/status")).json();
    render();
  },
  async trig(btn) { await api("/api/events/trigger", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({kind: btn.dataset.kind})}); },
  "help-here"() { openHelp(); }
};

function openHelp() {
  const m = $("#help-modal");
  m.hidden = false;
  applyChrome();
  $("#help-body").innerHTML = helpEntries().map(([k, v]) => `<h3 id="h-${k}">${k === "hotkeys" ? t("help.title") : t("nav." + k)}</h3><p>${v}</p>`).join("");
}
function closeHelp() { $("#help-modal").hidden = true; $("#palette").hidden = true; }

document.addEventListener("click", async (e) => {
  const nav = e.target.closest("nav button");
  if (nav) { section = nav.dataset.section; render(); }
  if (e.target.id === "btn-save") actions.save();
  if (e.target.id === "btn-help") openHelp();
  if (e.target.id === "help-close") closeHelp();
  if (e.target.dataset && e.target.dataset.odm) {
    await api("/api/odm-check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({key: e.target.dataset.odm, pass: e.target.checked})});
  }
});
document.addEventListener("change", async (e) => {
  if (!e.target.classList || !e.target.classList.contains("lang")) return;
  storeLang(e.target.value);
  await loadDict(e.target.value);
  if (cfg && !$("#app").hidden) { renderNav(); renderUserChip(); render(); }
});
$("#login-form").addEventListener("submit", doLogin);
$("#login-eye").addEventListener("click", () => {
  const f = $("#login-pass");
  f.type = f.type === "password" ? "text" : "password";
});
$("#user-chip").addEventListener("click", () => { $("#user-menu").hidden = !$("#user-menu").hidden; });
document.addEventListener("click", (e) => {
  if (!e.target.closest("#user-box")) $("#user-menu").hidden = true;
});
$("#menu-logout").addEventListener("click", logout);
$("#menu-password").addEventListener("click", () => { $("#user-menu").hidden = true; openPasswordDialog(); });
$("#pw-banner-go").addEventListener("click", openPasswordDialog);
$("#pw-form").addEventListener("submit", savePassword);
$("#pw-cancel").addEventListener("click", () => { $("#pw-dialog").hidden = true; });
$("#menu-log").addEventListener("click", () => { $("#user-menu").hidden = true; $("#logpane").hidden = !$("#logpane").hidden; });
$("#help-search").addEventListener("input", (e) => {
  const q = e.target.value.toLowerCase();
  $("#help-body").querySelectorAll("h3,p").forEach(n => {
    n.style.display = n.textContent.toLowerCase().includes(q) ? "" : "none";
  });
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeHelp(); $("#pw-dialog").hidden = true; }
  if (e.key === "F1") { e.preventDefault(); if (!$("#app").hidden) openHelp(); }
  if ($("#app").hidden) return;  // sign-in screen: no hotkeys
  if (e.ctrlKey && e.key.toLowerCase() === "s") { e.preventDefault(); if (isAdmin()) actions.save(); }
  if (e.ctrlKey && e.key.toLowerCase() === "e") { e.preventDefault(); actions.export(); }
  if (e.ctrlKey && e.key.toLowerCase() === "i") { e.preventDefault(); actions.import(); }
  if (e.ctrlKey && e.key.toLowerCase() === "l") { e.preventDefault(); $("#logpane").hidden = !$("#logpane").hidden; }
  if (e.ctrlKey && e.key.toLowerCase() === "q") { e.preventDefault(); window.close(); }
  if (e.ctrlKey && e.key === ",") { e.preventDefault(); section = "system"; render(); }
  if (e.ctrlKey && e.key.toLowerCase() === "k") {
    e.preventDefault();
    const pal = $("#palette"); pal.hidden = !pal.hidden;
    $("#palette-list").innerHTML = SECTIONS.map(s => `<li>${t("nav." + s)}</li>`).join("");
    $("#palette-q").focus();
  }
  if (e.ctrlKey && e.key >= "1" && e.key <= "9") {
    e.preventDefault();
    section = SECTIONS[Number(e.key) - 1] || section;
    render();
  }
  if (e.altKey && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "-", "Equal", "Minus"].includes(e.key)) {
    e.preventDefault();
    const map = {ArrowLeft: {pan: -1}, ArrowRight: {pan: 1}, ArrowUp: {tilt: 1}, ArrowDown: {tilt: -1}, "+": {zoom: 1}, Equal: {zoom: 1}, "-": {zoom: -1}, Minus: {zoom: -1}};
    if (canPtz()) api("/api/ptz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({channel: ptzChannel(), cmd: "continuous", channel: ptzChannel(), ...{pan: 0, tilt: 0, zoom: 0, ...(map[e.key] || {})}})});
  }
});
$("#palette-q").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const q = e.target.value.toLowerCase();
    const hit = SECTIONS.find(s => s.startsWith(q) || s.includes(q) || t("nav." + s).toLowerCase().includes(q));
    if (hit) { section = hit; render(); closeHelp(); }
  }
});

boot();
document.addEventListener("change", (e) => {
  if (e.target.dataset && e.target.dataset.live) {
    const frame = document.querySelector(`[data-live-frame="${e.target.dataset.live}"]`);
    if (frame) frame.src = "/preview/" + e.target.value + "/";
  }
});
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("[data-goto]");
  if (a) { e.preventDefault(); section = a.dataset.goto; render(); }
});
