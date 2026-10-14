/* Diagnostics + Fleet + channel constructor pages. Loaded after app.js; uses its globals (pages, actions, api, cfg, ...). */
(function () {
  const dt = (k) => t("diag." + k);  // texts: locales/*.json -> "diag"
  const KINDS = ["motion", "tamper", "analytics", "relay", "contact", "metadata", "custom"];
  const AN_KINDS = ["line", "field", "cellmotion", "motionregion", "counting", "loitering"];
  const anKind = (topic) => (AN_KINDS.includes(topic) ? topic : "field");
  const json = (method, body) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const opt = (arr, cur, labelFn) => arr.map((v) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(labelFn ? labelFn(v) : v)}</option>`).join("");

  let diag = null;
  let fleet = null;
  let timer = null;
  const form = {kind: "tamper", name: "", channel: 0, device: "this", schedule: "interval", interval: 30, unit: "seconds",
    range_min: 5, range_max: 60, hold_s: 3, repeat: 0, start_delay_s: 0, action: "pulse", target: "", topic: "", data: "", source: "", push_url: ""};

  function kv(text) {
    const out = {};
    String(text || "").split(/\r?\n/).forEach((l) => { const i = l.indexOf("="); if (i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1).trim(); });
    return out;
  }
  function formRule() {
    const f = form;
    const r = {kind: f.kind, name: f.name, channel: Number(f.channel) || 0, schedule: f.schedule, unit: f.unit,
      interval: Number(f.interval), range_min: Number(f.range_min), range_max: Number(f.range_max), hold_s: Number(f.hold_s),
      repeat: Number(f.repeat), start_delay_s: Number(f.start_delay_s), action: f.action, target: f.target, topic: f.topic,
      data: kv(f.data), source: kv(f.source), push_url: f.push_url};
    if (f.device === "all") r.device = 0;
    return r;
  }

  async function loadDiag() {
    try { diag = await (await api("/api/diag")).json(); } catch (e) { diag = null; }
  }
  async function loadFleet() {
    try { fleet = await (await api("/api/fleet")).json(); } catch (e) { fleet = null; }
  }

  function ledRow(kind, items) {
    return Object.entries(items).map(([tok, v]) => {
      const on = kind === "relay" ? v.active : v;
      return `<button data-dact="${kind}" data-tok="${esc(tok)}" data-on="${on ? 1 : 0}" style="border-color:${on ? "var(--err)" : "var(--line)"}">
        <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${on ? "var(--err)" : "#2f6b47"};margin-right:6px"></span>${esc(tok)} · ${on ? dt("on") : dt("off")}</button>`;
    }).join(" ");
  }

  function field(label, id, val, type = "text", extra = "") {
    return `<label>${label}<input data-f="${id}" type="${type}" value="${esc(val)}" ${extra}/></label>`;
  }
  function sel(label, id, arr, cur, fn) { return `<label>${label}<select data-f="${id}">${opt(arr, cur, fn)}</select></label>`; }

  function ruleForm() {
    const f = form;
    const timing = f.schedule === "interval"
      ? field(dt("every"), "interval", f.interval, "number", 'min="0.05" step="any"')
      : f.schedule === "random"
        ? field(dt("from"), "range_min", f.range_min, "number", 'min="0.05" step="any"') + field(dt("to"), "range_max", f.range_max, "number", 'min="0.05" step="any"')
        : "";
    const k = f.kind;
    const topicField = k === "analytics"
      ? sel(dt("an_kind"), "topic", AN_KINDS, anKind(f.topic), (v) => dt("an_" + v))
      : k === "custom" ? field(dt("topic"), "topic", f.topic) : "";
    return `<p class="hint">${dt("hint_kind")} ${f.schedule === "manual" ? "" : dt("hint_timer")}
      ${k === "analytics" && anKind(f.topic) === "counting" ? dt("hint_counting") : ""}</p>
    <div class="grid">
      ${sel(dt("kind"), "kind", KINDS, f.kind, (v) => dt("k_" + v))}
      ${topicField}
      ${field(dt("name"), "name", f.name)}
      ${field(dt("channel"), "channel", f.channel, "number", 'min="0" max="64"')}
      ${sel(dt("device"), "device", ["this", "all"], f.device, (v) => (v === "all" ? dt("dev_all") : dt("dev_this")))}
      ${sel(dt("schedule"), "schedule", ["manual", "interval", "random"], f.schedule, (v) => dt("s_" + v))}
      ${timing}
      ${f.schedule === "manual" ? "" : sel(dt("unit"), "unit", ["seconds", "minutes", "hours"], f.unit, (v) => dt("u_" + v))}
      ${field(dt("hold"), "hold_s", f.hold_s, "number", 'min="0" step="any"')}
      ${f.schedule === "manual" ? "" : field(dt("repeat"), "repeat", f.repeat, "number", 'min="0"')}
      ${f.schedule === "manual" ? "" : field(dt("delay"), "start_delay_s", f.start_delay_s, "number", 'min="0" step="any"')}
      ${sel(dt("action"), "action", ["pulse", "toggle", "on", "off"], f.action, (v) => dt("a_" + v))}
      ${k === "relay" || k === "contact" ? field(dt("target"), "target", f.target) : ""}
      ${field(dt("push"), "push_url", f.push_url)}
      <label>${dt("data")}<textarea data-f="data" rows="3">${esc(f.data)}</textarea></label>
      <label>${dt("src")}<textarea data-f="source" rows="3">${esc(f.source)}</textarea></label>
    </div>`;
  }

  function rulesTable() {
    const rules = (diag && diag.config.rules) || [];
    if (!rules.length) return `<p class="hint">${dt("none")}</p>`;
    const st = {};
    ((diag.status && diag.status.rules) || []).forEach((r) => { st[r.id] = r; });
    const rows = rules.map((r) => {
      const s = st[r.id] || {};
      const sched = r.schedule === "manual" ? dt("s_manual")
        : r.schedule === "interval" ? `${r.interval} ${dt("u_" + r.unit)}`
          : `${r.range_min}–${r.range_max} ${dt("u_" + r.unit)}`;
      return `<tr>
        <td><input type="checkbox" data-dact="toggle" data-id="${esc(r.id)}" ${r.enabled ? "checked" : ""}/></td>
        <td>${esc(r.name)}</td><td>${esc(r.kind === "analytics" ? dt("an_" + anKind(r.topic)) : dt("k_" + r.kind))}${r.channel ? " ch" + r.channel : ""}${r.device ? " dev" + r.device : ""}</td>
        <td>${esc(sched)}</td><td>${s.next_in_s != null ? Math.max(0, s.next_in_s) + " s" : "—"}</td><td>${s.fired || 0}</td>
        <td><button data-dact="fire" data-id="${esc(r.id)}">${dt("fire")}</button>
            <button data-dact="del" data-id="${esc(r.id)}">${dt("del")}</button></td></tr>`;
    }).join("");
    return `<table><tr><th>${dt("enabled")}</th><th>${dt("table_name")}</th><th>${dt("table_kind")}</th><th>${dt("table_sched")}</th>
      <th>${dt("table_next")}</th><th>${dt("table_fired")}</th><th></th></tr>${rows}</table>`;
  }

  function limitsLine() {
    if (!diag || !diag.limits) return "";
    const e = diag.limits.estimate || {};
    const g = diag.limits.governor || {};
    return `${dt("load")}: ${e.devices || 0} dev · ${e.channels || 0} ch · ${e.streams || 0} streams · ${e.mpx_per_s || 0} Mpx/s
      (${(e.cores_needed || 0).toFixed ? Number(e.cores_needed).toFixed(1) : e.cores_needed}/${e.cores || "?"} cores) ·
      ${dt("governor")}: cpu ${g.cpu_pct ?? "?"}% mem ${g.mem_pct ?? "?"}% · ${dt("lvl")} ${diag.limits.shed_level || 0} · ${dt("dropped")} ${diag.limits.dropped_events || 0}`;
  }

  pages.diag = function () {
    const c = (diag && diag.config) || {relays: 0, inputs: 0, webhooks: [], notify: true};
    return `<div class="card"><h1>${dt("diag_title")}</h1>
      <p class="hint" id="diag-limits">${limitsLine()}</p>
      <h2>${dt("io")}</h2>
      <div class="grid">
        <label>${dt("relays")}<input type="number" min="0" max="32" data-dcfg="relays" value="${c.relays}"/></label>
        <label>${dt("inputs")}<input type="number" min="0" max="32" data-dcfg="inputs" value="${c.inputs}"/></label>
      </div>
      <div id="diag-io"><p>${ledRow("relay", (diag && diag.status.relays) || {})}</p><p>${ledRow("input", (diag && diag.status.inputs) || {})}</p></div>
      <h2>${dt("rules")}</h2>
      <div id="diag-rules">${rulesTable()}</div>
      <h2>${dt("add_rule")}</h2>
      <div id="diag-form">${ruleForm()}</div>
      <div class="row"><button data-dact="add">${dt("add_rule")}</button><button data-dact="fire-adhoc">${dt("fire_once")}</button></div>
      <h2>${dt("push_title")}</h2>
      <label>${dt("webhooks")}<textarea data-dcfg="webhooks" rows="3">${esc((c.webhooks || []).join("\n"))}</textarea></label>
      <label class="chk"><input type="checkbox" data-dcfg="notify" ${c.notify ? "checked" : ""}/> ${dt("notify")}</label>
      <div class="row"><button data-dact="save-cfg">${dt("save")}</button></div>
      <h2>${dt("log")}</h2>
      <pre id="diag-log" style="max-height:260px;overflow:auto"></pre></div>`;
  };

  pages.fleet = function () {
    const devs = (fleet && fleet.devices) || [];
    const rows = devs.map((d) => `<tr><td>${d.instance}</td><td>${esc(d.name)}</td><td>${esc(d.serial)}</td><td>${d.onvif_port}</td>
      <td>${d.rtsp_port}</td><td>${d.channels}</td><td>${d.ptz_channels}</td><td><a href="${esc(d.url)}" target="_blank">${dt("open")}</a></td></tr>`).join("");
    const f = (fleet && fleet.fleet) || {count: 1, onvif_ports: "", rtsp_ports: ""};
    return `<div class="card"><h1>${dt("fleet_title")}</h1>
      <p class="hint">${dt("root_file")}: <code>${esc((fleet && fleet.root_file) || "")}</code></p>
      <table><tr><th>#</th><th>${dt("name")}</th><th>SN</th><th>ONVIF</th><th>RTSP</th><th>ch</th><th>PTZ</th><th></th></tr>${rows}</table>
      <h2>${dt("def")}</h2>
      <div class="grid">
        <label>${dt("count")}<input type="number" min="1" max="256" data-fleet="count" value="${f.count}"/></label>
        <label>${dt("onvif_ports")}<input data-fleet="onvif_ports" value="${esc(f.onvif_ports)}" placeholder="8080-8089"/></label>
        <label>${dt("rtsp_ports")}<input data-fleet="rtsp_ports" value="${esc(f.rtsp_ports)}" placeholder="554-563"/></label>
      </div>
      <p class="hint" id="fleet-msg"></p>
      <div class="row"><button data-fact="save">${dt("save")}</button>
        <button data-fact="export">${dt("export_all")}</button><button data-fact="import">${dt("import_all")}</button></div></div>`;
  };

  // channel constructor card appended to the Channels page
  const baseChannels = pages.channels;
  pages.channels = function () {
    const rows = (cfg.channels || []).map((ch, i) => `<tr><td>${ch.id}</td><td>${esc(ch.name)}</td>
      <td><input type="checkbox" data-ch="${i}" data-key="ptz_enabled" ${ch.ptz_enabled !== false ? "checked" : ""}/></td>
      <td><input type="number" min="1" max="65535" style="width:90px" data-ch="${i}" data-key="rtsp_port" value="${ch.rtsp_port || ""}" placeholder="${cfg.network.rtsp_port}"/></td>
      <td><select data-ch="${i}" data-key="streams">${opt([1, 2, 3], (ch.third && ch.third.enabled) ? 3 : (ch.sub && ch.sub.enabled) ? 2 : 1)}</select></td></tr>`).join("");
    return baseChannels() + `<div class="card"><h2>${dt("constructor")}</h2><p class="hint">${dt("hint_streams")}</p>
      <table><tr><th>#</th><th>${dt("name")}</th><th>${dt("ptz_on")}</th><th>${dt("rtsp_port")}</th><th>${dt("streams")}</th></tr>${rows}</table>
      <div class="row"><button data-cact="apply">${dt("apply")}</button></div></div>`;
  };

  function say(msg) { if (typeof log === "function") log(msg); }

  async function refreshLive() {
    if (section !== "diag") return;
    try {
      const [d, l] = await Promise.all([api("/api/diag").then((r) => r.json()), api("/api/diag/log?limit=60").then((r) => r.json())]);
      diag = d;
      const io = $("#diag-io"); if (io) io.innerHTML = `<p>${ledRow("relay", d.status.relays)}</p><p>${ledRow("input", d.status.inputs)}</p>`;
      const rt = $("#diag-rules"); if (rt) rt.innerHTML = rulesTable();
      const lim = $("#diag-limits"); if (lim) lim.textContent = limitsLine();
      const lg = $("#diag-log");
      if (lg) lg.textContent = (l.events || []).map((e) => `${e.ts.slice(11, 19)}  ${e.topic.replace("tns1:", "")}  ${JSON.stringify(e.source)} ${JSON.stringify(e.data)}${e.rule ? "  [" + e.rule + "]" : ""}`).join("\n");
    } catch (e) { /* ignore transient errors */ }
  }

  document.addEventListener("input", (e) => {
    const el = e.target;
    if (el.dataset && el.dataset.f && el.closest("#diag-form")) {
      form[el.dataset.f] = el.value;
      if (["kind", "schedule"].includes(el.dataset.f)) { $("#diag-form").innerHTML = ruleForm(); }
    }
  });
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset && el.dataset.f && el.closest("#diag-form") && el.tagName === "SELECT") {
      form[el.dataset.f] = el.value;
      if (el.dataset.f === "kind" && el.value === "analytics") form.topic = anKind(form.topic);
      $("#diag-form").innerHTML = ruleForm();
    }
    if (el.dataset && el.dataset.dact === "toggle") {
      api(`/api/diag/rules/${el.dataset.id}`, json("PUT", {enabled: el.checked})).then(loadDiag);
    }
  });

  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-dact],[data-fact],[data-cact]");
    if (!b) return;
    const a = b.dataset.dact;
    if (a === "relay" || a === "input") {
      await api(`/api/diag/${a}/${b.dataset.tok}`, json("POST", {active: b.dataset.on !== "1"}));
      refreshLive();
    } else if (a === "fire") {
      await api("/api/diag/fire", json("POST", {rule_id: b.dataset.id}));
    } else if (a === "del") {
      await api(`/api/diag/rules/${b.dataset.id}`, {method: "DELETE"});
      await loadDiag(); render();
    } else if (a === "add") {
      const r = await api("/api/diag/rules", json("POST", formRule()));
      const j = await r.json();
      say(j.ok ? dt("saved") : (j.error || dt("error")));
      await loadDiag(); render();
    } else if (a === "fire-adhoc") {
      const r = await api("/api/diag/fire", json("POST", {rule: formRule()}));
      const j = await r.json();
      say(j.ok ? dt("fire_once") : (j.error || dt("error")));
    } else if (a === "save-cfg") {
      const c = JSON.parse(JSON.stringify(diag.config));
      document.querySelectorAll("[data-dcfg]").forEach((el) => {
        const k = el.dataset.dcfg;
        if (k === "webhooks") c.webhooks = el.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        else if (k === "notify") c.notify = el.checked;
        else c[k] = Number(el.value);
      });
      const r = await api("/api/diag", json("PUT", c));
      const j = await r.json();
      say(j.ok ? dt("saved") : (j.error || dt("error")));
      await loadDiag(); render();
    }
    const fa = b.dataset.fact;
    if (fa === "save") {
      const body = {};
      document.querySelectorAll("[data-fleet]").forEach((el) => { body[el.dataset.fleet] = el.dataset.fleet === "count" ? Number(el.value) : el.value; });
      const j = await (await api("/api/fleet", json("PUT", body))).json();
      const m = $("#fleet-msg"); if (m) m.textContent = j.ok ? dt("restart") : (j.error || dt("error"));
    } else if (fa === "export") { actions.export(); }
    else if (fa === "import") { actions.import(); }
    if (b.dataset.cact === "apply") {
      document.querySelectorAll("[data-ch]").forEach((el) => {
        const i = Number(el.dataset.ch), k = el.dataset.key, ch = cfg.channels[i];
        if (k === "ptz_enabled") ch.ptz_enabled = el.checked;
        else if (k === "rtsp_port") ch.rtsp_port = el.value ? Number(el.value) : null;
        else if (k === "streams") ch.streams = Number(el.value);
      });
      await actions.save();
      cfg = await (await api("/api/config")).json();
      render();
    }
  });

  const origRender = render;
  window.render = async function () {
    if (section === "diag") { await loadDiag(); }
    if (section === "fleet") { await loadFleet(); }
    origRender();
  };
  if (timer) clearInterval(timer);
  timer = setInterval(refreshLive, 1500);
})();
