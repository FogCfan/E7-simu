"use strict";
/* E7 Twin — dashboard (agent 06). JS vanilla + uPlot vendorisé.
   Aucune constante de carte : tout vient de GET /api/board. */
(() => {
  const WINDOW_S = 60, THRESH_WARN = 70, THRESH_HOT = 85;
  const MAX_UART = 500, MAX_EVENTS = 300, SVG = document.getElementById("map");
  const $ = (id) => document.getElementById(id);

  const S = {
    board: null, status: { state: "stopped", cores: {}, images: {}, fake: true },
    sample: null, hist: {},            // hist[core] = {t:[], load:[], mips:[]}
    uart: {},                          // uart[core] = [lines]
    events: [], seen: new Set(),
    view: "map", core: null, sel: null, charts: {}, lastT: -1, profTimer: null,
  };

  // ---------- utilitaires ----------
  const el = (tag, attrs = {}, text) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const sv = (tag, attrs = {}, text) => {
    const e = document.createElementNS(SVG.namespaceURI, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const hex = (n) => "0x" + n.toString(16).toUpperCase().padStart(8, "0");
  const fmtB = (n) => n == null ? "—" : n >= 1048576 ? (n / 1048576).toFixed(2) + " Mio" : n >= 1024 ? (n / 1024).toFixed(1) + " kio" : n + " o";
  const lvl = (pct) => pct >= THRESH_HOT ? "hot" : pct >= THRESH_WARN ? "warn" : "ok";
  const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); };
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const showError = (msg) => { const b = $("error-banner"); b.textContent = msg; b.hidden = !msg; };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch (_) { /* corps vide */ }
    if (!r.ok) {
      const d = body && body.detail;
      throw new Error(typeof d === "string" ? d : (r.status + " " + r.statusText));
    }
    return body;
  }

  // ---------- thème ----------
  function applyTheme(t) {
    if (t) document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    destroyCharts();
    if (S.view === "cores") renderCoresView();
  }
  function isDark() {
    const t = document.documentElement.getAttribute("data-theme");
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  $("btn-theme").addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    try { localStorage.setItem("e7twin-theme", next); } catch (_) { /* stockage indisponible */ }
    applyTheme(next);
  });
  try { const t = localStorage.getItem("e7twin-theme"); if (t) document.documentElement.setAttribute("data-theme", t); } catch (_) { /* ignoré */ }

  // ---------- navigation ----------
  function showView(v) {
    S.view = v;
    document.querySelectorAll("nav.tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    document.querySelectorAll("main > section").forEach((s) => { s.hidden = s.id !== "view-" + v; });
    if (v === "cores") renderCoresView();
    if (v === "memory") renderMemory();
    if (v === "compat") loadKnownImages();
    if (v === "npu") loadNpuList();
    if (v !== "cores") stopProfile();
  }
  document.querySelectorAll("nav.tabs [role=tab]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  // ---------- contrôle ----------
  async function simAction(a) {
    try { applyStatus(await api("/api/sim/" + a, { method: "POST" })); showError(""); }
    catch (e) { showError("Action « " + a + " » impossible : " + e.message); }
  }
  $("btn-start").addEventListener("click", () => simAction("start"));
  $("btn-pause").addEventListener("click", () => simAction("pause"));
  $("btn-reset").addEventListener("click", () => simAction("reset"));

  const STATE_LABEL = { stopped: "arrêtée", running: "en marche", paused: "en pause", error: "erreur" };
  function applyStatus(st) {
    S.status = Object.assign({}, S.status, st);
    const s = S.status.state;
    const b = $("sim-state");
    b.textContent = STATE_LABEL[s] || s;
    b.className = "badge state-" + s;
    $("sim-fake").hidden = !S.status.fake;
    $("btn-start").disabled = s === "running";
    $("btn-pause").disabled = s !== "running";
    if (s === "error" && st.message) showError("Erreur de simulation : " + st.message);
    if (s === "stopped" && st.t_virtual_s === 0) resetHistory();
    renderMap();
  }

  // ---------- WebSocket ----------
  let ws = null, retry = 0;
  function setWs(kind, text) {
    $("ws-ind").className = "ws ws-" + kind;
    $("ws-text").textContent = text;
  }
  function connect() {
    setWs("conn", "connexion…");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onopen = () => { retry = 0; setWs("up", "connecté"); };
    ws.onmessage = (ev) => { try { onMessage(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
    ws.onclose = () => {
      const delay = Math.min(10000, 500 * 2 ** retry++);
      setWs("down", "déconnecté — nouvelle tentative dans " + Math.ceil(delay / 1000) + " s");
      setTimeout(connect, delay);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) { /* déjà fermé */ } };
  }

  function onMessage(m) {
    switch (m.type) {
      case "sample": onSample(m.data); break;
      case "uart": onUart(m); break;
      case "event": addEvent(m.data); break;
      case "status": applyStatus(m); break;
    }
  }

  // ---------- historiques ----------
  function resetHistory() {
    S.hist = {}; S.uart = {}; S.events = []; S.seen = new Set(); S.lastT = -1;
    renderUart(); renderTimeline();
  }
  function onSample(d) {
    if (d.t_virtual_s < S.lastT) resetHistory();   // reset côté serveur
    S.sample = d;
    $("sim-time").textContent = "t = " + d.t_virtual_s.toFixed(1) + " s";
    if (d.t_virtual_s > S.lastT) {                 // le temps n'avance pas en pause
      S.lastT = d.t_virtual_s;
      for (const [id, c] of Object.entries(d.cores)) {
        const h = S.hist[id] || (S.hist[id] = { t: [], load: [], mips: [] });
        h.t.push(d.t_virtual_s); h.load.push(c.load_pct); h.mips.push(c.mips);
        while (h.t.length > 2 && h.t[0] < d.t_virtual_s - WINDOW_S - 1) { h.t.shift(); h.load.shift(); h.mips.shift(); }
      }
    }
    (d.events || []).forEach(addEvent);
    renderMap();
    if (S.view === "cores") updateCharts();
    if (S.view === "memory") renderMemory();
  }
  function onUart(m) {
    const a = S.uart[m.core] || (S.uart[m.core] = []);
    a.push({ t: m.t, text: m.text });
    if (a.length > MAX_UART) a.shift();
    if (S.view === "cores" && S.core === m.core) renderUart();
  }
  function addEvent(e) {
    const k = [e.t, e.kind, e.core, e.detail].join("|");
    if (S.seen.has(k)) return;
    S.seen.add(k);
    if (e.kind === "uart") return;
    S.events.unshift(e);
    if (S.events.length > MAX_EVENTS) { const r = S.events.pop(); S.seen.delete([r.t, r.kind, r.core, r.detail].join("|")); }
    if (S.view === "timeline") renderTimeline();
  }

  // ---------- carte (SVG généré depuis /api/board) ----------
  const mapNodes = {};   // id -> {g, fill, label, kind, w}
  function memPct(name) {
    const m = S.sample && S.sample.memory[name];
    if (!m || !m.size) return null;
    return 100 * (m.watermark_bytes != null ? m.watermark_bytes : m.static_bytes) / m.size;
  }
  function node(parent, id, kind, x, y, w, h, title, sub, onClick, disabled) {
    const g = sv("g", { class: "node" + (disabled ? " disabled" : ""), transform: `translate(${x},${y})`,
      role: "button", tabindex: disabled ? "-1" : "0", "aria-label": title });
    g.appendChild(sv("rect", { class: "box", width: w, height: h, rx: 6 }));
    const fill = sv("rect", { class: "fill", x: 0, y: h - 7, width: 0, height: 7, rx: 3 });
    g.appendChild(fill);
    g.appendChild(sv("text", { x: 8, y: 17 }, title));
    const label = sv("text", { class: "sub", x: 8, y: 32 }, sub);
    g.appendChild(label);
    const act = () => onClick();
    g.addEventListener("click", act);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); } });
    parent.appendChild(g);
    mapNodes[id] = { g, fill, label, kind, w, h, disabled, base: sub };
    return g;
  }
  function buildMap() {
    const b = S.board;
    clear(SVG);
    for (const k of Object.keys(mapNodes)) delete mapNodes[k];
    const nCores = b.cores.length, gap = 16, pad = 10;
    const cw = Math.floor((960 - 2 * pad - gap * (nCores - 1)) / nCores);
    let y = 8;
    SVG.appendChild(sv("text", { class: "grp", x: pad, y: y + 10 }, "Cœurs, NPU et TCM locales"));
    y += 18;
    const rowCore = y, rowNpu = y + 76, rowTcm = y + 140;
    let maxY = rowTcm;
    b.cores.forEach((c, i) => {
      const x = pad + i * (cw + gap);
      const sub = `${c.type} · ${Math.round(c.freq_hz / 1e6)} MHz` + (c.mve ? " · MVE" : "");
      node(SVG, "core:" + c.id, "core", x, rowCore, cw, 56, c.id.toUpperCase().replace("_", "-"), sub,
        () => select("core", c.id), !c.executable);
      if (c.npu) {
        SVG.insertBefore(sv("line", { class: "link", x1: x + cw / 2, y1: rowCore + 56, x2: x + cw / 2, y2: rowNpu }), SVG.firstChild);
        node(SVG, "npu:" + c.id, "npu", x + 10, rowNpu, cw - 20, 44, c.npu, "NPU de " + c.id, () => select("npu", c.id), false);
      }
      const tcms = b.memories.filter((m) => m.local && m.visible_to.length === 1 && m.visible_to[0] === c.id);
      tcms.forEach((m, j) => {
        const yy = rowTcm + j * 50;
        node(SVG, "mem:" + m.name, "mem", x + 10, yy, cw - 20, 42, m.name, fmtB(m.size), () => select("mem", m.name), false);
        maxY = Math.max(maxY, yy + 42);
      });
    });
    y = maxY + 20;
    SVG.appendChild(sv("text", { class: "grp", x: pad, y }, "Mémoires partagées (vues de tous les cœurs)"));
    y += 8;
    const shared = b.memories.filter((m) => !m.local && !m.alias_of)
      .sort((a, c) => (a.kind === "sram" ? 0 : 1) - (c.kind === "sram" ? 0 : 1));
    const perRow = 4, mw = Math.floor((960 - 2 * pad - gap * (perRow - 1)) / perRow);
    shared.forEach((m, i) => {
      const x = pad + (i % perRow) * (mw + gap), yy = y + Math.floor(i / perRow) * 52;
      node(SVG, "mem:" + m.name, "mem", x, yy, mw, 44, m.name + " (" + m.kind.toUpperCase() + ")", fmtB(m.size),
        () => select("mem", m.name), false);
      maxY = yy + 44;
    });
    SVG.setAttribute("viewBox", `0 0 960 ${maxY + 14}`);
  }
  function renderMap() {
    if (!S.board) return;
    const sc = S.status.cores || {};
    for (const [id, n] of Object.entries(mapNodes)) {
      const [kind, key] = id.split(":");
      let pct = null, text = n.base;
      if (kind === "core") {
        const c = S.sample && S.sample.cores[key];
        if (n.disabled) text = (sc[key] || "non exécuté") + " — " + n.base;
        else if (c) { pct = c.load_pct; text = `charge ${c.load_pct.toFixed(1)} % · ${c.mips.toFixed(0)} MIPS`; }
      } else if (kind === "mem") {
        pct = memPct(key);
        if (pct != null) text = `${pct.toFixed(1)} % de ${fmtB(S.sample.memory[key].size)}`;
      }
      n.label.textContent = text;
      n.fill.setAttribute("width", pct == null ? 0 : Math.max(0, Math.min(100, pct)) / 100 * n.w);
      n.g.setAttribute("class", "node" + (n.disabled ? " disabled" : "") + (pct == null ? "" : " lvl-" + lvl(pct)) +
        (S.sel === id ? " sel" : ""));
    }
  }
  function select(kind, key) {
    S.sel = kind + ":" + key;
    renderMap(); renderDetail();
  }
  function renderDetail() {
    const d = $("detail"); clear(d);
    d.appendChild(el("h2", {}, "Détail"));
    if (!S.sel) { d.appendChild(el("p", { class: "muted" }, "Cliquer sur un cœur ou une mémoire.")); return; }
    const [kind, key] = S.sel.split(":");
    const rows = [];
    let goto = null;
    if (kind === "core" || kind === "npu") {
      const c = S.board.cores.find((x) => x.id === key);
      rows.push([kind === "npu" ? "NPU" : "Cœur", kind === "npu" ? c.npu : c.id], ["Type", c.type],
        ["Fréquence", c.freq_hz / 1e6 + " MHz"], ["FPU", c.fpu || "—"], ["MVE", c.mve ? "oui" : "non"],
        ["Exécutable", c.executable ? "oui" : "non (A32 non exécuté en v1)"]);
      if (c.npu) rows.push(["NPU associé", c.npu]);
      const s = S.sample && S.sample.cores[key];
      if (s) rows.push(["Charge", s.load_pct.toFixed(1) + " % (CPI = 1)"], ["MIPS", s.mips.toFixed(1)], ["WFI", s.wfi_pct.toFixed(1) + " %"]);
      const img = (S.status.images || {})[key];
      if (img) rows.push(["Image", img]);
      const slot = S.board.atoc_slots.filter((a) => a.core === key);
      slot.forEach((a) => rows.push(["Slot ATOC " + a.kind, `${a.name} @ ${hex(a.base)} (${fmtB(a.size)})`]));
      if (c.executable && kind === "core") goto = key;
    } else {
      const m = S.board.memories.find((x) => x.name === key);
      rows.push(["Région", m.name], ["Type", m.kind], ["Base", hex(m.base)], ["Fin (exclue)", hex(m.end)], ["Taille", fmtB(m.size) + " (" + m.size + " o)"],
        ["Visible par", m.visible_to.join(", ")], ["Locale", m.local ? "oui (contenu propre à chaque cœur)" : "non"]);
      if (m.alias_of) rows.push(["Alias de", m.alias_of]);
      const s = S.sample && S.sample.memory[key];
      if (s) rows.push(["Statique", fmtB(s.static_bytes)], ["Watermark", fmtB(s.watermark_bytes)]);
    }
    const dl = el("dl");
    rows.forEach(([k, v]) => { dl.appendChild(el("dt", {}, k)); dl.appendChild(el("dd", {}, String(v))); });
    d.appendChild(dl);
    if (goto) {
      const b = el("button", { type: "button" }, "Ouvrir l'onglet du cœur");
      b.style.marginTop = "10px";
      b.addEventListener("click", () => { S.core = goto; showView("cores"); });
      d.appendChild(b);
    }
  }

  // ---------- cœurs ----------
  function execCores() { return S.board ? S.board.cores.filter((c) => c.executable) : []; }
  function renderCoresView() {
    if (!S.board) return;
    const cores = execCores();
    if (!S.core || !cores.some((c) => c.id === S.core)) S.core = cores.length ? cores[0].id : null;
    const tabs = $("core-tabs"); clear(tabs);
    cores.forEach((c) => {
      const b = el("button", { type: "button", role: "tab", "aria-selected": String(c.id === S.core) }, c.id);
      b.addEventListener("click", () => { S.core = c.id; destroyCharts(); renderCoresView(); });
      tabs.appendChild(b);
    });
    renderUart();
    ensureCharts();
    updateCharts();
    loadProfile();
    stopProfile();
    S.profTimer = setInterval(loadProfile, 5000);
  }
  function stopProfile() { if (S.profTimer) { clearInterval(S.profTimer); S.profTimer = null; } }
  function destroyCharts() { for (const k of Object.keys(S.charts)) { S.charts[k].destroy(); delete S.charts[k]; } }
  function ensureCharts() {
    if (S.charts.load || !S.core) return;
    const mk = (host, label, color, max) => {
      const box = $(host);
      clear(box);
      const w = Math.max(280, box.clientWidth || 400);
      const grid = { stroke: cssVar("--grid"), width: 1 }, axis = cssVar("--muted");
      return new uPlot({
        width: w, height: 190,
        scales: { x: { time: false, range: (u, mn, mx) => [Math.max(0, mx - WINDOW_S), Math.max(WINDOW_S, mx)] },
                  y: { range: max ? [0, max] : (u, mn, mx) => [0, Math.max(1, mx * 1.15)] } },
        axes: [{ stroke: axis, grid, ticks: grid, label: "temps virtuel (s)" }, { stroke: axis, grid, ticks: grid }],
        series: [{}, { label, stroke: color, width: 2, fill: color + "22" }],
        legend: { show: true },
      }, [[], []], box);
    };
    S.charts.load = mk("chart-load", "charge %", cssVar("--s1"), 100);
    S.charts.mips = mk("chart-mips", "MIPS", cssVar("--s2"), null);
  }
  function updateCharts() {
    if (!S.core || !S.charts.load) return;
    const h = S.hist[S.core] || { t: [], load: [], mips: [] };
    S.charts.load.setData([h.t, h.load]);
    S.charts.mips.setData([h.t, h.mips]);
  }
  window.addEventListener("resize", () => {
    if (S.view !== "cores") return;
    for (const [k, c] of Object.entries(S.charts)) {
      const box = $(k === "load" ? "chart-load" : "chart-mips");
      c.setSize({ width: Math.max(280, box.clientWidth), height: 190 });
    }
  });

  function renderUart() {
    const pre = $("uart"); if (!pre || !S.core) return;
    const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
    clear(pre);
    const lines = S.uart[S.core] || [];
    if (!lines.length) pre.textContent = "(aucune sortie UART)";
    lines.forEach((l) => {
      const t = el("span", { class: "t" }, "[" + Number(l.t).toFixed(3) + "] ");
      pre.appendChild(t); pre.appendChild(document.createTextNode(l.text + "\n"));
    });
    if (stick) pre.scrollTop = pre.scrollHeight;
  }
  async function loadProfile() {
    const core = S.core; if (!core) return;
    const tb = $("prof-table").tBodies[0];
    try {
      const p = await api("/api/profile/" + encodeURIComponent(core));
      if (core !== S.core) return;
      $("prof-fake").hidden = !p.fake;
      $("prof-note").textContent = p.approximation || "";
      clear(tb);
      (p.functions || []).forEach((f) => {
        const tr = el("tr");
        tr.appendChild(el("td", {}, f.name));
        tr.appendChild(el("td", { class: "num" }, f.pct.toFixed(1)));
        const td = el("td"); const bar = el("span", { class: "bar" }); const b = el("b");
        b.style.width = Math.min(100, f.pct) + "%"; bar.appendChild(b); td.appendChild(bar); tr.appendChild(td);
        tb.appendChild(tr);
      });
    } catch (e) { clear(tb); $("prof-note").textContent = "Profil indisponible : " + e.message; }
  }

  // ---------- mémoire ----------
  function renderMemory() {
    const list = $("mem-list");
    if (!S.board) return;
    if (!S.sample) { list.textContent = "En attente du premier échantillon…"; return; }
    clear(list);
    S.board.memories.filter((m) => !m.alias_of).forEach((m) => {
      const s = S.sample.memory[m.name]; if (!s) return;
      const size = s.size || m.size;
      const stPct = 100 * s.static_bytes / size;
      const wmPct = s.watermark_bytes != null ? 100 * s.watermark_bytes / size : null;
      const top = Math.max(stPct, wmPct || 0);
      const row = el("div", { class: "mem " + (top >= THRESH_HOT ? "hot" : top >= 70 ? "warn" : "") });
      const nm = el("div", { class: "nm" }, m.name);
      nm.appendChild(el("small", {}, `${m.kind}${m.local ? " · locale " + m.visible_to[0] : ""} · ${hex(m.base)}`));
      const track = el("div", { class: "track", role: "img",
        "aria-label": `${m.name} : statique ${stPct.toFixed(1)} %` + (wmPct != null ? `, watermark ${wmPct.toFixed(1)} %` : "") });
      const wm = el("div", { class: "wm" }); wm.style.width = Math.min(100, wmPct || 0) + "%";
      const st = el("div", { class: "st" }); st.style.width = Math.min(100, stPct) + "%";
      track.appendChild(wm); track.appendChild(st);
      const txt = el("div", {}, `statique ${fmtB(s.static_bytes)} (${stPct.toFixed(1)} %)` +
        (wmPct != null ? ` · watermark ${fmtB(s.watermark_bytes)} (${wmPct.toFixed(1)} %)` : " · watermark n/d") + ` / ${fmtB(size)} `);
      if (top >= THRESH_HOT) txt.appendChild(el("span", { class: "alert" }, "⚠ > 85 %"));
      row.appendChild(nm); row.appendChild(track); row.appendChild(txt);
      list.appendChild(row);
    });
  }

  // ---------- timeline ----------
  const KIND_LABEL = { mhu: "MHU", fault: "Fault", unmapped: "Accès non mappé" };
  document.querySelectorAll(".filters input").forEach((i) => i.addEventListener("change", renderTimeline));
  function renderTimeline() {
    const on = new Set([...document.querySelectorAll(".filters input:checked")].map((i) => i.dataset.kind));
    const tb = $("tl-table").tBodies[0]; clear(tb);
    const evs = S.events.filter((e) => on.has(e.kind)).slice(0, 200);
    $("tl-empty").hidden = evs.length > 0;
    evs.forEach((e) => {
      const tr = el("tr");
      tr.appendChild(el("td", { class: "num" }, Number(e.t).toFixed(4)));
      tr.appendChild(el("td", { class: "kind-" + e.kind }, KIND_LABEL[e.kind] || e.kind));
      tr.appendChild(el("td", {}, e.core || ""));
      tr.appendChild(el("td", {}, e.detail || ""));
      tb.appendChild(tr);
    });
  }

  // ---------- compatibilité ----------
  const badge = (v, cls = "") => el("span", { class: `badge v-${v} ${cls}` }, v);
  async function loadKnownImages() {
    const dl = $("check-known"); clear(dl);
    const names = new Set(Object.values(S.status.images || {}).map((p) => p.split(/[\\/]/).pop().replace(/\.elf$/, "")));
    try { await api("/api/check/_"); } catch (e) {
      const m = /connues : \[(.*)\]/.exec(e.message);
      if (m) (m[1].match(/'([^']+)'/g) || []).forEach((q) => names.add(q.slice(1, -1)));
    }
    names.forEach((n) => dl.appendChild(el("option", { value: n })));
  }
  $("check-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("check-image").value.trim(), out = $("check-report"), msg = $("check-msg");
    clear(out); msg.textContent = "Analyse de « " + name + " »…";
    try {
      renderReport(await api("/api/check/" + encodeURIComponent(name)), out);
      msg.textContent = "";
    } catch (e) { msg.textContent = "Échec : " + e.message; }
  });
  function renderReport(r, out) {
    const head = el("p");
    head.appendChild(document.createTextNode(`${r.image} · ${r.core} · verdict global `));
    head.appendChild(badge(r.verdict, "big"));
    out.appendChild(head);
    const t = el("table", { class: "tbl" });
    const hd = el("thead"); const hr = el("tr");
    ["Règle", "Verdict", "Mesuré", "Limite", "Explication"].forEach((h) => hr.appendChild(el("th", {}, h)));
    hd.appendChild(hr); t.appendChild(hd);
    const tb = el("tbody");
    r.rules.forEach((x) => {
      const tr = el("tr");
      tr.appendChild(el("td", {}, x.title || x.id));
      const td = el("td"); td.appendChild(badge(x.verdict)); tr.appendChild(td);
      tr.appendChild(el("td", {}, x.measured == null ? "—" : String(x.measured)));
      tr.appendChild(el("td", {}, x.limit == null ? "—" : String(x.limit)));
      tr.appendChild(el("td", {}, x.message));
      tb.appendChild(tr);
    });
    t.appendChild(tb); out.appendChild(t);
    out.appendChild(el("h2", {}, "Occupation par région"));
    const list = el("div", { class: "memlist" });
    Object.entries(r.regions_usage || {}).forEach(([name, u]) => {
      const pct = u.pct != null ? u.pct : 100 * u.used_bytes / u.size_bytes;
      const row = el("div", { class: "mem " + (pct > 100 ? "hot" : pct > 85 ? "hot" : pct >= 70 ? "warn" : "") });
      row.appendChild(el("div", { class: "nm" }, name));
      const track = el("div", { class: "track", role: "img", "aria-label": `${name} : ${pct.toFixed(1)} %` });
      const st = el("div", { class: "st" }); st.style.width = Math.min(100, pct) + "%"; track.appendChild(st);
      row.appendChild(track);
      row.appendChild(el("div", {}, `${fmtB(u.used_bytes)} / ${fmtB(u.size_bytes)} (${pct.toFixed(1)} %)` +
        (u.reserved_bytes ? ` · dont réserve pile/tas ${fmtB(u.reserved_bytes)}` : "")));
      list.appendChild(row);
    });
    out.appendChild(list);
  }

  // ---------- NPU ----------
  async function loadNpuList() {
    const sel = $("npu-select"), out = $("npu-report"), msg = $("npu-msg");
    let list = [];
    try { list = await api("/api/npu"); } catch (e) { msg.textContent = "Échec : " + e.message; return; }
    clear(sel); clear(out);
    if (!list.length) {
      msg.textContent = "Aucun rapport : lancez `e7twin npu <modele.tflite>`.";
      sel.disabled = true; return;
    }
    msg.textContent = ""; sel.disabled = false;
    list.forEach((r) => sel.appendChild(el("option", { value: r.model }, r.model)));  // déjà trié du plus récent au plus ancien
    loadNpuReport(list[0].model);
  }
  $("npu-select").addEventListener("change", (e) => loadNpuReport(e.target.value));
  async function loadNpuReport(stem) {
    const out = $("npu-report"), msg = $("npu-msg");
    clear(out); msg.textContent = "Chargement…";
    try { renderNpu(await api("/api/npu/" + encodeURIComponent(stem)), out); msg.textContent = ""; }
    catch (e) { msg.textContent = "Échec : " + e.message; }
  }
  function renderNpu(r, out) {
    out.appendChild(el("p", {}, `${r.model} · ${fmtB(r.model_bytes)} · Vela ${r.vela_version || "?"} · poids en ${r.weights_region}`));
    Object.entries(r.results || {}).forEach(([id, x]) => {
      out.appendChild(el("h2", {}, `${x.accelerator || id} → ${x.core}`));
      if (!x.ok) { out.appendChild(el("p", { class: "muted" }, "Estimation échouée : " + (x.error || "inconnue"))); return; }
      const t = el("table", { class: "tbl" });
      const rows = [
        ["Latence estimée", (x.inference_time_s * 1000).toFixed(2) + " ms"],
        ["Débit estimé", x.inferences_per_s.toFixed(1) + " inf/s"],
        ["Cycles (avec attentes mémoire)", String(x.cycles_total)],
        ["Opérateurs sur NPU", String(x.npu_operators)],
        ["Opérateurs en repli CPU", String(x.cpu_operators) +
          (Object.keys(x.cpu_op_types || {}).length ? " (" + Object.entries(x.cpu_op_types).map(([k, n]) => k + " ×" + n).join(", ") + ")" : "")],
        ["Poids", fmtB(x.weights_bytes)],
      ];
      const tb = el("tbody");
      rows.forEach(([k, v]) => { const tr = el("tr"); tr.appendChild(el("th", { scope: "row" }, k)); tr.appendChild(el("td", {}, v)); tb.appendChild(tr); });
      t.appendChild(tb); out.appendChild(t);
      const bud = x.sram_budget_bytes, over = x.sram_bytes > bud || !x.fits;
      const pct = bud ? 100 * x.sram_bytes / bud : 100;
      const row = el("div", { class: "mem " + (over ? "hot" : pct >= 70 ? "warn" : "") });
      row.appendChild(el("div", { class: "nm" }, "Arena SRAM"));
      const track = el("div", { class: "track", role: "img", "aria-label": `Arena ${pct.toFixed(1)} % du budget` });
      const st = el("div", { class: "st" }); st.style.width = Math.min(100, pct) + "%"; track.appendChild(st);
      row.appendChild(track);
      row.appendChild(el("div", {}, `${fmtB(x.sram_bytes)} / ${fmtB(bud)} (${pct.toFixed(1)} %)` + (over ? " · budget dépassé" : "")));
      const ml = el("div", { class: "memlist" }); ml.appendChild(row); out.appendChild(ml);
      (x.vela_warnings || []).forEach((w) => out.appendChild(el("p", { class: "muted" }, "⚠ " + w)));
    });
  }

  // ---------- démarrage ----------
  async function init() {
    connect();
    try {
      S.board = await api("/api/board");
      $("board-name").textContent = "— " + (S.board.info.name || "");
      buildMap(); renderMap();
      S.core = execCores().length ? execCores()[0].id : null;
      applyStatus(await api("/api/sim/status"));
    } catch (e) { showError("Impossible de charger la carte : " + e.message); }
    const v = location.hash.slice(1);   // ex. /#cores : lien direct vers une vue
    if (document.getElementById("view-" + v)) showView(v);
  }
  init();
})();
