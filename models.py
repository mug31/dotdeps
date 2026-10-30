"""Struktur data bersama untuk parser, graph, dan validator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DepKind(str, Enum):
    FILE = "file"       # file config lain (source, include, require, @import)
    BINARY = "binary"   # program yang dieksekusi (exec-once, exec, on-click)


class Status(str, Enum):
    OK = "ok"
    BROKEN_LINK = "broken_link"          # file yang di-include tidak ada
    MISSING_PACKAGE = "missing_package"  # binary tidak ditemukan di $PATH
    UNRESOLVED = "unresolved"            # nilai dinamis, tidak bisa dicek statis
    CYCLE = "cycle"                      # include melingkar (A -> B -> A)
    OUTSIDE = "outside"                  # file di luar repo GitHub (bukan error)


@dataclass(frozen=True)
class Dependency:
    """Satu relasi ketergantungan hasil pattern matching.

    `target` berisi path absolut (untuk FILE) atau nama/path binary (untuk
    BINARY). Jika `unresolved` bernilai True, target masih berupa ekspresi
    mentah yang tidak bisa dievaluasi secara statis.
    """

    kind: DepKind
    target: str
    source: Path
    line: int
    directive: str
    raw: str
    unresolved: bool = False

    @property
    def location(self) -> str:
        return f"{self.source}:{self.line}"


@dataclass(frozen=True)
class Issue:
    status: Status
    message: str
    dependency: Dependency | None = None
    cycle: tuple[str, ...] = ()
