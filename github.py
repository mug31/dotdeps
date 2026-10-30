"""Ambil dotfiles dari repo GitHub publik untuk discan.

Alur:
  1. Unduh arsip .tar.gz repo (urllib, tanpa git & tanpa token) secara
     streaming; hanya file teks kecil yang diekstrak, wallpaper dll. dilewati.
  2. Deteksi folder mana yang berperan sebagai ~/.config, karena tiap orang
     menyusun repo berbeda: `.config/hypr`, `config/hypr`, `dotfiles/.config/hypr`,
     atau gaya GNU Stow `hypr/.config/hypr`.
  3. Bangun "home virtual" berisi symlink `.config/<app>` ke folder di repo,
     sehingga `source = ~/.config/hypr/x.conf` mengarah ke file di repo.

Isi repo hanya dibaca sebagai teks; tidak ada yang dieksekusi.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

MAX_DOWNLOAD = 200 * 1024 * 1024   # batas unduhan arsip (byte)
MAX_FILE = 1024 * 1024             # file > 1 MB dilewati (gambar, font, dll.)
MAX_DEPTH = 4                      # kedalaman pencarian folder config
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".mp4",
    ".webm", ".mkv", ".mp3", ".ogg", ".wav", ".ttf", ".otf", ".woff", ".woff2",
    ".zip", ".gz", ".xz", ".zst", ".pdf", ".psd", ".blend",
}
KNOWN_APPS = {
    "hypr", "waybar", "kitty", "foot", "alacritty", "mako", "swaync", "wofi",
    "walker", "rofi", "dunst", "eww", "ags", "fuzzel", "wlogout", "swaylock",
    "sway", "i3", "picom", "polybar", "ghostty", "wezterm", "fish", "tmux",
    "nvim", "fastfetch", "cava", "btop", "quickshell", "hyprpanel",
}
SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__"}

RE_REPO = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:github\.com/)?"
    r"(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/?#]+))?/?(?:[?#].*)?$"
)


class GitHubError(Exception):
    pass


@dataclass
class RepoCheckout:
    owner: str
    repo: str
    ref: str
    commit: str
    home: Path                      # home virtual (berisi .config/<app>)
    config_root: str                # folder di repo yang dianggap ~/.config
    apps: list[str] = field(default_factory=list)
    skipped_files: int = 0

    @property
    def label(self) -> str:
        ref = self.commit[:7] if self.commit else self.ref
        return f"{self.owner}/{self.repo}@{ref}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_repo(spec: str) -> tuple[str, str, str]:
    """'user/repo', 'github.com/user/repo', atau URL .../tree/<branch>."""
    m = RE_REPO.match(spec.strip())
    if not m:
        raise GitHubError(f"format repo tidak dikenali: '{spec}' (contoh: user/dotfiles)")
    return m["owner"], m["repo"], m["ref"] or "HEAD"


class _LimitedReader:
    """Bungkus response HTTP agar unduhan berhenti jika melebihi batas."""

    def __init__(self, raw, limit: int):
        self.raw, self.limit, self.read_bytes = raw, limit, 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.raw.read(size)
        self.read_bytes += len(chunk)
        if self.read_bytes > self.limit:
            raise GitHubError(f"repo terlalu besar (> {self.limit // 2**20} MB)")
        return chunk


def _wanted(member: tarfile.TarInfo) -> bool:
    parts = Path(member.name).parts
    if any(p in SKIP_DIRS for p in parts):
        return False
    if member.isdir() or member.issym():
        return True
    return member.isfile() and member.size <= MAX_FILE and Path(member.name).suffix.lower() not in SKIP_EXT


def download(owner: str, repo: str, ref: str, dest: Path) -> tuple[Path, str, int]:
    """Unduh & ekstrak arsip. Return (folder repo, commit sha, jumlah file dilewati)."""
    url = f"https://github.com/{owner}/{repo}/archive/{ref}.tar.gz"
    req = urllib.request.Request(url, headers={"User-Agent": "dotdeps"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise GitHubError(f"repo '{owner}/{repo}' (ref {ref}) tidak ditemukan atau privat") from None
        raise GitHubError(f"GitHub menolak permintaan: HTTP {err.code}") from None
    except urllib.error.URLError as err:
        raise GitHubError(f"tidak bisa terhubung ke GitHub: {err.reason}") from None

    # URL akhir: codeload.github.com/<owner>/<repo>/tar.gz/<commit-sha>
    commit = resp.geturl().rstrip("/").rsplit("/", 1)[-1]
    skipped = 0
    with resp, tarfile.open(fileobj=_LimitedReader(resp, MAX_DOWNLOAD), mode="r|gz") as tar:
        for member in tar:
            if not _wanted(member):
                skipped += member.isfile()
                continue
            try:
                # filter "data" menolak path absolut, "..", dan link keluar folder.
                tar.extract(member, dest, filter="data")
            except tarfile.FilterError:
                skipped += 1
    tops = [p for p in dest.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise GitHubError("format arsip GitHub tidak terduga")
    return tops[0], commit, skipped


def _walk_dirs(root: Path) -> Iterator[tuple[Path, int]]:
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        yield path, depth
        if depth >= MAX_DEPTH:
            continue
        try:
            children = sorted(p for p in path.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            continue
        stack.extend((c, depth + 1) for c in children if c.name not in SKIP_DIRS)


def detect_layout(repo_root: Path) -> tuple[Path, dict[str, Path]]:
    """Cari folder yang paling mirip ~/.config.

    Skor folder = jumlah subfolder yang namanya app dotfiles umum. Folder
    bernama `.config`/`config` diutamakan saat skor sama. Folder `.config` lain
    (gaya GNU Stow) digabungkan.
    """
    def score(path: Path) -> int:
        try:
            return sum(1 for c in path.iterdir() if c.is_dir() and c.name in KNOWN_APPS)
        except OSError:
            return 0

    best: tuple[int, int, int, Path] | None = None
    dot_configs: list[tuple[int, int, Path]] = []
    for path, depth in _walk_dirs(repo_root):
        s = score(path)
        if not s:
            continue
        if path.name == ".config":
            dot_configs.append((s, -depth, path))
        named = path.name.lower() in {".config", "config", "configs"}
        key = (s, int(named), -depth, path)
        if best is None or key[:3] > best[:3]:
            best = key
    if best is None:
        raise GitHubError("tidak menemukan folder config (hypr, waybar, kitty, …) di repo ini")

    # Folder bernama `.config` paling bisa dipercaya. Jika ada beberapa (gaya
    # GNU Stow: hypr/.config/hypr, waybar/.config/waybar), semuanya digabung.
    if dot_configs:
        dot_configs.sort(reverse=True)
        config_root = dot_configs[0][2]
        sources = [d[2] for d in dot_configs]
    else:
        config_root = best[3]
        sources = [config_root]

    apps: dict[str, Path] = {}
    for source in sources:
        for child in sorted(source.iterdir()):
            if child.is_dir() and child.name not in apps and child.name not in SKIP_DIRS:
                apps[child.name] = child
    return config_root, apps


def build_virtual_home(home: Path, config_root: Path, apps: dict[str, Path]) -> None:
    config_dir = home / ".config"
    config_dir.mkdir(parents=True)
    for name, path in apps.items():
        (config_dir / name).symlink_to(path, target_is_directory=True)
    # Jika repo meniru struktur home (ada .config), ikut tautkan .local, .zshrc, dll.
    if config_root.name == ".config":
        for sibling in config_root.parent.iterdir():
            if sibling.name.startswith(".") and sibling.name != ".config" and sibling.name not in SKIP_DIRS:
                (home / sibling.name).symlink_to(sibling, target_is_directory=sibling.is_dir())


@contextmanager
def checkout(spec: str) -> Iterator[RepoCheckout]:
    """Context manager: unduh repo ke folder sementara, hapus setelah selesai."""
    owner, repo, ref = parse_repo(spec)
    tmp = Path(tempfile.mkdtemp(prefix="dotdeps-"))
    try:
        repo_root, commit, skipped = download(owner, repo, ref, tmp / "src")
        config_root, apps = detect_layout(repo_root)
        home = tmp / "home"
        build_virtual_home(home, config_root, apps)
        rel = os.path.relpath(config_root, repo_root)
        yield RepoCheckout(owner, repo, ref, commit, home, "/" if rel == "." else rel + "/",
                           sorted(apps), skipped)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
