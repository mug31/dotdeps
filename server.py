"""Antarmuka web lokal: `python3 main.py serve`.

Server memakai http.server bawaan Python dan hanya melayani:
  GET /                -> web/index.html (+ file statis di web/)
  GET /api/options     -> daftar app yang tersedia & default
  GET /api/scan?...    -> hasil scan dalam format node/edge untuk Cytoscape.js
                          (source=real|samples|custom|github, repo=user/dotfiles)

Semua operasi bersifat read-only; tidak ada file config yang diubah.
"""

from __future__ import annotations

import json
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from graph import label, node_id
from models import DepKind, Status
from scanner import DEFAULT_APPS, SAMPLES_HOME, ScanError, ScanOutcome, available_apps, run_scan, scan_github
from validator import find_binary

WEB_DIR = Path(__file__).resolve().parent / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

STATUS_KEY = {
    Status.BROKEN_LINK: "broken",
    Status.MISSING_PACKAGE: "missing",
    Status.OUTSIDE: "outside",
}


def resolve_home(source: str, custom: str) -> Path:
    if source == "samples":
        return SAMPLES_HOME
    if source == "custom" and custom.strip():
        return Path(custom.strip()).expanduser()
    return Path.home()


def short(path: str, home: Path) -> str:
    h = str(home)
    return "~" + path[len(h):] if path == h or path.startswith(h + "/") else path


def graph_payload(scan: ScanOutcome) -> dict:
    """Ubah DependencyGraph + ValidationResult menjadi elemen Cytoscape."""
    graph, result, home = scan.graph, scan.result, scan.home

    # Status tiap node dari issue validator.
    node_status: dict[str, str] = {}
    for issue in result.issues:
        if issue.dependency and issue.status in STATUS_KEY:
            node_status[node_id(issue.dependency)] = STATUS_KEY[issue.status]

    cycle_edges: set[tuple[str, str]] = set()
    cycle_nodes: set[str] = set()
    for cycle in graph.find_cycles():
        # graphlib: cycle[i] adalah predecessor cycle[i+1] -> edge cycle[i+1] -> cycle[i]
        for a, b in zip(cycle, cycle[1:]):
            cycle_edges.add((b, a))
        cycle_nodes.update(cycle)

    refs: dict[str, list[dict]] = {}
    edge_info: dict[tuple[str, str], set[str]] = {}
    for dep in graph.dependencies:
        if dep.unresolved:
            continue
        nid = node_id(dep)
        refs.setdefault(nid, []).append({
            "source": short(str(dep.source), home), "line": dep.line,
            "directive": dep.directive, "raw": dep.raw,
        })
        edge_info.setdefault((f"file:{dep.source}", nid), set()).add(dep.directive)

    roots = set(graph.top_level_roots())
    parsed = {f"file:{p}" for p in graph.parsed_files}
    nodes = []
    for nid in sorted(set(graph.adj) | set(graph.radj)):
        kind = "bin" if nid.startswith("bin:") else "file"
        path = label(nid)
        status = node_status.get(nid, "ok")
        external = kind == "file" and not Path(path).is_relative_to(scan.config_dir)
        nodes.append({"data": {
            "id": nid,
            "kind": kind,
            "label": Path(path).name if kind == "file" or "/" in path else path,
            "path": short(path, home),
            "status": status,
            "cycle": nid in cycle_nodes,
            "external": external,
            "root": nid in roots,
            "parsed": nid in parsed,
            "resolved": find_binary(path) if kind == "bin" else None,
            "refs": refs.get(nid, []),
        }})

    edges = [
        {"data": {
            "id": f"{src}->{dst}", "source": src, "target": dst,
            "directive": ", ".join(sorted(directives)),
            "cycle": (src, dst) in cycle_edges,
        }}
        for (src, dst), directives in sorted(edge_info.items())
    ]

    unresolved = [
        {"target": d.target, "source": short(str(d.source), home), "line": d.line, "directive": d.directive}
        for d in graph.dependencies if d.unresolved
    ]
    files = sum(n["data"]["kind"] == "file" for n in nodes)
    return {
        "meta": {
            "home": str(home), "config_dir": str(scan.config_dir), "apps": scan.apps,
            "parsed_files": len(graph.parsed_files), "relations": len(graph.dependencies),
            "repo": scan.repo and {
                "label": scan.repo.label, "url": scan.repo.url,
                "config_root": scan.repo.config_root, "apps": scan.repo.apps,
                "skipped_files": scan.repo.skipped_files,
            },
        },
        "stats": {
            "files": files,
            "programs": len(nodes) - files,
            "broken": len({n for n, s in node_status.items() if s == "broken"}),
            "missing": len({n for n, s in node_status.items() if s == "missing"}),
            "outside": len({n for n, s in node_status.items() if s == "outside"}),
            "cycles": len(result.by_status(Status.CYCLE)),
            "unresolved": len(unresolved),
            "ok": len(result.ok),
        },
        "cycles": [[short(x, home) for x in i.cycle] for i in result.by_status(Status.CYCLE)],
        "unresolved": unresolved,
        "elements": {"nodes": nodes, "edges": edges},
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):  # log ringkas
        print(f"  {self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}")

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Tolak Host asing (mencegah DNS rebinding dari situs lain).
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            self.send_error(HTTPStatus.FORBIDDEN, "host tidak diizinkan")
            return

        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/options":
            home = resolve_home(q.get("source", "real"), q.get("custom", ""))
            config_dir = home / ".config"
            return self.send_json({
                "home": str(home),
                "config_dir": str(config_dir),
                "exists": config_dir.is_dir(),
                "samples_home": str(SAMPLES_HOME),
                "apps": available_apps(config_dir),
                "default_apps": DEFAULT_APPS,
            })
        if url.path == "/api/scan":
            apps = [a for a in q.get("apps", "").split(",") if a] or None
            scan_all = q.get("all") == "1"
            find_binary.cache_clear()  # scan ulang selalu cek $PATH terbaru
            try:
                if q.get("source") == "github":
                    # Folder sementara dihapus setelah payload selesai dibuat.
                    with scan_github(q.get("repo", ""), apps=apps, scan_all=scan_all) as scan:
                        payload = graph_payload(scan)
                else:
                    home = resolve_home(q.get("source", "real"), q.get("custom", ""))
                    payload = graph_payload(run_scan(home, apps=apps, scan_all=scan_all,
                                                     follow_external=q.get("follow", "1") == "1", warn=False))
            except ScanError as err:
                return self.send_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)
            return self.send_json(payload)
        if url.path.startswith("/api/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        return super().do_GET()


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        ALLOWED_HOSTS.add(host)
    httpd = ThreadingHTTPServer((host, port), partial(Handler))
    url = f"http://{'localhost' if host in {'127.0.0.1', '::1'} else host}:{port}/"
    print(f"◆ dotdeps web berjalan di {url}  (Ctrl+C untuk berhenti)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserver dihentikan.")
    finally:
        httpd.server_close()
    return 0
