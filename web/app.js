/* dotdeps web UI — form scan, daftar masalah, dan graph (Cytoscape.js + dagre). */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const STORE_KEY = "dotdeps.form.v2";
const STATUS_TEXT = {
  ok: "OK",
  broken: "File tidak ditemukan",
  missing: "Belum terinstall",
  outside: "Di luar repo",
};

const state = { source: "samples", options: null, data: null, elapsed: 0 };
let cy = null;
let graphDirty = false;

// ---------------------------------------------------------------------------
// Utilitas
// ---------------------------------------------------------------------------

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function load() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
}

function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      source: state.source, repo: $("#repo").value.trim(), apps: checkedApps(),
      all: $("#opt-all").checked, follow: $("#opt-follow").checked,
    }));
  } catch { /* penyimpanan tidak tersedia */ }
}

const measureCtx = document.createElement("canvas").getContext("2d");
function textWidth(text) {
  measureCtx.font = "600 12px system-ui, sans-serif";
  return measureCtx.measureText(text).width;
}

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------

function checkedApps() {
  return $$("#apps input:checked").map((i) => i.value);
}

function setSource(source) {
  state.source = source;
  $$(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.source === source)));
  $$("[data-for]").forEach((el) => el.classList.toggle("hidden", !el.dataset.for.split(" ").includes(source)));
  if (source === "github") $("#repo").focus();
  else loadOptions(load().apps);
  refreshCli();
}

async function loadOptions(preferred) {
  try {
    const res = await fetch(`/api/options?source=${state.source}`);
    state.options = await res.json();
  } catch {
    return;
  }
  const o = state.options;
  if (state.source === "real") $("#real-path").textContent = o.config_dir;
  const wanted = preferred && preferred.length ? preferred : o.default_apps;
  let selected = o.apps.filter((a) => wanted.includes(a));
  if (!selected.length) selected = o.apps.filter((a) => o.default_apps.includes(a));
  $("#apps").innerHTML = o.apps.map((a) =>
    `<label><input type="checkbox" value="${esc(a)}" ${selected.includes(a) ? "checked" : ""}> ${esc(a)}</label>`
  ).join("");
  refreshCli();
}

function cliCommand() {
  const parts = ["python3 main.py"];
  if (state.source === "samples") parts.push("--home samples");
  parts.push("scan");
  if (state.source === "github") parts.push(`--github ${$("#repo").value.trim() || "user/dotfiles"}`);
  if ($("#opt-all").checked) {
    parts.push("--all");
  } else if (state.source !== "github" && state.options) {
    const apps = checkedApps();
    const defaults = state.options.default_apps.filter((a) => state.options.apps.includes(a));
    const same = defaults.length === apps.length && defaults.every((a) => apps.includes(a));
    if (!same) apps.forEach((a) => parts.push(`-a ${a}`));
  }
  if (state.source !== "github" && !$("#opt-follow").checked) parts.push("--no-follow");
  return parts.join(" ");
}

function refreshCli() {
  $("#cli-cmd").textContent = cliCommand();
  save();
}

function setStatus(text, isError = false) {
  $("#status").textContent = text;
  $("#status").classList.toggle("error", isError);
}

async function runScan() {
  const params = new URLSearchParams({
    source: state.source,
    all: $("#opt-all").checked ? "1" : "0",
    follow: $("#opt-follow").checked ? "1" : "0",
  });
  if (state.source === "github") {
    const repo = $("#repo").value.trim();
    if (!repo) {
      setStatus("Masukkan nama repo, misalnya user/dotfiles.", true);
      $("#repo").focus();
      return;
    }
    params.set("repo", repo);
  } else {
    const apps = checkedApps();
    if (!apps.length && !$("#opt-all").checked) {
      setStatus("Pilih minimal satu app di Opsi lanjutan.", true);
      return;
    }
    params.set("apps", apps.join(","));
  }

  const btn = $("#btn-scan");
  btn.disabled = true;
  btn.textContent = "Memindai…";
  setStatus(state.source === "github" ? "Mengunduh repo dari GitHub… biasanya 5–15 detik." : "");
  const t0 = performance.now();
  try {
    const res = await fetch(`/api/scan?${params}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    state.data = data;
    state.elapsed = Math.round(performance.now() - t0);
    setStatus("");
    renderResult();
  } catch (err) {
    setStatus(`Gagal: ${err.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Scan";
  }
}

// ---------------------------------------------------------------------------
// Ringkasan & daftar masalah
// ---------------------------------------------------------------------------

function nodesBy(status) {
  return state.data.elements.nodes.map((n) => n.data).filter((n) => n.status === status);
}

function renderResult() {
  const d = state.data;
  const st = d.stats;
  const repo = d.meta.repo;
  const errors = st.broken + st.missing + st.cycles;

  const parts = [];
  if (st.missing) parts.push(`${st.missing} program belum terinstall${repo ? " di laptopmu" : ""}`);
  if (st.broken) parts.push(`${st.broken} file tidak ditemukan`);
  if (st.cycles) parts.push(`${st.cycles} include melingkar`);

  const source = repo
    ? `Repo <a href="${esc(repo.url)}" target="_blank" rel="noopener">${esc(repo.label)}</a> · folder <code>${esc(repo.config_root)}</code>`
    : `<code>${esc(d.meta.config_dir)}</code>`;
  $("#verdict").className = `verdict ${errors ? "bad" : "ok"}`;
  $("#verdict").innerHTML = `
    <div class="icon">${errors ? "!" : "✓"}</div>
    <div>
      <h2>${errors ? `${errors} masalah ditemukan` : "Semua dependensi terpenuhi"}</h2>
      ${parts.length ? `<p class="breakdown">${parts.join(" · ")}</p>` : ""}
      <p class="meta">${source} · app: ${esc(d.meta.apps.join(", "))} ·
        ${d.meta.parsed_files} file dibaca · ${st.files} file · ${st.programs} program · ${state.elapsed} ms</p>
    </div>`;

  renderIssues();
  $("#result").classList.remove("hidden");
  graphDirty = true;
  if (cy) { cy.destroy(); cy = null; }
  closeDrawer();
  showTab("issues");
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function whereText(refs) {
  if (!refs.length) return "";
  const first = `${refs[0].source}:${refs[0].line}`;
  return refs.length > 1 ? `${first} · +${refs.length - 1} lokasi lain` : first;
}

function rowsHtml(nodes) {
  return `<div class="rows">${nodes.map((n) => `
    <div class="row">
      <span class="name">${esc(n.path)}</span>
      <span class="where">${esc(whereText(n.refs))}</span>
      <button type="button" class="go" data-node="${esc(n.id)}">Lihat di graph</button>
    </div>`).join("")}</div>`;
}

function renderIssues() {
  const d = state.data;
  const repo = d.meta.repo;
  const missing = nodesBy("missing");
  const broken = nodesBy("broken");
  const outside = nodesBy("outside");
  const html = [];

  if (missing.length) {
    html.push(`<section class="group">
      <h3><i class="dot bad"></i>${repo ? "Program yang belum terinstall di laptopmu" : "Program belum terinstall"}
        <span class="count">${missing.length}</span></h3>
      <p class="hint">Cari nama paketnya dengan <code>pacman -F &lt;nama&gt;</code>.</p>
      ${rowsHtml(missing)}
    </section>`);
  }
  if (broken.length) {
    html.push(`<section class="group">
      <h3><i class="dot bad"></i>File tidak ditemukan <span class="count">${broken.length}</span></h3>
      ${rowsHtml(broken)}
    </section>`);
  }
  if (d.cycles.length) {
    html.push(`<section class="group">
      <h3><i class="dot cycle"></i>Include melingkar <span class="count">${d.cycles.length}</span></h3>
      <div class="rows">${d.cycles.map((c) =>
        `<div class="row"><span class="where">${c.map(esc).join(" → ")}</span></div>`).join("")}</div>
    </section>`);
  }
  if (!html.length) {
    html.push('<p class="empty-ok">Tidak ada masalah. Semua file dan program yang dipanggil ditemukan.</p>');
  }
  if (outside.length) {
    html.push(`<details class="group">
      <summary><i class="dot warn"></i>Di luar repo, bukan error <span class="count">${outside.length}</span></summary>
      <p class="hint">File atau script yang memang tidak ikut di-upload, misalnya hasil generate tema atau script di $PATH pemiliknya.</p>
      ${rowsHtml(outside)}
    </details>`);
  }
  if (d.unresolved.length) {
    html.push(`<details class="group">
      <summary><i class="dot muted"></i>Tidak bisa dicek (nilai dinamis) <span class="count">${d.unresolved.length}</span></summary>
      <p class="hint">Perintah yang baru terbentuk saat program berjalan, jadi tidak bisa dicek tanpa menjalankannya.</p>
      <div class="rows">${d.unresolved.slice(0, 60).map((u) => `
        <div class="row"><span class="name">${esc(u.target)}</span>
        <span class="where">${esc(u.source)}:${u.line}</span></div>`).join("")}</div>
    </details>`);
  }
  $("#panel-issues").innerHTML = html.join("");
}

// ---------------------------------------------------------------------------
// Tab
// ---------------------------------------------------------------------------

function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("#panel-issues").classList.toggle("hidden", name !== "issues");
  $("#panel-graph").classList.toggle("hidden", name !== "graph");
  // Cytoscape butuh container yang terlihat untuk menghitung ukuran.
  if (name === "graph" && graphDirty) buildGraph();
}

// ---------------------------------------------------------------------------
// Graph (Cytoscape.js)
// ---------------------------------------------------------------------------

function graphStyle() {
  const c = {
    text: cssVar("--text"),
    file: cssVar("--file"), fileFill: cssVar("--file-fill"),
    bin: cssVar("--bin"), binFill: cssVar("--bin-fill"),
    bad: cssVar("--bad"), badFill: cssVar("--bad-fill"),
    warn: cssVar("--warn"), warnFill: cssVar("--warn-fill"),
    cycle: cssVar("--cycle"), edge: cssVar("--edge"),
    ext: cssVar("--ext"), extFill: cssVar("--ext-fill"),
    hl: cssVar("--highlight"), accent: cssVar("--accent"),
  };
  return [
    {
      selector: "node",
      style: {
        label: "data(label)", "font-size": 12, "font-weight": 600, color: c.text,
        "text-valign": "center", "text-halign": "center",
        width: (n) => Math.max(36, textWidth(n.data("label")) + 22), height: 26,
        shape: "round-rectangle", "background-color": c.fileFill,
        "border-color": c.file, "border-width": 1.5, "min-zoomed-font-size": 7,
      },
    },
    { selector: 'node[kind = "bin"]', style: { "corner-radius": 13, "background-color": c.binFill, "border-color": c.bin } },
    { selector: "node[?root]", style: { "border-width": 3.5 } },
    { selector: "node[?external]", style: { "background-color": c.extFill, "border-color": c.ext, "border-style": "dashed" } },
    { selector: "node[?cycle]", style: { "border-color": c.cycle, "border-width": 3, "border-style": "double" } },
    { selector: 'node[status = "outside"]', style: { "background-color": c.warnFill, "border-color": c.warn, "border-style": "dashed", color: c.warn } },
    {
      selector: 'node[status = "broken"], node[status = "missing"]',
      style: { "background-color": c.badFill, "border-color": c.bad, "border-width": 2.5, "border-style": "solid", color: c.bad },
    },
    {
      selector: "edge",
      style: {
        width: 1.3, "line-color": c.edge, "target-arrow-color": c.edge,
        "target-arrow-shape": "triangle", "arrow-scale": 0.9, "curve-style": "bezier",
      },
    },
    { selector: "edge[?cycle]", style: { "line-color": c.cycle, "target-arrow-color": c.cycle, width: 2.5, "line-style": "dashed" } },
    { selector: ".faded", style: { opacity: 0.12 } },
    { selector: "node.match", style: { "underlay-color": c.hl, "underlay-opacity": 0.45, "underlay-padding": 6 } },
    { selector: "node.up", style: { "underlay-color": c.hl, "underlay-opacity": 0.3, "underlay-padding": 4 } },
    { selector: "edge.up", style: { "line-color": c.hl, "target-arrow-color": c.hl, width: 2.5 } },
    { selector: "node.down", style: { "underlay-color": c.accent, "underlay-opacity": 0.25, "underlay-padding": 4 } },
    { selector: "edge.down", style: { "line-color": c.accent, "target-arrow-color": c.accent, width: 2.2 } },
    { selector: "node:selected", style: { "underlay-color": c.hl, "underlay-opacity": 0.7, "underlay-padding": 7 } },
    { selector: ".gone", style: { display: "none" } },
  ];
}

function layoutOptions(animate) {
  const common = { animate, animationDuration: 350, fit: true, padding: 30 };
  switch ($("#layout").value) {
    case "dagre-lr": return { ...common, name: "dagre", rankDir: "LR", nodeSep: 10, rankSep: 70, edgeSep: 6 };
    case "dagre-tb": return { ...common, name: "dagre", rankDir: "TB", nodeSep: 16, rankSep: 60 };
    case "cose": return { ...common, name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70 };
    default: return {
      ...common, name: "concentric", minNodeSpacing: 12, levelWidth: () => 1,
      concentric: (n) => (n.data("root") ? 3 : n.data("kind") === "file" ? 2 : 1),
    };
  }
}

function runLayout(animate = true) {
  if (cy) cy.elements().not(".gone").layout(layoutOptions(animate)).run();
}

function buildGraph() {
  graphDirty = false;
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#cy"),
    elements: state.data.elements,
    style: graphStyle(),
    minZoom: 0.05,
    maxZoom: 4,
  });
  cy.on("tap", "node", (evt) => selectNode(evt.target));
  cy.on("tap", (evt) => { if (evt.target === cy) { clearHighlight(); closeDrawer(); } });
  applyFilters(false);
}

function applyFilters(animate = true) {
  if (!cy) return;
  const showPrograms = $("#f-programs").checked;
  const onlyProblems = $("#f-problems").checked;
  cy.batch(() => {
    cy.elements().removeClass("gone");
    if (!showPrograms) cy.nodes('[kind = "bin"]').addClass("gone");
    if (onlyProblems) {
      const bad = cy.nodes('[status = "broken"], [status = "missing"], node[?cycle]');
      cy.elements().not(bad.union(bad.predecessors())).addClass("gone");
    }
    // Sembunyikan node yang semua induknya ikut tersembunyi.
    let changed = true;
    while (changed) {
      changed = false;
      cy.nodes().not(".gone").forEach((n) => {
        const parents = n.incomers("node");
        if (parents.length && parents.every((p) => p.hasClass("gone"))) { n.addClass("gone"); changed = true; }
      });
    }
    cy.edges().forEach((e) => {
      if (e.source().hasClass("gone") || e.target().hasClass("gone")) e.addClass("gone");
    });
  });
  runLayout(animate);
}

function clearHighlight() {
  if (!cy) return;
  cy.elements().removeClass("faded up down match").unselect();
}

function selectNode(node, center = false) {
  clearHighlight();
  node.select();
  const up = node.predecessors();
  const down = node.successors();
  cy.elements().not(up.union(down).union(node)).addClass("faded");
  up.addClass("up");
  down.addClass("down");
  openDrawer(node);
  if (center) centerVisible(node);
}

// Tengahkan node di area graph yang tidak tertutup drawer
// (drawer di kanan pada desktop, di bawah pada layar sempit).
function centerVisible(node) {
  const drawer = $("#drawer");
  const fullWidth = drawer.offsetWidth >= cy.width();
  const w = cy.width() - (fullWidth ? 0 : drawer.offsetWidth);
  const h = cy.height() - (fullWidth ? drawer.offsetHeight : 0);
  const zoom = Math.max(cy.zoom(), 1.1);
  const pos = node.position();
  cy.animate({ zoom, pan: { x: w / 2 - pos.x * zoom, y: h / 2 - pos.y * zoom } }, { duration: 300 });
}

function focusNode(id) {
  showTab("graph");
  const node = cy && cy.getElementById(id);
  if (!node || node.empty()) return;
  if (node.hasClass("gone")) {
    $("#f-programs").checked = true;
    $("#f-problems").checked = false;
    applyFilters(false);
  }
  selectNode(node, true);
  $("#panel-graph").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function search(query, select = false) {
  if (!cy) return;
  clearHighlight();
  const q = query.trim().toLowerCase();
  if (!q) return;
  const hits = cy.nodes().not(".gone").filter((n) =>
    n.data("label").toLowerCase().includes(q) || n.data("path").toLowerCase().includes(q));
  cy.elements().not(hits).addClass("faded");
  hits.addClass("match");
  if (select && hits.length) {
    const exact = hits.filter((n) => n.data("label").toLowerCase() === q);
    selectNode((exact.length ? exact : hits).first(), true);
  } else if (hits.length) {
    cy.animate({ fit: { eles: hits, padding: 80 } }, { duration: 250 });
  }
}

// ---------------------------------------------------------------------------
// Drawer detail node
// ---------------------------------------------------------------------------

function chainsTo(node, limit = 8) {
  const out = [];
  const walk = (n, path) => {
    if (out.length >= limit) return;
    const parents = n.incomers("node").filter((p) => !path.includes(p));
    if (!parents.length) { out.push([...path].reverse()); return; }
    parents.forEach((p) => walk(p, [...path, p]));
  };
  walk(node, [node]);
  return out.filter((c) => c.length > 1);
}

function nodeLink(n) {
  return `<button type="button" class="node-link" data-node="${esc(n.id())}">${esc(n.data("label"))}</button>`;
}

function statusBadge(status) {
  const cls = { ok: "ok", outside: "warn" }[status] || "bad";
  return `<span class="badge ${cls}">${STATUS_TEXT[status]}</span>`;
}

function openDrawer(node) {
  const d = node.data();
  const badges = [statusBadge(d.status)];
  if (d.cycle) badges.push('<span class="badge cycle">dalam cycle</span>');
  if (d.root) badges.push('<span class="badge neutral">titik masuk</span>');
  if (d.external) badges.push('<span class="badge neutral">di luar .config</span>');

  const info = d.kind === "bin" ? `Lokasi: ${esc(d.resolved || "tidak ditemukan di $PATH")}` : esc(d.path);
  const refs = d.refs.slice(0, 12).map((r) =>
    `<li>${esc(r.source)}:${r.line} <span class="muted">[${esc(r.directive)}]</span>
     <span class="raw"><code>${esc(r.raw.slice(0, 120))}</code></span></li>`).join("");
  const chains = chainsTo(node).map((c) =>
    `<li class="chain">${c.map(nodeLink).join('<span class="arrow">→</span>')}</li>`).join("");
  const kids = node.outgoers("node");
  const children = kids.map((n) =>
    `<li>${n.data("kind") === "bin" ? "⚙" : "📄"} ${nodeLink(n)}
     ${n.data("status") !== "ok" ? statusBadge(n.data("status")) : ""}</li>`).join("");

  $("#drawer-body").innerHTML = `
    <h3>${d.kind === "bin" ? "⚙" : "📄"} ${esc(d.label)}</h3>
    <div>${badges.join("")}</div>
    <p class="path">${info}</p>
    ${refs ? `<h4>Dipanggil di (${d.refs.length})</h4><ul>${refs}</ul>` : ""}
    ${chains ? `<h4>Jalur dari titik masuk</h4><ul>${chains}</ul>` : ""}
    ${children ? `<h4>Bergantung pada (${kids.length})</h4><ul>${children}</ul>` : ""}`;
  $("#drawer").classList.remove("hidden");
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Event
// ---------------------------------------------------------------------------

function init() {
  const saved = load();
  if (saved.repo) $("#repo").value = saved.repo;
  if (typeof saved.all === "boolean") $("#opt-all").checked = saved.all;
  if (typeof saved.follow === "boolean") $("#opt-follow").checked = saved.follow;

  $$(".seg button").forEach((b) => b.addEventListener("click", () => setSource(b.dataset.source)));
  $("#scan-form").addEventListener("submit", (e) => { e.preventDefault(); runScan(); });
  $("#repo").addEventListener("input", refreshCli);
  $$(".chip-btn").forEach((b) => b.addEventListener("click", () => {
    $("#repo").value = b.dataset.repo;
    refreshCli();
    runScan();
  }));
  $("#apps").addEventListener("change", refreshCli);
  $("#opt-all").addEventListener("change", refreshCli);
  $("#opt-follow").addEventListener("change", refreshCli);
  $("#apps-default").addEventListener("click", () => {
    $$("#apps input").forEach((i) => { i.checked = state.options.default_apps.includes(i.value); });
    refreshCli();
  });
  $("#apps-all").addEventListener("click", () => {
    $$("#apps input").forEach((i) => { i.checked = true; });
    refreshCli();
  });

  $$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  $("#layout").addEventListener("change", () => runLayout());
  $("#f-programs").addEventListener("change", () => applyFilters());
  $("#f-problems").addEventListener("change", () => applyFilters());
  $("#btn-fit").addEventListener("click", () =>
    cy && cy.animate({ fit: { eles: cy.elements().not(".gone"), padding: 30 } }, { duration: 250 }));
  $("#btn-png").addEventListener("click", () => {
    if (!cy) return;
    const a = document.createElement("a");
    a.href = cy.png({ full: true, scale: 2, bg: cssVar("--surface") });
    a.download = "dotdeps-graph.png";
    a.click();
  });
  $("#drawer-close").addEventListener("click", () => { closeDrawer(); clearHighlight(); });

  const searchBox = $("#search");
  searchBox.addEventListener("input", () => search(searchBox.value));
  searchBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter") search(searchBox.value, true);
    if (e.key === "Escape") { searchBox.value = ""; clearHighlight(); }
  });

  document.body.addEventListener("click", (e) => {
    const target = e.target.closest("[data-node]");
    if (target) focusNode(target.dataset.node);
  });

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => cy && cy.style(graphStyle()));

  setSource(saved.source || "samples");
}

init();
