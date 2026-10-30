#!/usr/bin/env python3
"""dotdeps — Sistem Validasi dan Pencarian Dependensi Dotfiles
Berbasis Graph Search dan Pattern Matching.

Contoh:
  python3 main.py scan                     # scan ~/.config (hypr, waybar, ...)
  python3 main.py scan --app hypr --tree   # hanya Hyprland + tampilkan tree
  python3 main.py search kitty             # siapa yang memanggil kitty?
  python3 main.py --home samples scan      # demo memakai dotfiles contoh
  python3 main.py scan --github user/repo  # cek dotfiles orang lain di GitHub
  python3 main.py serve                    # antarmuka web interaktif
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from graph import file_node
from report import Reporter, to_json, use_color
from scanner import ScanError, ScanOutcome, run_scan, scan_github


def common_options(suppress: bool) -> argparse.ArgumentParser:
    """Opsi umum, boleh ditulis sebelum atau sesudah nama perintah.

    Versi untuk subcommand memakai default SUPPRESS supaya tidak menimpa
    nilai yang sudah diberikan sebelum nama perintah.
    """
    def default(value):
        return argparse.SUPPRESS if suppress else value

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", type=Path, default=default(Path.home()),
                        help="direktori home untuk ekspansi '~' (default: $HOME)")
    common.add_argument("-g", "--github", metavar="REPO", default=default(None),
                        help="scan dotfiles dari repo GitHub publik, mis. user/dotfiles atau URL")
    common.add_argument("--config-dir", type=Path, default=default(None),
                        help="direktori config yang discan (default: <home>/.config)")
    common.add_argument("-a", "--app", action="append", metavar="NAMA", default=default(None),
                        help="subfolder ~/.config yang discan, bisa diulang (default: hypr, waybar, ...)")
    common.add_argument("--all", action="store_true", default=default(False), help="scan seluruh isi ~/.config")
    common.add_argument("--no-follow", action="store_true", default=default(False),
                        help="jangan ikuti include ke file di luar config-dir (mis. /usr/share)")
    common.add_argument("--no-color", action="store_true", default=default(False), help="matikan warna ANSI")
    return common


def build_parser() -> argparse.ArgumentParser:
    top, common = common_options(False), common_options(True)

    p = argparse.ArgumentParser(prog="dotdeps", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Jalankan `python3 main.py <perintah> -h` untuk detail.",
                                parents=[top])
    sub = p.add_subparsers(dest="command", metavar="<perintah>")

    scan = sub.add_parser("scan", parents=[common], help="validasi semua dependensi (default)")
    scan.add_argument("-v", "--verbose", action="store_true", help="tampilkan semua lokasi & nilai dinamis")
    scan.add_argument("-t", "--tree", action="store_true", help="tampilkan tree dependensi")
    scan.add_argument("--depth", type=int, default=4, help="kedalaman maksimum tree (default 4)")
    scan.add_argument("--json", action="store_true", help="output JSON (untuk diproses tool lain)")

    tree = sub.add_parser("tree", parents=[common], help="tampilkan tree dependensi dari file root")
    tree.add_argument("root", nargs="?", help="file root (default: semua titik masuk)")
    tree.add_argument("--depth", type=int, default=6)

    search = sub.add_parser("search", parents=[common], help="cari file/program dan rantai dependensinya")
    search.add_argument("query", help="nama program atau file, mis. 'kitty' atau 'monitors.conf'")

    sub.add_parser("order", parents=[common], help="urutan resolusi file (topological sort)")

    serve = sub.add_parser("serve", help="buka antarmuka web interaktif (graph + tombol scan)")
    serve.add_argument("--host", default="127.0.0.1", help="alamat server (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="port server (default 8765)")
    serve.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "scan"
    if command == "serve":
        from server import serve
        return serve(args.host, args.port, open_browser=not args.no_browser)

    try:
        if args.github:
            print(f"Mengunduh {args.github} dari GitHub…", file=sys.stderr)
            with scan_github(args.github, args.app, args.all) as scan:
                return run_command(args, command, scan)
        scan = run_scan(args.home, args.config_dir, args.app, args.all,
                        follow_external=not args.no_follow)
    except ScanError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return run_command(args, command, scan)


def run_command(args, command: str, scan: ScanOutcome) -> int:
    home, config_dir, apps, graph, result = scan.home, scan.config_dir, scan.apps, scan.graph, scan.result
    rep = Reporter(home, color=use_color(args.no_color))

    if command == "search":
        return rep.search(graph, args.query)

    if command == "order":
        rep.order(graph)
        return 0

    if command == "tree":
        if args.root:
            path = Path(args.root).expanduser()
            roots = [file_node(path.resolve() if path.exists() else config_dir / args.root)]
        else:
            roots = graph.top_level_roots()
        rep.tree(graph, roots, args.depth)
        return 0

    if getattr(args, "json", False):
        print(to_json(graph, result))
        return 1 if result.error_count else 0

    source = (f"Repo: {scan.repo.url}  ({scan.repo.label}, folder {scan.repo.config_root})"
              if scan.repo else f"Scan: {config_dir}")
    rep.header("dotdeps — Validasi Dependensi Dotfiles", f"{source}   App: {', '.join(apps)}")
    if getattr(args, "tree", False):
        rep.tree(graph, graph.top_level_roots(), args.depth)
    rep.scan_report(graph, result, verbose=getattr(args, "verbose", False))
    return 1 if result.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
