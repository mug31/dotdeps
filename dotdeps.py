# dotdeps (versi sederhana)
# Validasi & pencarian dependensi dotfiles berbasis graph search dan pattern matching.
#
# Cara pakai:
#   python3 dotdeps.py                       -> scan ~/.config milik sendiri
#   python3 dotdeps.py --home contoh         -> scan dotfiles contoh
#   python3 dotdeps.py --home contoh cari kitty
#   python3 web.py                           -> versi web, buka http://localhost:8000

import os
import re
import shutil
import sys

# ---------- 1. Pattern matching (regex) ----------
POLA_SOURCE = re.compile(r"^\s*(source|include)\s*=?\s*(\S+)")   # source = a.conf / include a.conf
POLA_EXEC = re.compile(r"^\s*exec(-once)?\s*=\s*(.+)")           # exec-once = waybar
POLA_BIND = re.compile(r"^\s*bind\w*\s*=.*?,\s*exec\s*,\s*(.+)")  # bind = SUPER, Q, exec, kitty
POLA_VAR = re.compile(r"^\s*\$(\w+)\s*=\s*(.+)")                  # $terminal = kitty
POLA_REQUIRE = re.compile(r"require\s*\(?\s*[\"']([\w.]+)[\"']")  # require("hypr.bindings")
POLA_LUA_EXEC = re.compile(r"exec\w*\s*\(\s*[\"']([^\"']+)")      # hl.exec_cmd("waybar")

# folder tempat modul Lua dicari: ~/.config dan folder bawaan Omarchy
FOLDER_LUA = [os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")]


def cari_modul_lua(modul, home):
    """require("hypr.bindings") -> ~/.config/hypr/bindings.lua"""
    nama = modul.replace(".", "/") + ".lua"
    for folder in [home + "/.config"] + FOLDER_LUA:
        if os.path.exists(os.path.join(folder, nama)):
            return os.path.join(folder, nama)
    return os.path.join(home, ".config", nama)  # tidak ketemu -> nanti jadi broken link


def baca_dependensi(file, home, variabel):
    """Baca satu file config, kembalikan daftar (jenis, target)."""
    hasil = []
    lua = file.endswith(".lua")
    for baris in open(file, encoding="utf-8", errors="ignore"):
        baris = baris.split("--" if lua else "#")[0]  # buang komentar

        if lua:
            for modul in POLA_REQUIRE.findall(baris):
                hasil.append(("file", cari_modul_lua(modul, home)))
            for perintah in POLA_LUA_EXEC.findall(baris):
                hasil.append(("program", perintah.split()[0]))
            continue

        for nama, nilai in variabel.items():   # ganti $var dengan nilainya
            baris = baris.replace("$" + nama, nilai)

        cocok = POLA_VAR.match(baris)
        if cocok:
            variabel[cocok.group(1)] = cocok.group(2).strip()
            continue

        cocok = POLA_SOURCE.match(baris)
        if cocok:
            path = cocok.group(2)
            if path.startswith("~"):
                path = home + path[1:]
            elif not path.startswith("/"):
                path = os.path.join(os.path.dirname(file), path)
            hasil.append(("file", os.path.normpath(path)))
            continue

        cocok = POLA_EXEC.match(baris) or POLA_BIND.match(baris)
        if cocok:
            perintah = re.sub(r"^\[.*?\]\s*", "", cocok.groups()[-1])  # buang [workspace 2]
            if perintah.split():
                hasil.append(("program", perintah.split()[0]))
    return hasil


# ---------- 2. Graph search: BFS untuk membangun graph ----------
def bangun_graph(file_awal, home):
    graph = {}
    variabel = {}
    antrian = list(file_awal)
    while antrian:
        file = antrian.pop(0)                  # FIFO -> BFS
        if file in graph or not os.path.exists(file):
            continue
        graph[file] = baca_dependensi(file, home, variabel)
        for jenis, target in graph[file]:
            if jenis == "file":
                antrian.append(target)
    return graph


def scan(home):
    config = home + "/.config"
    file_awal = [config + "/hypr/hyprland.conf", config + "/hypr/hyprland.lua", config + "/kitty/kitty.conf"]
    return bangun_graph(file_awal, home)


# ---------- 3. Validasi ----------
def validasi(graph):
    """Kembalikan daftar (masalah, target, file yang memanggil)."""
    masalah = []
    for file, deps in graph.items():
        for jenis, target in deps:
            if jenis == "file" and not os.path.exists(target):
                masalah.append(("BROKEN LINK", target, file))
            if jenis == "program" and shutil.which(target) is None:
                masalah.append(("TIDAK ADA", target, file))
    return masalah


# ---------- 4. Graph search: DFS untuk mencari include melingkar ----------
def cari_cycle(graph):
    hasil = []

    def dfs(file, jalur):
        for jenis, target in graph.get(file, []):
            if jenis != "file":
                continue
            if target in jalur:                # balik ke file yang sedang dilewati
                hasil.append(jalur[jalur.index(target):] + [target])
            elif target in graph:
                dfs(target, jalur + [target])

    for file in graph:
        dfs(file, [file])
    unik = {tuple(sorted(c[:-1])): c for c in hasil}  # cycle sama, titik awal beda
    return list(unik.values())


# ---------- 5. Pencarian: siapa yang memanggil X? ----------
def cari(graph, kata):
    return [(file, target) for file, deps in graph.items()
            for jenis, target in deps if kata in target]


# ---------- Program utama ----------
if __name__ == "__main__":
    args = sys.argv[1:]
    home = os.path.expanduser("~")
    if args[:1] == ["--home"]:
        home = os.path.abspath(args[1])
        args = args[2:]

    def pendek(path):
        return path.replace(home, "~")

    graph = scan(home)
    print(f"{len(graph)} file config dipindai\n")

    if args[:1] == ["cari"] and len(args) > 1:
        for file, target in cari(graph, args[1]):
            print(f"{pendek(file)}  ->  {pendek(target)}")
    else:
        for masalah, target, file in validasi(graph):
            print(f"{masalah:<13} {pendek(target)}  (dipanggil di {pendek(file)})")
        for c in cari_cycle(graph):
            print("MELINGKAR     " + " -> ".join(os.path.basename(f) for f in c))
