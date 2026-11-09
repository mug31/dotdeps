# dotdeps

**Sistem Validasi dan Pencarian Dependensi Dotfiles Berbasis Graph Search dan Pattern Matching**

NIM: 240605110182

Program Python untuk memeriksa file konfigurasi Linux (dotfiles) di `~/.config`,
khususnya Hyprland dan kitty. Program ini memetakan file config mana yang memanggil
file lain dan program apa saja yang dijalankan, menyusunnya menjadi **graph**, lalu
mendeteksi tiga jenis masalah:

| Masalah | Contoh |
|---|---|
| **Broken link** | `source = ~/.config/hypr/theme.conf` padahal filenya tidak ada |
| **Program tidak ada** | `exec-once = waybar` padahal `waybar` belum terinstall |
| **Include melingkar** | `keybinds.conf → extra.conf → keybinds.conf` |

Hasilnya bisa dilihat di terminal atau di browser. Di browser, daftar dependensi dan
graph-nya saling terhubung.

## Kebutuhan

- Python **3.6** atau lebih baru
- Tidak perlu install library apa pun. Semua yang dipakai bawaan Python:
  `re`, `os`, `shutil`, `sys`, `json`, `http.server`
- Tampilan web memakai Cytoscape.js 3.30.2 + dagre 0.8.5 + cytoscape-dagre 2.5.0,
  sudah disertakan di `web/vendor/`, jadi tetap jalan tanpa internet

## Cara menjalankan

Masuk ke folder project dulu:

```bash
cd dotdeps
```

### Versi web (disarankan)

```bash
python3 web.py
```

Lalu buka **http://localhost:8000** di browser.

- Pilih **Laptop ini** untuk memindai config asli di `~/.config`, atau **Contoh**
  untuk memindai dotfiles contoh yang sengaja diberi error.
- Klik baris di daftar (kiri) untuk menyorot node-nya di graph (kanan), atau klik
  node di graph untuk menandai barisnya di daftar.
- Kotak **Cari** dan centang **Hanya masalah** menyaring daftar dan graph sekaligus.
- Tekan `Ctrl+C` di terminal untuk mematikan server.

### Versi terminal

```bash
python3 dotdeps.py                          # scan config asli di ~/.config
python3 dotdeps.py --home contoh            # scan dotfiles contoh
python3 dotdeps.py --home contoh cari kitty # siapa saja yang memanggil "kitty"?
```

Contoh hasil:

```
6 file config dipindai

BROKEN LINK   ~/.config/hypr/theme.conf  (dipanggil di ~/.config/hypr/hyprland.conf)
TIDAK ADA     waybar  (dipanggil di ~/.config/hypr/hyprland.conf)
TIDAK ADA     browser-fiktif  (dipanggil di ~/.config/hypr/hyprland.conf)
BROKEN LINK   ~/.config/kitty/tema-hilang.conf  (dipanggil di ~/.config/kitty/kitty.conf)
TIDAK ADA     wofi  (dipanggil di ~/.config/hypr/keybinds.conf)
MELINGKAR     extra.conf -> keybinds.conf -> extra.conf
```

## Struktur

```
dotdeps/
├── dotdeps.py     # SEMUA algoritma: regex, BFS, validasi, DFS, pencarian
├── web.py         # web server: kirim hasil dotdeps.py sebagai JSON + file tampilan
├── web/           # tampilan web (HTML, CSS, JavaScript)
│   └── vendor/    # library graph Cytoscape.js + dagre
└── contoh/.config/   # dotfiles contoh dengan error yang disengaja
```

`web.py` tidak punya algoritma sendiri. Ia hanya memanggil fungsi di `dotdeps.py`
lalu mengirim hasilnya ke browser, jadi versi terminal dan versi web selalu
memberikan hasil yang sama.

## Cara kerja

```
file config ──► 1. Pattern matching ──► 2. BFS ──► graph ──► 3. Validasi ──► broken link / program tidak ada
                   (regex per baris)    (telusuri          ├─► 4. DFS ─────► include melingkar
                                         file lain)        └─► 5. Pencarian ► siapa yang memanggil X
```

### 1. Pattern matching (regex): `baca_dependensi`

Setiap baris file config dicocokkan dengan pola regex. Bagian yang ditangkap oleh
kurung `( )` diambil sebagai dependensi.

| Pola | Mencari | Contoh baris | Yang diambil |
|---|---|---|---|
| `POLA_SOURCE` | file yang di-include | `source = ~/.config/hypr/monitors.conf`<br>`include warna.conf` | path file |
| `POLA_EXEC` | program saat startup | `exec-once = waybar` | `waybar` |
| `POLA_BIND` | program dari shortcut | `bind = SUPER, Return, exec, kitty` | `kitty` |
| `POLA_VAR` | variabel Hyprland | `$terminal = kitty` | `terminal` = `kitty` |
| `POLA_REQUIRE` | modul Lua | `require("hypr.bindings")` | `hypr.bindings` |
| `POLA_LUA_EXEC` | program dari Lua | `hl.exec_cmd("waybar")` | `waybar` |

Contoh membaca `POLA_SOURCE`, yaitu `^\s*(source|include)\s*=?\s*(\S+)`:

| Potongan | Artinya |
|---|---|
| `^\s*` | awal baris, boleh ada spasi di depan |
| `(source\|include)` | kata `source` atau `include` |
| `\s*=?\s*` | tanda `=` boleh ada boleh tidak (Hyprland pakai `=`, kitty tidak) |
| `(\S+)` | path file, yaitu teks sampai ketemu spasi |

Hal-hal pendukung:
- Komentar dibuang dulu (`#` untuk `.conf`, `--` untuk `.lua`).
- Variabel seperti `$terminal` diganti nilainya sebelum dicocokkan.
- Untuk perintah program, yang diambil hanya kata pertama
  (`swww img wall.png` → `swww`).
- Nama modul Lua diubah menjadi path oleh `cari_modul_lua`:
  `hypr.bindings` → `hypr/bindings.lua`, lalu dicari di `~/.config` dan di folder
  bawaan Omarchy (`/usr/share/omarchy`).

### 2. BFS (Breadth-First Search): `bangun_graph`

Membangun graph mulai dari file utama (`hyprland.conf`, `hyprland.lua`, `kitty.conf`):

1. Masukkan file utama ke **antrian**.
2. Ambil file paling depan dari antrian (`pop(0)`, FIFO). Inilah yang membuatnya BFS.
3. Baca dependensinya dengan regex dan simpan ke graph.
4. Setiap dependensi berupa file dimasukkan ke belakang antrian.
5. Ulangi sampai antrian kosong. File yang sudah ada di graph dilewati, jadi
   include melingkar tidak membuat program berputar terus.

Graph disimpan sebagai dict biasa, contohnya:

```python
{
  "hyprland.conf": [("file", "monitors.conf"), ("file", "keybinds.conf"), ("program", "waybar")],
  "keybinds.conf": [("program", "kitty"), ("file", "extra.conf")],
  ...
}
```

### 3. Validasi: `validasi`

Setiap dependensi di graph dicek:
- **file**: `os.path.exists(path)`. Kalau tidak ada, berarti **BROKEN LINK**.
- **program**: `shutil.which(nama)`, yaitu mencari program di `$PATH`. Kalau tidak
  ketemu, berarti **TIDAK ADA**.

### 4. DFS (Depth-First Search): `cari_cycle`

Mencari include melingkar. DFS menelusuri graph sedalam mungkin sambil mencatat
**jalur** file yang sedang dilewati. Kalau suatu file memanggil file yang **sudah
ada di jalur**, berarti ada lingkaran:

```
jalur: [keybinds.conf, extra.conf]
extra.conf memanggil keybinds.conf → sudah ada di jalur → MELINGKAR
```

Lingkaran yang sama tapi titik awalnya beda (`A → B → A` dan `B → A → B`)
hanya dilaporkan sekali.

### 5. Pencarian: `cari`

Memeriksa semua sisi graph dan mengembalikan pasangan (file, target) yang target-nya
mengandung kata yang dicari. Ini untuk menjawab pertanyaan seperti "file mana saja
yang memanggil `kitty`?"

## Batasan

- Format yang didukung: config Hyprland (`.conf` dan `.lua`) serta config yang memakai
  `include` (kitty, foot, dll).
- `dofile(...)` dan daftar `exec_once = { ... }` di Lua belum dibaca.
- Pencarian modul Lua hanya di `~/.config` dan folder bawaan Omarchy.
- Untuk perintah program, hanya kata pertama yang dicek, jadi
  `uwsm app -- waybar` dicek sebagai `uwsm`.
