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
    tab: "programs", pview: "examples", view: "map", core: null, sel: null, charts: {}, lastT: -1, profTimer: null,
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
    let r;
    try { r = await fetch(path, opts); } catch (_) { throw new Error(T("err_network")); }
    let body = null;
    try { body = await r.json(); } catch (_) { /* corps vide */ }
    if (!r.ok) {
      const d = body && body.detail;
      throw new Error(typeof d === "string" ? d : (r.status + " " + r.statusText));
    }
    return body;
  }


  // ---------- langue (FR par défaut, EN en option) ----------
  const I18N = {
    fr: {
      theme: "◐ Thème", theme_aria: "Basculer le thème clair/sombre", skip: "Aller au contenu",
      tab_programs: "Programmes", tab_build: "Compilation", sub_examples: "Exemples", sub_mine: "Mes programmes", mine_none: "Aucun programme : compilez un main.c dans Compilation ou importez un ELF dans Importer.", mine_compiled: "compilé", mine_imported: "importé", mine_edit: "Modifier", mine_delete: "Supprimer", mine_confirm: "Supprimer « {0} » ?", mine_added: "Ajouté à Mes programmes.", mine_view: "Voir dans Mes programmes", mine_loading: "Chargement…", tab_import: "Importer", tab_verify: "Vérification", tab_sim: "Simulation",
      sub_map: "Carte", sub_cores: "Cœurs", sub_memory: "Mémoire", sub_timeline: "Timeline", sub_npu: "NPU",
      ws_up: "connecté", ws_connecting: "connexion…", ws_down: "déconnecté — nouvelle tentative dans {0} s", 
      tab_tests: "Tests", tst_help: "Lancez le test ici sur le simulateur, puis le même programme sur la carte. La ligne SIG doit être identique.", tst_none: "Aucun test (exemples nommés parity_*).", tst_run: "Lancer sur le simulateur", tst_running: "Exécution… (jusqu'à 2 min)", tst_ok: "OK", tst_fail: "ÉCHEC", tst_score: "Score", tst_duration: "Durée", tst_copy: "Copier", tst_copied: "Copié", tst_uart: "Sortie UART", tst_board: "Sur la carte", tst_no_cmd: "Aucune commande disponible pour ce test.", tst_flash_note: "Le flash de la carte est fait par l'humain, jamais par l'outil.", tst_board_sig: "SIG relevée sur la carte", tst_same: "Identique", tst_diff: "Différent",
      start: "Démarrer", pause: "Pause", reset: "Réinitialiser",
      state_stopped: "arrêtée", state_running: "en marche", state_paused: "en pause", state_error: "erreur",
      ex_title: "Exemples", ex_intro: "Choisissez un exemple, vérifiez-le ou simulez-le.",
      ex_build: "Compiler", ex_building: "Compilation… (peut prendre une minute)", ex_build_all: "Compiler les exemples manquants ({0})", ex_progress: "{0}/{1} : {2}", ex_build_fail: "La compilation a échoué.", ex_build_err: "Erreur : {0}", ex_log: "Journal de compilation",
      ex_loading: "Chargement des exemples…", ex_none: "Aucun exemple disponible.",
      ex_built: "compilé", ex_notbuilt: "non compilé", ex_cores: "Cœurs", ex_verify: "Vérifier", ex_simulate: "Simuler",
      imp_elf_title: "Importer un ELF", imp_drop: "Déposez un fichier .elf ici ou cliquez pour choisir",
      imp_src_title: "Compiler un main.c", imp_core: "Cœur", imp_name: "Nom", imp_compile: "Compiler",
      imp_log: "Log de compilation", imp_uploading: "Envoi de « {0} »…", imp_compiling: "Compilation en cours…",
      imp_ok: "ELF prêt : {0}", imp_failed: "La compilation a échoué.", imp_err_line: "ligne {0}",
      imp_need_elf: "Choisissez un fichier .elf.", imp_empty_src: "Le code source est vide.",
      ver_title: "Vérification de compatibilité", ver_none: "Aucune vérification : utilisez « Vérifier » sur un exemple ou un fichier importé.",
      ver_running: "Vérification de « {0} »…", ver_verdict: "Verdict global", ver_report: "Ouvrir le rapport HTML",
      ver_rule: "Règle", ver_v: "Verdict", ver_measured: "Mesuré", ver_limit: "Limite", ver_expl: "Explication",
      ver_regions: "Occupation par région",
      sim_loading: "Chargement de « {0} » sur {1}…", sim_started: "Simulation démarrée.",
      err_action: "Action impossible", err_http: "Erreur", err_network: "Serveur injoignable.",
      sub_display: "Écran", disp_note: "Émulation : image lue dans le framebuffer, sans timing DSI",
      disp_off: "Écran inactif — le firmware n'a pas encore programmé le contrôleur CDC200",
      disp_state: "{0}×{1} · format {2} · framebuffer {3} · {4} images/s",
      accel: "Accéléré", accel_tip: "Saute les périodes de veille : la simulation va bien plus vite que le temps réel, les animations défilent en accéléré",
      quit: "Quitter", quit_confirm: "Arrêter E7 Twin (simulation et serveur) ?",
      quit_done: "E7 Twin est arrêté. Vous pouvez fermer cet onglet.",
      npu_none: "Aucun rapport : lancez `e7twin npu <modele.tflite>`.",
    },
    en: {
      theme: "◐ Theme", theme_aria: "Toggle light/dark theme", skip: "Skip to content",
      tab_programs: "Programs", tab_build: "Build", sub_examples: "Examples", sub_mine: "My programs", mine_none: "No program yet: compile a main.c in Build or import an ELF in Import.", mine_compiled: "compiled", mine_imported: "imported", mine_edit: "Edit", mine_delete: "Delete", mine_confirm: "Delete “{0}”?", mine_added: "Added to My programs.", mine_view: "View in My programs", mine_loading: "Loading…", tab_import: "Import", tab_verify: "Check", tab_sim: "Simulation",
      sub_map: "Map", sub_cores: "Cores", sub_memory: "Memory", sub_timeline: "Timeline", sub_npu: "NPU",
      ws_up: "connected", ws_connecting: "connecting…", ws_down: "disconnected — retrying in {0} s", 
      tab_tests: "Tests", tst_help: "Run the test here on the simulator, then the same program on the board. The SIG line must be identical.", tst_none: "No test (examples named parity_*).", tst_run: "Run on the simulator", tst_running: "Running… (up to 2 min)", tst_ok: "OK", tst_fail: "FAIL", tst_score: "Score", tst_duration: "Duration", tst_copy: "Copy", tst_copied: "Copied", tst_uart: "UART output", tst_board: "On the board", tst_no_cmd: "No command available for this test.", tst_flash_note: "Flashing the board is done by a human, never by the tool.", tst_board_sig: "SIG read on the board", tst_same: "Identical", tst_diff: "Different",
      start: "Start", pause: "Pause", reset: "Reset",
      state_stopped: "stopped", state_running: "running", state_paused: "paused", state_error: "error",
      ex_title: "Examples", ex_intro: "Pick an example, check it or simulate it.",
      ex_build: "Build", ex_building: "Building… (may take a minute)", ex_build_all: "Build missing examples ({0})", ex_progress: "{0}/{1}: {2}", ex_build_fail: "The build failed.", ex_build_err: "Error: {0}", ex_log: "Build log",
      ex_loading: "Loading examples…", ex_none: "No example available.",
      ex_built: "built", ex_notbuilt: "not built", ex_cores: "Cores", ex_verify: "Check", ex_simulate: "Simulate",
      imp_elf_title: "Import an ELF", imp_drop: "Drop an .elf file here or click to choose",
      imp_src_title: "Build a main.c", imp_core: "Core", imp_name: "Name", imp_compile: "Build",
      imp_log: "Build log", imp_uploading: "Uploading “{0}”…", imp_compiling: "Building…",
      imp_ok: "ELF ready: {0}", imp_failed: "The build failed.", imp_err_line: "line {0}",
      imp_need_elf: "Choose an .elf file.", imp_empty_src: "The source code is empty.",
      ver_title: "Compatibility check", ver_none: "No check yet: use “Check” on an example or an imported file.",
      ver_running: "Checking “{0}”…", ver_verdict: "Overall verdict", ver_report: "Open the HTML report",
      ver_rule: "Rule", ver_v: "Verdict", ver_measured: "Measured", ver_limit: "Limit", ver_expl: "Explanation",
      ver_regions: "Usage per region",
      sim_loading: "Loading “{0}” on {1}…", sim_started: "Simulation started.",
      err_action: "Action failed", err_http: "Error", err_network: "Server unreachable.",
      sub_display: "Display", disp_note: "Emulation: image read from the framebuffer, no DSI timing",
      disp_off: "Display inactive — the firmware has not programmed the CDC200 controller yet",
      disp_state: "{0}×{1} · format {2} · framebuffer {3} · {4} fps",
      accel: "Accelerated", accel_tip: "Skips idle periods: the simulation runs much faster than real time, animations play fast-forward",
      quit: "Quit", quit_confirm: "Stop E7 Twin (simulation and server)?",
      quit_done: "E7 Twin is stopped. You can close this tab.",
      npu_none: "No report: run `e7twin npu <model.tflite>`.",
    },
    zh: {
      theme: "◐ 主题", theme_aria: "切换浅色/深色主题", skip: "跳到内容",
      tab_programs: "程序", tab_build: "编译", sub_examples: "示例", sub_mine: "我的程序", mine_none: "暂无程序：请在“编译”中编译 main.c，或在“导入”中导入 ELF。", mine_compiled: "已编译", mine_imported: "已导入", mine_edit: "编辑", mine_delete: "删除", mine_confirm: "删除“{0}”？", mine_added: "已添加到“我的程序”。", mine_view: "在“我的程序”中查看", mine_loading: "加载中…", tab_import: "导入", tab_verify: "检查", tab_sim: "仿真",
      sub_map: "总览", sub_cores: "核心", sub_memory: "内存", sub_timeline: "时间线", sub_npu: "NPU",
      ws_up: "已连接", ws_connecting: "正在连接…", ws_down: "已断开——{0} 秒后重试", 
      tab_tests: "测试", tst_help: "先在仿真器上运行测试，再在开发板上运行同一程序。SIG 行必须一致。", tst_none: "没有测试（名称为 parity_* 的示例）。", tst_run: "在仿真器上运行", tst_running: "正在运行…（最长 2 分钟）", tst_ok: "通过", tst_fail: "失败", tst_score: "得分", tst_duration: "耗时", tst_copy: "复制", tst_copied: "已复制", tst_uart: "UART 输出", tst_board: "在开发板上", tst_no_cmd: "此测试没有可用的命令。", tst_flash_note: "烧录开发板由人工完成，工具绝不会自动烧录。", tst_board_sig: "开发板上读到的 SIG", tst_same: "一致", tst_diff: "不一致",
      start: "开始", pause: "暂停", reset: "复位",
      state_stopped: "已停止", state_running: "运行中", state_paused: "已暂停", state_error: "错误",
      ex_title: "示例", ex_intro: "选择一个示例，进行检查或仿真。",
      ex_build: "编译", ex_building: "正在编译…（可能需要一分钟）", ex_build_all: "编译缺失的示例（{0}）", ex_progress: "{0}/{1}：{2}", ex_build_fail: "编译失败。", ex_build_err: "错误：{0}", ex_log: "编译日志",
      ex_loading: "正在加载示例…", ex_none: "没有可用的示例。",
      ex_built: "已编译", ex_notbuilt: "未编译", ex_cores: "核心", ex_verify: "检查", ex_simulate: "仿真",
      imp_elf_title: "导入 ELF", imp_drop: "将 .elf 文件拖到此处，或点击选择",
      imp_src_title: "编译 main.c", imp_core: "核心", imp_name: "名称", imp_compile: "编译",
      imp_log: "编译日志", imp_uploading: "正在上传“{0}”…", imp_compiling: "正在编译…",
      imp_ok: "ELF 已就绪：{0}", imp_failed: "编译失败。", imp_err_line: "第 {0} 行",
      imp_need_elf: "请选择一个 .elf 文件。", imp_empty_src: "源代码为空。",
      ver_title: "兼容性检查", ver_none: "尚未检查：请对示例或已导入的文件点击“检查”。",
      ver_running: "正在检查“{0}”…", ver_verdict: "总体结论", ver_report: "打开 HTML 报告",
      ver_rule: "规则", ver_v: "结论", ver_measured: "实测值", ver_limit: "限制", ver_expl: "说明",
      ver_regions: "各区域占用情况",
      sim_loading: "正在将“{0}”加载到 {1}…", sim_started: "仿真已开始。",
      err_action: "操作失败", err_http: "错误", err_network: "无法连接服务器。",
      sub_display: "显示屏", disp_note: "仿真：从帧缓冲区读取图像，不含 DSI 时序",
      disp_off: "显示屏未启用——固件尚未配置 CDC200 控制器",
      disp_state: "{0}×{1} · 格式 {2} · 帧缓冲区 {3} · {4} 帧/秒",
      accel: "加速", accel_tip: "跳过空闲等待：仿真速度远快于实时，动画会快进播放",
      quit: "退出", quit_confirm: "停止 E7 Twin（仿真和服务器）？",
      quit_done: "E7 Twin 已停止。您可以关闭此标签页。",
      npu_none: "暂无报告：请运行 `e7twin npu <model.tflite>`。",
    },
  };
  let wsState = null;
  let LANG = "fr";
  try { const l = localStorage.getItem("e7twin-lang"); if (l && I18N[l]) LANG = l; else { const n = (navigator.language || "fr").toLowerCase(); LANG = n.startsWith("zh") ? "zh" : n.startsWith("en") ? "en" : "fr"; } } catch (_) { /* ignoré */ }
  const LOCALE = { fr: "fr-FR", en: "en-GB", zh: "zh-CN" };
  const T = (k, fb) => { const v = I18N[LANG][k]; return v === undefined ? (fb === undefined ? k : fb) : v; };
  const TF = (k, ...a) => T(k).replace(/\{(\d+)\}/g, (_, i) => a[+i]);
  function applyLang() {
    document.documentElement.lang = LANG === "zh" ? "zh-CN" : LANG;
    document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = T(n.dataset.i18n, n.textContent); });
    document.querySelectorAll("[data-i18n-aria]").forEach((n) => n.setAttribute("aria-label", T(n.dataset.i18nAria)));
    document.querySelectorAll("[data-i18n-title]").forEach((n) => n.setAttribute("title", T(n.dataset.i18nTitle)));
    $("btn-lang").value = LANG;
    applyStatus({});
    if (wsState) setWs(wsState.kind, wsState.key, ...wsState.args);
    if (S.tab === "programs") { renderExamples(); renderMine(); }
    if (S.tab === "tests") renderTests();
    if (S.lastReport) { clear($("ver-report")); renderVerify(S.lastReport); }
  }
  $("btn-lang").addEventListener("change", () => {
    LANG = I18N[$("btn-lang").value] ? $("btn-lang").value : "fr";
    try { localStorage.setItem("e7twin-lang", LANG); } catch (_) { /* ignoré */ }
    applyLang();
  });

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
  function showTab(t) {
    S.tab = t;
    document.querySelectorAll("#nav-top [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
    document.querySelectorAll("main > section").forEach((s) => { s.hidden = s.id !== "view-" + t; });
    if (t === "programs") showPview(S.pview);
    if (t === "tests") loadTests();
    if (t === "sim") showView(S.view); else stopProfile();
  }
  let dispOn = false, dispInfo = null, dispFrames = 0, dispT0 = 0, dispFps = 0;
  function renderDisplayState() {
    const st = $("display-state"); if (!st) return;
    if (!dispInfo || !dispInfo.enabled) { st.textContent = T("disp_off"); return; }
    st.textContent = TF("disp_state", dispInfo.width, dispInfo.height, dispInfo.format,
      "0x" + Number(dispInfo.address).toString(16).toUpperCase(), dispFps.toFixed(1));
  }
  async function pollDisplayInfo() {
    try { const r = await fetch("/api/display"); dispInfo = r.ok ? await r.json() : null; } catch (_) { dispInfo = null; }
    const img = $("display-img");
    if (img && dispInfo && dispInfo.width) img.parentElement.style.aspectRatio = dispInfo.width + " / " + dispInfo.height;
    renderDisplayState();
  }
  function nextFrame() {
    if (!dispOn) return;
    const img = $("display-img");
    const again = () => { if (dispOn) setTimeout(nextFrame, 200); };
    img.onload = () => {
      dispFrames++; const now = performance.now();
      if (now - dispT0 >= 1000) { dispFps = dispFrames * 1000 / (now - dispT0); dispFrames = 0; dispT0 = now; renderDisplayState(); }
      img.hidden = false; again();
    };
    img.onerror = () => { img.hidden = true; dispFps = 0; again(); };
    img.src = "/api/display.png?t=" + Date.now();
  }
  function startDisplay() {
    if (dispOn) return;
    dispOn = true; dispFrames = 0; dispT0 = performance.now(); dispFps = 0;
    pollDisplayInfo(); S.dispTimer = setInterval(pollDisplayInfo, 2000); nextFrame();
  }
  function stopDisplay() { dispOn = false; clearInterval(S.dispTimer); }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopDisplay(); else if (S.tab === "sim" && S.view === "display") startDisplay();
  });
  function showPview(v) {
    S.pview = v;
    if (S.tab !== "programs") { showTab("programs"); return; }
    document.querySelectorAll("#nav-prog [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.pview === v)));
    document.querySelectorAll("#view-programs > section.sub").forEach((x) => { x.hidden = x.id !== "view-" + v; });
    if (v === "examples") loadExamples(); else loadMine();
  }
  document.querySelectorAll("#nav-prog [role=tab]").forEach((b) => b.addEventListener("click", () => showPview(b.dataset.pview)));
  function showView(v) {                 // sous-vue de l'onglet Simulation
    S.view = v;
    if (S.tab !== "sim") { showTab("sim"); return; }
    document.querySelectorAll("#nav-sub [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    document.querySelectorAll("#view-sim > section.sub").forEach((s) => { s.hidden = s.id !== "view-" + v; });
    if (v === "cores") renderCoresView();
    if (v === "memory") renderMemory();
    if (v === "npu") loadNpuList();
    if (v === "display") startDisplay(); else stopDisplay();
    if (v !== "cores") stopProfile();
  }
  document.querySelectorAll("#nav-top [role=tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  document.querySelectorAll("#nav-sub [role=tab]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  // ---------- contrôle ----------
  async function simAction(a) {
    try { applyStatus(await api("/api/sim/" + a, { method: "POST" })); showError(""); }
    catch (e) { showError(T("err_action") + " « " + a + " » : " + e.message); }
  }
  $("btn-start").addEventListener("click", () => simAction("start"));
  $("btn-pause").addEventListener("click", () => simAction("pause"));
  $("btn-reset").addEventListener("click", () => simAction("reset"));

    function applyStatus(st) {
    S.status = Object.assign({}, S.status, st);
    const s = S.status.state;
    const b = $("sim-state");
    b.textContent = T("state_" + s, s);
    b.className = "badge state-" + s;
    $("sim-fake").hidden = !S.status.fake;
    const ck = $("chk-accel");
    ck.checked = !!S.status.accelerated;
    ck.disabled = !Object.keys(S.status.images || {}).length;
    $("btn-start").disabled = s === "running";
    $("btn-pause").disabled = s !== "running";
    if (s === "error" && st.message) showError("Erreur de simulation : " + st.message);
    if (s === "stopped" && st.t_virtual_s === 0) resetHistory();
    renderMap();
  }

  $("chk-accel").addEventListener("change", async (e) => {
    const ck = e.target, want = ck.checked;
    try { applyStatus(await api("/api/sim/speed", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accelerated: want }) })); showError(""); }
    catch (err) { ck.checked = !want; showError(T("err_action") + " : " + err.message); }
  });

  // ---------- WebSocket ----------
  let ws = null, retry = 0, quitting = false;
  function setWs(kind, key, ...args) {
    wsState = { kind, key, args };
    $("ws-ind").className = "ws ws-" + kind;
    $("ws-text").textContent = TF(key, ...args);
  }
  function connect() {
    setWs("conn", "ws_connecting");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onopen = () => { retry = 0; setWs("up", "ws_up"); };
    ws.onmessage = (ev) => { try { onMessage(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
    ws.onclose = () => {
      if (quitting) return;
      const delay = Math.min(10000, 500 * 2 ** retry++);
      setWs("down", "ws_down", Math.ceil(delay / 1000));
      setTimeout(connect, delay);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) { /* déjà fermé */ } };
  }

  $("btn-quit").addEventListener("click", async () => {
    if (!confirm(T("quit_confirm"))) return;
    try { await fetch("/api/shutdown", { method: "POST" }); } catch (_) { /* serveur déjà arrêté */ }
    quitting = true;
    try { ws && ws.close(); } catch (_) { /* déjà fermé */ }
    document.body.textContent = "";
    const p = document.createElement("p");
    p.style.cssText = "font:1.2rem sans-serif;text-align:center;margin:20vh 1rem";
    p.textContent = T("quit_done");
    document.body.appendChild(p);
    window.close();
  });

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
  function renderReport(r, out) {
    const head = el("p");
    head.appendChild(document.createTextNode(`${r.image} · ${r.core} · ${T("ver_verdict")} `));
    head.appendChild(badge(r.verdict, "big"));
    out.appendChild(head);
    const t = el("table", { class: "tbl" });
    const hd = el("thead"); const hr = el("tr");
    [T("ver_rule"), T("ver_v"), T("ver_measured"), T("ver_limit"), T("ver_expl")].forEach((h) => hr.appendChild(el("th", {}, h)));
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
    out.appendChild(el("h2", {}, T("ver_regions")));
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

  // ---------- exemples / import / vérification ----------
  const plain = (t) => String(t || "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1").replace(/^#+\s*/gm, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  const coreLabel = (c) => c.replace("m55_", "M55-").toUpperCase();
  let EXAMPLES = null;
  async function loadExamples() {
    $("ex-msg").textContent = EXAMPLES ? "" : T("ex_loading");
    try { EXAMPLES = await api("/api/examples"); $("ex-msg").textContent = ""; }
    catch (e) { $("ex-msg").textContent = T("err_http") + " : " + e.message; return; }
    renderExamples();
  }
  function actionButtons(box, elfs) {      // elfs : {core: chemin}
    const b1 = el("button", { type: "button" }, T("ex_verify")), b2 = el("button", { type: "button", class: "primary" }, T("ex_simulate"));
    b1.addEventListener("click", () => verifyElfs(elfs));
    b2.addEventListener("click", () => simulateElfs(elfs));
    box.appendChild(b1); box.appendChild(b2);
  }
  const EXSTATE = {};   // name -> {msg, log, open} : échec de compilation conservé au changement de langue
  function exStatusEl(name, es) {
    const p = el("p", { class: "ex-status", role: "status" }, es.msg);
    if (es.log) {
      const d = el("details", {}); d.open = !!es.open; d.addEventListener("toggle", () => { es.open = d.open; });
      d.appendChild(el("summary", {}, T("ex_log"))); d.appendChild(el("pre", {}, es.log)); p.appendChild(d);
    }
    return p;
  }
  function renderExamples() {
    const list = $("ex-list"); clear(list);
    if (!EXAMPLES) return;
    if (!EXAMPLES.length) { list.appendChild(el("p", { class: "muted" }, T("ex_none"))); return; }
    EXAMPLES.forEach((x) => {
      const card = el("div", { class: "card ex" });
      const es = EXSTATE[x.name];
      card.appendChild(el("h3", {}, x.name));
      card.appendChild(el("span", { class: "badge " + (x.built ? "v-PASS" : "v-SKIP") }, x.built ? T("ex_built") : T("ex_notbuilt")));
      card.appendChild(el("p", {}, plain(x.description)));
      card.appendChild(el("p", { class: "muted" }, T("ex_cores") + " : " + (x.cores || []).map(coreLabel).join(", ")));
      const row = el("div", { class: "inline" });
      actionButtons(row, x.elfs || {});
      row.querySelectorAll("button").forEach((b) => { b.disabled = !x.built; });
      if (!x.built) {
        const bb = el("button", { type: "button" }, T("ex_build"));
        bb.addEventListener("click", () => buildExample(x.name, card));
        row.appendChild(bb);
      }
      card.appendChild(row);
      if (es && !x.built) card.appendChild(exStatusEl(x.name, es));
      list.appendChild(card);
    });
    const missing = EXAMPLES.filter((x) => !x.built);
    if (missing.length) {
      const all = el("button", { type: "button", class: "primary", id: "ex-build-all" }, TF("ex_build_all", missing.length));
      all.addEventListener("click", () => buildMissing(all));
      list.insertBefore(all, list.firstChild);
    }
  }

  // Compile un exemple ; renvoie {ok, error?}. Met à jour EXAMPLES et la carte si card est fourni.
  async function buildExample(name, card, quiet) {
    const row = card && card.querySelector(".inline");
    const btn = row && row.lastChild;
    const old = card && card.querySelector(".ex-status"); if (old) old.remove();
    const status = el("p", { class: "muted ex-status", role: "status" }, T("ex_building"));
    if (card) { card.appendChild(status); if (btn) btn.disabled = true; }
    try {
      const r = await api("/api/examples/" + encodeURIComponent(name) + "/build", { method: "POST" });
      if (r.example) { const i = EXAMPLES.findIndex((e) => e.name === name); if (i >= 0) EXAMPLES[i] = r.example; }
      if (!r.ok) throw Object.assign(new Error(T("ex_build_fail")), { log: r.log_tail });
      delete EXSTATE[name];
      if (!quiet) renderExamples();
      return { ok: true };
    } catch (e) {
      const es = EXSTATE[name] = { msg: TF("ex_build_err", e.message), log: e.log, open: false };
      if (card) { status.remove(); card.appendChild(exStatusEl(name, es)); }
      if (btn) btn.disabled = false;
      return { ok: false, error: e.message };
    }
  }
  async function buildMissing(btn) {
    btn.disabled = true;
    const todo = EXAMPLES.filter((x) => !x.built).map((x) => x.name);
    const failed = [];
    for (let i = 0; i < todo.length; i++) {
      btn.textContent = TF("ex_progress", i + 1, todo.length, todo[i]);
      const r = await buildExample(todo[i], null, true);
      if (!r.ok) failed.push(todo[i] + " (" + r.error + ")");
    }
    renderExamples();
    if (failed.length) $("ex-msg").textContent = TF("ex_build_err", failed.join(", "));
  }

  async function verifyElfs(elfs) {
    showTab("verify");
    const out = $("ver-report"), msg = $("ver-msg"); clear(out); S.lastReport = null;
    for (const [core, path] of Object.entries(elfs)) {
      msg.textContent = TF("ver_running", path.split(/[\\/]/).pop());
      try {
        const r = await api("/api/check/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ elf: path, core }) });
        S.lastReport = r; msg.textContent = ""; renderVerify(r);
      } catch (e) { msg.textContent = T("err_http") + " : " + e.message; return; }
    }
  }
  function renderVerify(r) {
    const wrap = el("div"); renderReport(r, wrap);
    if (r.report_url) {
      const p = el("p"); p.appendChild(el("a", { href: r.report_url, target: "_blank", rel: "noopener" }, T("ver_report")));
      wrap.insertBefore(p, wrap.children[1] || null);
    }
    $("ver-report").appendChild(wrap);
  }

  async function simulateElfs(elfs) {
    showTab("sim"); showError("");
    try {
      for (const [core, path] of Object.entries(elfs)) {
        $("sim-hint").textContent = TF("sim_loading", path.split(/[\\/]/).pop(), coreLabel(core));
        await api("/api/sim/load", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ core, path }) });
      }
      await simAction("start"); $("sim-hint").textContent = T("sim_started");
    } catch (e) { $("sim-hint").textContent = ""; showError(T("err_http") + " : " + e.message); }
  }

  // ---------- tests carte / simu ----------
  let TESTS = null;
  const TSTATE = {};       // name -> {result, boardSig, openUart, openBoard} (survit au changement de langue)
  const tstate = (n) => (TSTATE[n] = TSTATE[n] || { result: null, boardSig: "", openUart: false, openBoard: false });
  const normSig = (s) => String(s || "").replace(/\s+/g, "").toLowerCase();
  function copyBtn(getText) {
    const b = el("button", { type: "button" }, T("tst_copy"));
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(getText()); b.textContent = T("tst_copied"); setTimeout(() => { b.textContent = T("tst_copy"); }, 1500); }
      catch (_) { b.textContent = T("err_action"); }
    });
    return b;
  }
  async function loadTests() {
    $("tst-msg").textContent = TESTS ? "" : T("ex_loading");
    try { TESTS = await api("/api/tests"); $("tst-msg").textContent = ""; }
    catch (e) { $("tst-msg").textContent = T("err_http") + " : " + e.message; return; }
    renderTests();
  }
  function renderTests() {
    const list = $("tst-list"); if (!list) return; clear(list);
    if (!TESTS) return;
    if (!TESTS.length) { list.appendChild(el("p", { class: "muted" }, T("tst_none"))); return; }
    TESTS.forEach((x) => {
      const card = el("div", { class: "card ex", "data-test": x.name });
      card.appendChild(el("h3", {}, x.name));
      card.appendChild(el("span", { class: "badge " + (x.built ? "v-PASS" : "v-SKIP") }, x.built ? T("ex_built") : T("ex_notbuilt")));
      card.appendChild(el("p", {}, plain(x.description)));
      card.appendChild(el("p", { class: "muted" }, T("ex_cores") + " : " + (x.cores || []).map(coreLabel).join(", ")));
      const row = el("div", { class: "inline" });
      if (!x.built) {
        const bb = el("button", { type: "button" }, T("ex_build"));
        bb.addEventListener("click", () => buildTest(x.name, card, bb));
        row.appendChild(bb);
      } else {
        const run = el("button", { type: "button", class: "primary" }, T("tst_run"));
        run.addEventListener("click", () => runTest(x.name, card, run));
        row.appendChild(run);
      }
      card.appendChild(row);
      const res = el("div", { class: "tst-result" }); card.appendChild(res);
      if (tstate(x.name).result) renderTestResult(res, x.name, tstate(x.name).result);
      list.appendChild(card);
    });
  }
  async function buildTest(name, card, btn) {
    btn.disabled = true;
    const st = el("p", { class: "muted", role: "status" }, T("ex_building"));
    card.querySelector(".tst-result").replaceChildren(st);
    try {
      const r = await api("/api/examples/" + encodeURIComponent(name) + "/build", { method: "POST" });
      if (!r.ok) throw Object.assign(new Error(T("ex_build_fail")), { log: r.log_tail });
      TESTS = await api("/api/tests"); renderTests();
    } catch (e) {
      st.textContent = TF("ex_build_err", e.message); btn.disabled = false;
      if (e.log) { const d = el("details", {}); d.appendChild(el("summary", {}, T("ex_log"))); d.appendChild(el("pre", {}, e.log)); st.appendChild(d); }
    }
  }
  async function runTest(name, card, btn) {
    btn.disabled = true; const old = btn.textContent; btn.textContent = T("tst_running");
    const res = card.querySelector(".tst-result"); clear(res);
    try {
      const r = await api("/api/tests/" + encodeURIComponent(name) + "/run", { method: "POST" });
      const st = tstate(name); st.result = r; st.boardSig = ""; st.openUart = false; st.openBoard = false; renderTestResult(res, name, r);
    } catch (e) { res.appendChild(el("p", { class: "errs" }, TF("ex_build_err", e.message))); }
    btn.disabled = false; btn.textContent = old;
  }
  function renderTestResult(box, name, r) {
    clear(box);
    const head = el("div", { class: "inline" });
    head.appendChild(el("span", { class: "badge verdict " + (r.ok ? "v-PASS" : "v-FAIL") }, r.ok ? T("tst_ok") : T("tst_fail")));
    head.appendChild(el("span", { class: "muted" }, T("tst_score") + " : " + (r.score == null ? "–" : r.score) + " · " + T("tst_duration") + " : " + (r.duration_s == null ? "–" : Number(r.duration_s).toFixed(1) + " s")));
    box.appendChild(head);
    const sigRow = el("div", { class: "inline" });
    sigRow.appendChild(el("code", { class: "sig" }, r.sig || "—"));
    if (r.sig) sigRow.appendChild(copyBtn(() => r.sig));
    box.appendChild(sigRow);
    const st = tstate(name);
    const dU = el("details", {}); dU.open = st.openUart; dU.addEventListener("toggle", () => { st.openUart = dU.open; }); dU.appendChild(el("summary", {}, T("tst_uart"))); dU.appendChild(el("pre", {}, r.uart || "")); box.appendChild(dU);
    const dB = el("details", {}); dB.open = st.openBoard; dB.addEventListener("toggle", () => { st.openBoard = dB.open; }); dB.appendChild(el("summary", {}, T("tst_board")));
    if (r.board_cmd) { dB.appendChild(el("pre", {}, r.board_cmd)); dB.appendChild(copyBtn(() => r.board_cmd)); }
    else dB.appendChild(el("p", { class: "muted" }, T("tst_no_cmd")));
    dB.appendChild(el("p", { class: "muted" }, T("tst_flash_note")));
    box.appendChild(dB);
    const cmp = el("div", { class: "inline" });
    const inp = el("input", { type: "text", class: "tst-board-sig", placeholder: T("tst_board_sig"), "aria-label": T("tst_board_sig") });
    const out = el("strong", { role: "status" }, "");
    const upd = () => {
      if (!inp.value.trim()) { out.textContent = ""; out.className = ""; return; }
      const same = normSig(inp.value) === normSig(r.sig);
      out.textContent = same ? T("tst_same") : T("tst_diff"); out.className = same ? "tst-same" : "tst-diff";
    };
    inp.value = st.boardSig; upd();
    inp.addEventListener("input", () => { st.boardSig = inp.value; upd(); });
    cmp.appendChild(inp); cmp.appendChild(out); box.appendChild(cmp);
  }

  // import ELF
  function importResult(elf, core, boxId) {
    const box = $(boxId || "imp-result"); clear(box); box.hidden = false;
    const norm = String(elf).replace(/\\/g, "/");
    const bi = norm.lastIndexOf("build/");
    const shown = bi >= 0 ? norm.slice(bi) : norm.split("/").pop();
    const p = el("p", { title: elf }, TF("imp_ok", shown) + " — " + T("mine_added"));
    box.appendChild(p);
    const row = el("div", { class: "inline" });
    actionButtons(row, { [core]: elf });
    const v = el("button", { type: "button" }, T("mine_view"));
    v.addEventListener("click", () => showPview("mine"));
    row.appendChild(v); box.appendChild(row);
    MINE = null; loadMine();
  }
  // Mes programmes
  let MINE = null;
  async function loadMine() {
    $("mine-msg").textContent = MINE ? "" : T("mine_loading");
    try { MINE = await api("/api/programs"); $("mine-msg").textContent = ""; }
    catch (e) { $("mine-msg").textContent = T("err_http") + " : " + e.message; return; }
    renderMine();
  }
  function renderMine() {
    const list = $("mine-list"); clear(list);
    if (!MINE) return;
    if (!MINE.length) { list.appendChild(el("p", { class: "muted" }, T("mine_none"))); return; }
    MINE.forEach((x) => {
      const card = el("div", { class: "card ex" });
      card.appendChild(el("h3", {}, x.name));
      card.appendChild(el("span", { class: "badge " + (x.origin === "compiled" ? "v-PASS" : "v-SKIP") }, x.origin === "compiled" ? T("mine_compiled") : T("mine_imported")));
      card.appendChild(el("p", { class: "muted" }, T("ex_cores") + " : " + (x.core_guess ? coreLabel(x.core_guess) : "?") + " · " + new Date(x.mtime * 1000).toLocaleString(LOCALE[LANG])));
      const core = x.core_guess || (execCores()[0] || {}).id;
      const row = el("div", { class: "inline" });
      actionButtons(row, { [core]: x.elf });
      if (x.has_source) {
        const e = el("button", { type: "button" }, T("mine_edit"));
        e.addEventListener("click", () => editProgram(x, core)); row.appendChild(e);
      }
      const d = el("button", { type: "button" }, T("mine_delete"));
      d.addEventListener("click", () => deleteProgram(x)); row.appendChild(d);
      card.appendChild(row); list.appendChild(card);
    });
  }
  async function editProgram(x, core) {
    try {
      const r = await fetch("/api/programs/" + encodeURIComponent(x.name) + "/source");
      if (!r.ok) throw new Error("HTTP " + r.status);
      $("src-text").value = await r.text();
      $("src-name").value = x.name; if (core) $("src-core").value = core;
      showTab("build");
    } catch (e) { $("mine-msg").textContent = T("err_http") + " : " + e.message; }
  }
  async function deleteProgram(x) {
    if (!confirm(TF("mine_confirm", x.name))) return;
    try { await api("/api/programs/" + encodeURIComponent(x.name), { method: "DELETE" }); MINE = null; loadMine(); }
    catch (e) { $("mine-msg").textContent = e.message; }
  }
  async function uploadElf(file) {
    const msg = $("imp-msg"); $("imp-result").hidden = true;
    if (!file) { msg.textContent = T("imp_need_elf"); return; }
    msg.textContent = TF("imp_uploading", file.name);
    try {
      const r = await api("/api/upload?name=" + encodeURIComponent(file.name), { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
      msg.textContent = ""; importResult(r.elf, r.core_guess || (execCores()[0] || {}).id);
    } catch (e) { msg.textContent = T("err_http") + " : " + e.message; }
  }
  const drop = $("drop");
  drop.addEventListener("click", () => $("elf-file").click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("elf-file").click(); } });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadElf(e.dataTransfer.files[0]); });
  $("elf-file").addEventListener("change", (e) => uploadElf(e.target.files[0]));

  // compilation d'un main.c
  $("src-file").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    $("src-text").value = await f.text();
    const n = f.name.replace(/\.c$/i, "").replace(/[^A-Za-z0-9_]/g, "_"); if (n) $("src-name").value = n;
  });
  $("btn-compile").addEventListener("click", async () => {
    const msg = $("bld-msg"), errs = $("bld-errors"), logBox = $("bld-log-box");
    clear(errs); logBox.hidden = true; $("bld-result").hidden = true;
    const text = $("src-text").value;
    if (!text.trim()) { msg.textContent = T("imp_empty_src"); return; }
    msg.textContent = T("imp_compiling"); $("btn-compile").disabled = true;
    const core = $("src-core").value, name = $("src-name").value.trim() || "main";
    try {
      const r = await api("/api/build/source?core=" + encodeURIComponent(core) + "&name=" + encodeURIComponent(name),
        { method: "POST", headers: { "Content-Type": "text/plain; charset=utf-8" }, body: text });
      if (r.log) { $("bld-log").textContent = r.log; logBox.hidden = false; }
      if (r.ok && r.elf) { msg.textContent = ""; importResult(r.elf, r.core_guess || core, "bld-result"); }
      else {
        msg.textContent = T("imp_failed");
        const ul = el("ul", { class: "errs" });
        (r.errors || []).forEach((x) => ul.appendChild(el("li", {}, `${x.file ? String(x.file).split(/[\\/]/).pop() : ""} ${TF("imp_err_line", x.line)}${x.col ? ":" + x.col : ""} — ${x.message}`)));
        errs.appendChild(ul);
      }
    } catch (e) { msg.textContent = T("err_http") + " : " + e.message; }
    finally { $("btn-compile").disabled = false; }
  });

  // ---------- NPU ----------
  async function loadNpuList() {
    const sel = $("npu-select"), out = $("npu-report"), msg = $("npu-msg");
    let list = [];
    try { list = await api("/api/npu"); } catch (e) { msg.textContent = "Échec : " + e.message; return; }
    clear(sel); clear(out);
    if (!list.length) {
      msg.textContent = T("npu_none");
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
    const v = location.hash.slice(1);   // ex. /#cores ou /#verify : lien direct vers une vue
    applyLang();
    if (document.querySelector(`#nav-sub [data-view="${v}"]`)) showView(v);
    else if (document.querySelector(`#nav-top [data-tab="${v}"]`)) showTab(v);
    else showTab("programs");
  }
  init();
})();
