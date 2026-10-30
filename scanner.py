"""Alur scan bersama untuk CLI (main.py) dan antarmuka web (server.py)."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from github import GitHubError, RepoCheckout, checkout
from graph import DependencyGraph
from parser import ParseContext, discover_files
from validator import Scope, ValidationResult, validate

DEFAULT_APPS = ["hypr", "waybar", "kitty", "foot", "alacritty", "mako", "swaync", "wofi", "walker"]
SAMPLES_HOME = Path(__file__).resolve().parent / "samples"


class ScanError(Exception):
    pass


@dataclass
class ScanOutcome:
    home: Path
    config_dir: Path
    apps: list[str]
    graph: DependencyGraph
    result: ValidationResult
    repo: "RepoCheckout | None" = None


def available_apps(config_dir: Path) -> list[str]:
    """Subfolder ~/.config yang berisi minimal satu file config yang didukung."""
    if not config_dir.is_dir():
        return []
    return sorted(
        (d.name for d in config_dir.iterdir()
         if d.is_dir() and not d.name.startswith(".") and discover_files(d)),
        key=str.lower,
    )


def collect_entry_files(config_dir: Path, apps: list[str] | None, scan_all: bool,
                        warn: bool = True) -> tuple[list[Path], list[str]]:
    if scan_all:
        return discover_files(config_dir), ["(semua)"]
    found_apps, files = [], []
    for app in apps or DEFAULT_APPS:
        app_dir = config_dir / app
        if app_dir.is_dir():
            found_apps.append(app)
            files += discover_files(app_dir)
        elif apps and warn:
            print(f"peringatan: {app_dir} tidak ada, dilewati", file=sys.stderr)
    return files, found_apps


def run_scan(home: Path, config_dir: Path | None = None, apps: list[str] | None = None,
             scan_all: bool = False, follow_external: bool = True, warn: bool = True,
             repo: "RepoCheckout | None" = None) -> ScanOutcome:
    home = home.expanduser().resolve()
    config_dir = (config_dir or home / ".config").expanduser().resolve()
    if not config_dir.is_dir():
        raise ScanError(f"direktori config tidak ditemukan: {config_dir}")

    entry_files, found_apps = collect_entry_files(config_dir, apps, scan_all, warn)
    if not entry_files:
        if repo and not scan_all:
            raise ScanError(f"app {', '.join(apps or DEFAULT_APPS)} tidak ada di repo; "
                            f"yang tersedia: {', '.join(repo.apps)} (coba --all)")
        raise ScanError(f"tidak ada file config yang didukung di {config_dir}")

    ctx = ParseContext(home=home, config_dir=config_dir)
    graph = DependencyGraph.build(entry_files, ctx, follow_external=follow_external)
    # Untuk repo GitHub, file di luar repo bukan kesalahan pemilik repo.
    result = validate(graph, scope=Scope.for_repo(config_dir) if repo else None)
    return ScanOutcome(home, config_dir, found_apps, graph, result, repo)


@contextmanager
def scan_github(spec: str, apps: list[str] | None = None, scan_all: bool = False) -> Iterator[ScanOutcome]:
    """Unduh repo, scan, lalu hapus folder sementara saat keluar dari `with`.

    Include ke luar repo tidak diikuti, supaya file di laptop sendiri
    (mis. /usr/share/...) tidak tercampur dengan dotfiles orang lain.
    """
    try:
        with checkout(spec) as repo:
            yield run_scan(repo.home, apps=apps, scan_all=scan_all,
                           follow_external=False, warn=False, repo=repo)
    except GitHubError as err:
        raise ScanError(str(err)) from None
