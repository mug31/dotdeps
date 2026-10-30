"""Validasi graph: broken link, missing package, cycle, dan nilai dinamis."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from graph import DependencyGraph, label
from models import DepKind, Dependency, Issue, Status


@lru_cache(maxsize=None)
def find_binary(name: str) -> str | None:
    """shutil.which juga menerima path absolut dan mengecek bit executable."""
    return shutil.which(name)


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    ok: list[Dependency] = field(default_factory=list)

    def by_status(self, status: Status) -> list[Issue]:
        return [i for i in self.issues if i.status is status]

    @property
    def error_count(self) -> int:
        """Jumlah masalah unik: satu program yang dipanggil di 5 tempat
        dihitung sekali, sama seperti tampilan web."""
        unique = {
            (i.status, i.dependency.target if i.dependency else i.cycle)
            for i in self.issues
            if i.status in {Status.BROKEN_LINK, Status.MISSING_PACKAGE, Status.CYCLE}
        }
        return len(unique)


def in_scope(path: Path, scope: Path) -> bool:
    """True jika path berada di folder app yang memang ada di repo
    (scope = ~/.config virtual hasil checkout GitHub)."""
    try:
        rel = path.relative_to(scope)
    except ValueError:
        return False
    return bool(rel.parts) and (scope / rel.parts[0]).is_dir()


@dataclass
class Scope:
    """Konteks scan repo GitHub. `config_dir` = ~/.config virtual,
    `scripts` = nama semua file di repo (untuk script yang dipanggil via $PATH)."""
    config_dir: Path
    scripts: set[str] = field(default_factory=set)

    @classmethod
    def for_repo(cls, config_dir: Path) -> "Scope":
        names: set[str] = set()
        for _, _, files in os.walk(config_dir.parent, followlinks=True):
            names.update(files)
        return cls(config_dir, names)

    @property
    def home(self) -> Path:
        return self.config_dir.parent


def check_dependency(dep: Dependency, scope: Scope | None = None) -> Issue | None:
    """`scope` diisi saat memindai repo GitHub: sesuatu yang memang tidak
    ikut di repo dilaporkan sebagai OUTSIDE, bukan error pemilik repo."""
    if dep.unresolved:
        return Issue(Status.UNRESOLVED, f"nilai dinamis tidak bisa dicek: {dep.target}", dep)

    if dep.kind is DepKind.FILE:
        path = Path(dep.target)
        if not path.exists():
            if scope and not in_scope(path, scope.config_dir):
                return Issue(Status.OUTSIDE, f"di luar repo (tidak ikut di-upload): {dep.target}", dep)
            return Issue(Status.BROKEN_LINK, f"file tidak ditemukan: {dep.target}", dep)
        if not os.access(path, os.R_OK):
            return Issue(Status.BROKEN_LINK, f"file tidak bisa dibaca: {dep.target}", dep)
        return None

    if find_binary(dep.target) is None:
        path = Path(dep.target)
        if "/" in dep.target and path.exists():
            return Issue(Status.MISSING_PACKAGE, f"script ada tapi tidak executable (chmod +x): {dep.target}", dep)
        if scope and "/" in dep.target and path.is_relative_to(scope.home) and not in_scope(path, scope.config_dir):
            return Issue(Status.OUTSIDE, f"script di luar repo: {dep.target}", dep)
        if scope and "/" not in dep.target and dep.target in scope.scripts:
            return Issue(Status.OUTSIDE, f"script dari repo, dipanggil lewat $PATH pemiliknya: {dep.target}", dep)
        return Issue(Status.MISSING_PACKAGE, f"program tidak ada di $PATH: {dep.target}", dep)
    return None


def validate(graph: DependencyGraph, scope: Scope | None = None) -> ValidationResult:
    result = ValidationResult()
    for dep in graph.dependencies:
        issue = check_dependency(dep, scope)
        if issue:
            result.issues.append(issue)
        else:
            result.ok.append(dep)

    for cycle in graph.find_cycles():
        # graphlib memberi urutan predecessor; balik agar terbaca "A -> B -> A"
        chain = tuple(label(n) for n in reversed(cycle))
        result.issues.append(Issue(Status.CYCLE, "include melingkar: " + " -> ".join(chain), cycle=chain))
    return result
