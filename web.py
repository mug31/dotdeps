# Versi web dotdeps: python3 web.py lalu buka http://localhost:8000
#
# Python di sini hanya dua tugas:
#   1. /api/scan?sumber=laptop|contoh -> jalankan dotdeps.py, kirim hasilnya sebagai JSON
#   2. alamat lain                    -> kirim file tampilan dari folder web/
# Algoritmanya (regex, BFS, validasi, DFS) tetap di dotdeps.py.

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import dotdeps

FOLDER = os.path.dirname(os.path.abspath(__file__))
FOLDER_WEB = os.path.join(FOLDER, "web")
SUMBER = {
    "laptop": os.path.expanduser("~"),        # config asli
    "contoh": os.path.join(FOLDER, "contoh"),  # demo, sengaja ada error
}


def hasil_scan(sumber):
    home = SUMBER[sumber]
    graph = dotdeps.scan(home)
    masalah = {target: jenis for jenis, target, file in dotdeps.validasi(graph)}

    def pendek(path):
        return path.replace(home, "~")

    files = []
    for file, deps in graph.items():
        files.append({
            "file": pendek(file),
            "deps": [{"jenis": jenis, "target": pendek(target), "masalah": masalah.get(target, "")}
                     for jenis, target in deps],
        })
    return {
        "home": home,
        "files": files,
        "cycles": [[pendek(f) for f in c] for c in dotdeps.cari_cycle(graph)],
    }


class Handler(BaseHTTPRequestHandler):
    def kirim(self, kode, isi, tipe):
        self.send_response(kode)
        self.send_header("Content-Type", tipe)
        self.end_headers()
        self.wfile.write(isi)

    def do_GET(self):
        url = urlparse(self.path)

        if url.path == "/api/scan":
            sumber = parse_qs(url.query).get("sumber", ["laptop"])[0]
            if sumber not in SUMBER:
                sumber = "laptop"
            isi = json.dumps(hasil_scan(sumber)).encode()
            return self.kirim(200, isi, "application/json")

        nama = "index.html" if url.path == "/" else url.path.lstrip("/")
        path = os.path.normpath(os.path.join(FOLDER_WEB, nama))
        if not path.startswith(FOLDER_WEB + os.sep) or not os.path.isfile(path):
            return self.kirim(404, b"tidak ditemukan", "text/plain")
        tipe = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            self.kirim(200, f.read(), tipe + ("; charset=utf-8" if tipe.startswith("text") else ""))

    def log_message(self, *args):
        pass  # jangan penuhi terminal dengan log tiap request


print("Buka http://localhost:8000  (Ctrl+C untuk berhenti)")
HTTPServer(("localhost", 8000), Handler).serve_forever()
