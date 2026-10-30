# dotdeps

**Sistem Validasi dan Pencarian Dependensi Dotfiles Berbasis Graph Search dan Pattern Matching**

NIM: 240605110182

CLI Python murni (tanpa library eksternal) untuk memindai konfigurasi Linux di
`~/.config` (Hyprland, Waybar, kitty, dll) — di laptop sendiri **atau dari repo
dotfiles orang lain di GitHub** — memetakan ketergantungan antar file dan
program sebagai graph, lalu mendeteksi:

| Masalah | Contoh |
|---|---|
| **Broken link** | `source = ~/.config/hypr/theme.conf` padahal file tidak ada |
| **Missing package** | `exec-once = waybar` padahal `waybar` tidak terinstall |
| **Include melingkar** | `hyprland.conf → keybinds.conf → hyprland.conf` |
| **Nilai dinamis** | `exec_cmd(command)`: tidak bisa dicek statis, dilaporkan sebagai info |
| **Di luar repo** | khusus scan GitHub: file yang memang tidak ikut di-upload, bukan error |

## Kebutuhan

- Python **3.9+** (`graphlib` baru ada sejak 3.9)
- Library bawaan saja: `re`, `pathlib`, `shutil`, `graphlib`, `shlex`, `argparse`, `json`,
  `http.server`, `urllib.request`, `tarfile`, `tempfile`
- Frontend web (opsional): Cytoscape.js + dagre, sudah disertakan di `web/vendor/`

## Struktur

```
dotdeps/
├── main.py        # CLI (argparse): scan, tree, search, order
├── models.py      # dataclass Dependency, Issue, enum DepKind/Status
├── parser.py      # pattern matching (regex) per format config
├── graph.py       # pembangunan graph (BFS), DFS rantai, cycle (graphlib)
├── validator.py   # cek file ada (pathlib) & binary ada di $PATH (shutil.which)
├── report.py      # tampilan terminal berwarna, tree, JSON
├── scanner.py     # alur scan bersama untuk CLI dan web
├── github.py      # unduh repo GitHub (urllib + tarfile) & deteksi struktur folder
├── server.py      # web server lokal (http.server) + API JSON
├── web/           # antarmuka web: index.html, app.js, style.css
│   └── vendor/    # Cytoscape.js 3.30.2, dagre 0.8.5, cytoscape-dagre 2.5.0
├── samples/.config/   # dotfiles contoh dengan error yang disengaja
└── tests/test_dotdeps.py
```

## Cara menjalankan

```bash
cd ~/Projects/dotdeps

# 1. Demo pakai dotfiles contoh (aman, tidak menyentuh config asli)
python3 main.py --home samples scan --tree

# 2. Scan config asli (default: hypr, waybar, kitty, foot, mako, ...)
python3 main.py scan
python3 main.py scan -a hypr -v        # hanya Hyprland, tampilkan semua detail
python3 main.py scan --all             # seluruh ~/.config

# 3. Pencarian dependensi: siapa yang memanggil program/file ini?
python3 main.py search kitty
python3 main.py search monitors.conf

# 4. Tree dependensi & urutan resolusi (topological sort)
python3 main.py tree -a hypr --depth 2
python3 main.py order

# 5. Cek dotfiles orang lain langsung dari GitHub
python3 main.py scan --github JaKooLit/Hyprland-Dots
python3 main.py scan --github https://github.com/user/dotfiles/tree/dev --all

# 6. Output JSON (untuk diproses tool lain / CI)
python3 main.py scan --json > hasil.json

# 7. Antarmuka web interaktif (graph + tombol scan)
python3 main.py serve                  # buka http://localhost:8765
python3 main.py serve --port 9000 --no-browser

# Unit test
python3 -m unittest discover -s tests -v
```

## Scan dari GitHub (`--github`)

1. **Unduh**: arsip `.tar.gz` repo diambil dengan `urllib` (tanpa `git`, tanpa
   token) dan diekstrak secara *streaming* dengan `tarfile`. Hanya file teks
   kecil yang diambil; wallpaper, font, dll. dilewati. Filter `data` milik
   `tarfile` menolak path absolut, `..`, dan symlink yang keluar folder.
2. **Deteksi struktur**: setiap orang menyusun repo berbeda (`config/hypr`,
   `Configs/.config/hypr`, `dotfiles/.config/hypr`, atau GNU Stow
   `hypr/.config/hypr`). Folder yang paling banyak berisi subfolder app umum
   (hypr, waybar, kitty, …) dianggap sebagai `~/.config`.
3. **Home virtual**: folder sementara berisi symlink `.config/<app>` ke folder
   di repo, sehingga `source = ~/.config/hypr/x.conf` dan
   `os.getenv("HOME")` mengarah ke file di dalam repo. Folder dihapus setelah scan.
4. **Validasi** sama seperti scan lokal, dengan arti yang sedikit berbeda:
   *missing package* = program yang **belum terinstall di laptopmu** untuk
   memakai dotfiles itu. File yang memang tidak ikut di-upload (hasil generate
   tema, script di `$PATH` pemiliknya) dilaporkan terpisah sebagai
   *di luar repo*, bukan error.

Isi repo hanya dibaca sebagai teks; tidak ada script yang dijalankan.

## Antarmuka web (`serve`)

Browser tidak bisa membaca `~/.config` secara langsung, jadi `serve` menyalakan
web server lokal (`http.server`, bawaan Python). Halaman web memanggil
`/api/scan`, lalu Python menjalankan parser → graph → validator yang sama
dengan CLI dan mengirim hasilnya sebagai JSON.

Tampilannya dibuat sederhana (*progressive disclosure*): pertanyaan utama
"ada masalah atau tidak?" dijawab dulu, detail baru muncul saat diminta.

1. **Input**: pilih sumber *Laptop ini* / *GitHub* / *Contoh*, lalu **Scan**.
   Pilihan app, `--all`, dan perintah CLI setara ada di *Opsi lanjutan*.
2. **Ringkasan**: satu kalimat, mis. "8 masalah ditemukan — 8 program belum
   terinstall di laptopmu".
3. **Daftar masalah** (tab utama): dikelompokkan per jenis, tiap baris punya
   tombol *Lihat di graph*. Kategori yang bukan error (di luar repo, nilai
   dinamis) dilipat.
4. **Graph** (tab kedua): dirender dengan [Cytoscape.js](https://js.cytoscape.org)
   + layout dagre. Merah = masalah, ungu = include melingkar, kuning putus-putus
   = di luar repo. Klik node → panel detail berisi lokasi baris pemanggil,
   jalur dari titik masuk, dan dependensinya. Ada pencarian, filter, pilihan
   layout, dan **ekspor PNG** untuk laporan.

Library JavaScript disimpan lokal di `web/vendor/`, jadi halaman tetap jalan
tanpa internet (internet hanya dibutuhkan untuk scan GitHub). Server hanya
listen di `127.0.0.1`, menolak header `Host` asing, dan seluruh operasinya
read-only.

Exit code: `0` = semua dependensi terpenuhi, `1` = ada masalah, `2` = error input.
Jadi bisa dipakai di script, misal `python3 main.py scan && echo aman`.

## Cara kerja

### 1. Pattern Matching (`parser.py`)

Setiap format punya regex sendiri. Format dipilih dari ekstensi/folder file
(`detect_format`).

| Format | Pola yang dikenali | Jenis |
|---|---|---|
| Hyprland `.conf` | `source = path`, `exec-once = cmd`, `exec = cmd`, `bind = MOD, KEY, exec, cmd`, `bindd = MOD, KEY, deskripsi, exec, cmd`, `$var = nilai` | file, binary |
| Hyprland `.lua` | `require("hypr.x")`, `dofile(...)`, `exec_cmd("...")`, `exec_once = { ... }`, `o.bind(k, d, "cmd")`, `local x = "..."` | file, binary |
| Waybar `.jsonc` | `"include": [...]`, `"exec"`, `"exec-if"`, `"on-click*"`, `"on-scroll*"` | file, binary |
| CSS | `@import "file.css"` | file |
| Conf generik | `include = path` / `include path` (kitty, foot, mako) | file |

Detail yang ditangani:
- Komentar (`#`, `--`, `--[[ ]]`, `//`, `/* */`) dibuang tanpa merusak isi string,
  dan nomor baris tetap akurat.
- Ekspansi `~`, `$HOME`, variabel Hyprland (`$terminal`), glob (`conf.d/*.conf`),
  dan ekspresi Lua sederhana seperti `(os.getenv("X") or "default") .. "/file.lua"`.
- Variabel Hyprland yang dipakai sebelum didefinisikan (mis. `$term` di
  `keybinds.conf`, didefinisikan di file yang di-source belakangan) dicoba lagi
  setelah semua file terbaca.
- Nama binary diambil dari perintah shell (`extract_binaries`) dengan `shlex`:
  pembungkus seperti `uwsm app --`, `env VAR=1`, `sh -c "..."`, operator
  `&&`/`|`/`;`, redirect (`> /dev/null 2>&1`), rule `[workspace 2]`, dan
  command substitution `$(slurp)`. Aksi bawaan modul Waybar
  (`"on-click": "activate"`) tidak dianggap program.

### 2. Graph Search (`graph.py`)

- **Node**: `file:/path/config` atau `bin:nama-program`.
  **Edge** `A → B`: A bergantung pada B.
- **BFS crawler** (`DependencyGraph.build`): mulai dari file di `~/.config`,
  setiap `source`/`include`/`require` yang menunjuk file yang ada dimasukkan ke
  antrian, sehingga file di luar folder (mis. default Omarchy di `/usr/share`)
  ikut ditelusuri. Matikan dengan `--no-follow`.
- **DFS mundur** (`paths_to`): mencari rantai root → target untuk perintah `search`.
- **Deteksi cycle** (`find_cycles`): `graphlib.TopologicalSorter` melempar
  `CycleError` berisi satu cycle; edge-nya dihapus lalu diulang sampai graph
  menjadi DAG, sehingga semua cycle terlaporkan.
- **Topological sort** (`load_order`): urutan file dengan dependensi lebih dulu.

### 3. Validator (`validator.py`)

- File: `Path.exists()` + `os.access(R_OK)` → broken link.
- Binary: `shutil.which()` (hasil di-cache) → missing package. Untuk path
  script, dibedakan antara "tidak ada" dan "ada tapi belum `chmod +x`".

## Contoh output (`python3 main.py --home samples scan`)

```
◆ dotdeps — Validasi Dependensi Dotfiles
────────────────────────────────────────────────────────────────
  File diparse: 9   Relasi: 29   File unik: 10   Program unik: 18

✗ BROKEN LINK (3)
  • ~/.config/hypr/theme.conf  file tidak ditemukan
      ↳ ~/.config/hypr/hyprland.conf:8  [source]
  ...
✗ MISSING PACKAGE (n)
  • browser-fiktif  program tidak ada di $PATH
      ↳ ~/.config/hypr/hyprland.conf:12  [exec-once]
  ...
⟳ INCLUDE MELINGKAR (1)
  • ~/.config/hypr/hyprland.conf → ~/.config/hypr/keybinds.conf → ~/.config/hypr/hyprland.conf
────────────────────────────────────────────────────────────────
  ✗ 13 MASALAH DITEMUKAN   17 OK
```

Jumlah *missing package* tergantung program yang terinstall di komputer
masing-masing; `browser-fiktif` dan `wlogout-fiktif` selalu muncul karena
memang sengaja dibuat tidak ada.

## Keterbatasan

- Analisis statis: perintah yang dibentuk saat runtime (`exec_cmd(command)`,
  `value.launch`) hanya dilaporkan sebagai *nilai dinamis*, tidak divalidasi.
- `which` hanya mengecek `$PATH` sesi saat ini; program yang hanya ada di
  `$PATH` khusus sesi Hyprland bisa terlapor hilang.
- Tidak memetakan binary ke nama paket (gunakan `pacman -F <bin>`).
- Scan GitHub hanya untuk repo publik. File yang di-generate saat dipakai
  (mis. `colors-matugen.conf`) tapi letaknya di dalam folder app tetap
  terlapor sebagai file tidak ditemukan, karena memang tidak ada di repo.
