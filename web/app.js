/* Tampilan dotdeps versi sederhana: daftar dependensi (kiri) yang terhubung dengan graph (kanan).
   Datanya diambil dari /api/scan, yang dihitung oleh dotdeps.py. */

const $ = (s) => document.querySelector(s);
let sumber = "laptop";
let data = null;
let cy = null;

const nama = (path) => path.split("/").pop() || path;
const folder = (path) => path.slice(0, path.length - nama(path).length);
const warna = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const ukur = document.createElement("canvas").getContext("2d");
ukur.font = "12px system-ui, sans-serif";

function label(path) {
  const l = el("span", "label");
  l.append(el("span", "name", nama(path)));
  if (folder(path)) l.append(el("span", "dir", folder(path)));
  return l;
}

function el(tag, kelas, teks) {
  const e = document.createElement(tag);
  if (kelas) e.className = kelas;
  if (teks !== undefined) e.textContent = teks;
  return e;
}

/* ---------- Scan ---------- */
async function scan() {
  $("#btn-scan").disabled = true;
  $("#status").className = "status";
  $("#status").textContent = "Memindai…";
  try {
    const res = await fetch(`/api/scan?sumber=${sumber}`);
    if (!res.ok) throw new Error(`server membalas ${res.status}`);
    data = await res.json();
    $("#status").textContent = "";
    $("#result").classList.remove("hidden");
    tampilVerdict();
    tampilGraph();
    tampilDaftar();
  } catch (err) {
    $("#status").className = "status error";
    $("#status").textContent = `Gagal memindai: ${err.message}`;
  } finally {
    $("#btn-scan").disabled = false;
  }
}

function semuaDeps() {
  return data.files.flatMap((f) => f.deps.map((d) => ({ ...d, file: f.file })));
}

const diCycle = (id) => data.cycles.some((c) => c.includes(id));

/* ---------- Ringkasan ---------- */
function tampilVerdict() {
  const deps = semuaDeps();
  const unik = (jenis) => new Set(deps.filter((d) => d.masalah === jenis).map((d) => d.target)).size;
  const broken = unik("BROKEN LINK");
  const hilang = unik("TIDAK ADA");
  const cycle = data.cycles.length;
  const total = broken + hilang + cycle;
  const program = new Set(deps.filter((d) => d.jenis === "program").map((d) => d.target)).size;

  const v = $("#verdict");
  v.className = "verdict " + (total ? "bad" : "ok");
  v.replaceChildren(el("div", "icon", total ? "!" : "✓"));
  const teks = el("div");
  teks.append(el("h2", "", total ? `${total} masalah ditemukan` : "Semua dependensi aman"));
  if (total) teks.append(el("p", "", `${broken} broken link · ${hilang} program tidak ada · ${cycle} include melingkar`));
  teks.append(el("p", "", `${data.files.length} file config · ${program} program · dipindai dari ${data.home}`));
  v.append(teks);
}

/* ---------- Daftar dependensi (kiri) ---------- */
function tampilDaftar() {
  const kata = $("#search").value.trim().toLowerCase();
  const hanyaMasalah = $("#only-bad").checked;
  const cocok = (s) => !kata || s.toLowerCase().includes(kata);
  const list = $("#list");
  list.replaceChildren();

  for (const f of data.files) {
    const deps = f.deps.filter((d) =>
      (cocok(d.target) || cocok(f.file)) && (!hanyaMasalah || d.masalah || (diCycle(d.target) && diCycle(f.file))));
    if (!deps.length && !(cocok(f.file) && !hanyaMasalah)) continue;

    const grup = el("div", "group");
    const judul = el("button", "group-title");
    judul.type = "button";
    judul.dataset.id = f.file;
    judul.title = f.file;
    judul.append(el("i", "dot file"), label(f.file));
    if (diCycle(f.file)) judul.append(el("span", "badge cycle", "MELINGKAR"));
    judul.append(el("span", "count", `${f.deps.length} dependensi`));
    judul.onclick = () => pilih(f.file);
    grup.append(judul);

    for (const d of deps) {
      const baris = el("button", "item");
      baris.type = "button";
      baris.dataset.id = d.target;
      baris.title = d.target;
      baris.append(el("i", "dot " + d.jenis), label(d.target));
      if (d.masalah) baris.append(el("span", "badge bad", d.masalah));
      else if (diCycle(d.target) && diCycle(f.file)) baris.append(el("span", "badge cycle", "MELINGKAR"));
      baris.onclick = () => pilih(d.target);
      grup.append(baris);
    }
    list.append(grup);
  }

  if (!list.children.length) list.append(el("p", "kosong", hanyaMasalah ? "Tidak ada masalah 🎉" : "Tidak ada yang cocok"));
  saringGraph();
}

/* ---------- Graph (kanan) ---------- */
function elemenGraph() {
  const nodes = new Map();
  const edges = new Map();
  const edgeCycle = new Set();
  for (const c of data.cycles) {
    for (let i = 0; i < c.length - 1; i++) edgeCycle.add(c[i] + " → " + c[i + 1]);
  }

  const tambahNode = (id, jenis, masalah) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, label: nama(id), jenis, masalah: "", cycle: diCycle(id),
      luar: jenis === "file" && !id.startsWith("~/.config/"),
    });
    if (masalah) nodes.get(id).masalah = masalah;
  };

  for (const f of data.files) {
    tambahNode(f.file, "file", "");
    for (const d of f.deps) {
      tambahNode(d.target, d.jenis, d.masalah);
      const id = f.file + " → " + d.target;
      edges.set(id, { id, source: f.file, target: d.target, cycle: edgeCycle.has(id) });
    }
  }
  return [...nodes.values(), ...edges.values()].map((d) => ({ data: d }));
}

function tampilGraph() {
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#cy"),
    elements: elemenGraph(),
    maxZoom: 2,
    wheelSensitivity: 0.3,
    style: [
      {
        selector: "node",
        style: {
          label: "data(label)", "font-size": 12, color: warna("--text"),
          "text-valign": "center", "text-halign": "center",
          width: (n) => Math.max(36, ukur.measureText(n.data("label")).width + 22), height: 26,
          shape: "round-rectangle", "background-color": warna("--file-fill"),
          "border-color": warna("--file"), "border-width": 1.5,
        },
      },
      { selector: 'node[jenis = "program"]', style: { "corner-radius": 13, "background-color": warna("--bin-fill"), "border-color": warna("--bin") } },
      { selector: "node[?luar]", style: { "background-color": warna("--ext-fill"), "border-color": warna("--ext"), "border-style": "dashed" } },
      { selector: "node[?cycle]", style: { "border-color": warna("--cycle"), "border-width": 3, "border-style": "double" } },
      { selector: 'node[masalah != ""]', style: { "background-color": warna("--bad-fill"), "border-color": warna("--bad"), "border-width": 2.5, color: warna("--bad") } },
      {
        selector: "edge",
        style: {
          width: 1.3, "line-color": warna("--edge"), "target-arrow-color": warna("--edge"),
          "target-arrow-shape": "triangle", "arrow-scale": 0.9, "curve-style": "bezier",
        },
      },
      { selector: "edge[?cycle]", style: { "line-color": warna("--cycle"), "target-arrow-color": warna("--cycle"), width: 2.5, "line-style": "dashed" } },
      { selector: ".pudar", style: { opacity: 0.12 } },
      { selector: "node.cocok", style: { "underlay-color": warna("--highlight"), "underlay-opacity": 0.45, "underlay-padding": 6 } },
      { selector: "node.sorot", style: { "underlay-color": warna("--highlight"), "underlay-opacity": 0.7, "underlay-padding": 7 } },
      { selector: "edge.sorot", style: { "line-color": warna("--highlight"), "target-arrow-color": warna("--highlight"), width: 2.5 } },
    ],
    layout: { name: "dagre", rankDir: "LR", nodeSep: 10, rankSep: 70, padding: 20 },
  });

  cy.on("tap", "node", (e) => pilih(e.target.id()));
  cy.on("tap", (e) => { if (e.target === cy) lepas(); });
}

/* Pencarian & "Hanya masalah" juga berlaku di graph */
function saringGraph() {
  if (!cy) return;
  const kata = $("#search").value.trim().toLowerCase();
  cy.elements().removeClass("pudar sorot cocok");
  if (kata) cy.nodes().filter((n) => n.id().toLowerCase().includes(kata)).addClass("cocok");
  if ($("#only-bad").checked) {
    const bermasalah = cy.nodes().filter((n) => n.data("masalah") || n.data("cycle"));
    const tetap = bermasalah.union(bermasalah.incomers());
    cy.elements().not(tetap).addClass("pudar");
  }
}

/* ---------- Penghubung daftar <-> graph ---------- */
function pilih(id) {
  const node = cy.getElementById(id);
  if (node.empty()) return;

  // graph: sorot node beserta yang memanggil & yang dipanggilnya
  saringGraph();
  const sekitar = node.closedNeighborhood();
  cy.elements().not(sekitar).addClass("pudar");
  sekitar.removeClass("pudar");
  node.addClass("sorot");
  sekitar.edges().addClass("sorot");
  cy.animate({ fit: { eles: sekitar, padding: 60 } }, { duration: 300 });

  // daftar: tandai baris yang sama, gulir ke baris pertama
  let pertama = null;
  for (const b of document.querySelectorAll("#list [data-id]")) {
    const aktif = b.dataset.id === id;
    b.classList.toggle("aktif", aktif);
    if (aktif && !pertama) pertama = b;
  }
  if (pertama) pertama.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function lepas() {
  saringGraph();
  for (const b of document.querySelectorAll("#list .aktif")) b.classList.remove("aktif");
}

/* ---------- Tombol & input ---------- */
const CATATAN = {
  laptop: "Memindai <code>~/.config</code> di laptop ini.",
  contoh: "Dotfiles contoh yang sengaja diberi error.",
};

for (const b of document.querySelectorAll(".seg button")) {
  b.onclick = () => {
    sumber = b.dataset.sumber;
    for (const x of document.querySelectorAll(".seg button")) x.setAttribute("aria-selected", x === b);
    $("#note").innerHTML = CATATAN[sumber];
    scan();
  };
}
$("#scan-form").onsubmit = (e) => { e.preventDefault(); scan(); };
$("#search").oninput = () => data && tampilDaftar();
$("#only-bad").onchange = () => data && tampilDaftar();
$("#btn-fit").onclick = () => { if (cy) { lepas(); cy.animate({ fit: { padding: 20 } }, { duration: 300 }); } };

// ?sumber=contoh di alamat langsung membuka tab Contoh
const awal = document.querySelector(`.seg button[data-sumber="${new URLSearchParams(location.search).get("sumber")}"]`);
if (awal) awal.click();
else scan();
