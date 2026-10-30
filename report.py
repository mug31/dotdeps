"""Tampilan laporan di terminal (ANSI color, tree view, JSON)."""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from graph import DependencyGraph, label
from models import DepKind, Issue, Status
from validator import ValidationResult, check_dependency


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str: return self._wrap("1", t)
    def dim(self, t: str) -> str: return self._wrap("2", t)
    def red(self, t: str) -> str: return self._wrap("31", t)
    def green(self, t: str) -> str: return self._wrap("32", t)
    def yellow(self, t: str) -> str: return self._wrap("33", t)
    def cyan(self, t: str) -> str: return self._wrap("36", t)
    def magenta(self, t: str) -> str: return self._wrap("35", t)


def use_color(force_off: bool) -> bool:
    return not force_off and sys.stdout.isatty() and "NO_COLOR" not in os.environ


class Reporter:
    def __init__(self, home: Path, color: bool = True, out=sys.stdout):
        self.home = str(home)
        self.s = Style(color)
        self.out = out

    def p(self, text: str = "") -> None:
        print(text, file=self.out)

    def short(self, path: str) -> str:
        if path == self.home or path.startswith(self.home + "/"):
            return "~" + path[len(self.home):]
        return path

    def node_text(self, node: str) -> str:
        return self.short(label(node))

    # -- laporan scan -------------------------------------------------------

    def header(self, title: str, subtitle: str) -> None:
        self.p(self.s.bold(self.s.cyan(f"◆ {title}")))
        self.p(self.s.dim(subtitle))
        self.p(self.s.dim("─" * 64))

    def stats(self, graph: DependencyGraph, result: ValidationResult) -> None:
        deps = graph.dependencies
        files = {d.target for d in deps if d.kind is DepKind.FILE and not d.unresolved}
        bins = {d.target for d in deps if d.kind is DepKind.BINARY and not d.unresolved}
        self.p(
            f"  File diparse: {self.s.bold(str(len(graph.parsed_files)))}   "
            f"Relasi: {self.s.bold(str(len(deps)))}   "
            f"File unik: {self.s.bold(str(len(files)))}   "
            f"Program unik: {self.s.bold(str(len(bins)))}"
        )
        self.p()

    def _grouped(self, issues: list[Issue], title: str, icon: str, paint, verbose_limit: int) -> None:
        if not issues:
            return
        groups: dict[str, list[Issue]] = defaultdict(list)
        for issue in issues:
            groups[issue.dependency.target].append(issue)
        self.p(paint(self.s.bold(f"{icon} {title} ({len(groups)})")))
        for target in sorted(groups):
            items = groups[target]
            self.p(f"  {paint('•')} {self.s.bold(self.short(target))}  {self.s.dim(items[0].message.split(':')[0])}")
            for issue in items[:verbose_limit]:
                d = issue.dependency
                self.p(self.s.dim(f"      ↳ {self.short(str(d.source))}:{d.line}  [{d.directive}]"))
            if len(items) > verbose_limit:
                self.p(self.s.dim(f"      ↳ … +{len(items) - verbose_limit} lokasi lain"))
        self.p()

    def scan_report(self, graph: DependencyGraph, result: ValidationResult, verbose: bool) -> None:
        self.stats(graph, result)
        limit = 50 if verbose else 3
        self._grouped(result.by_status(Status.BROKEN_LINK), "BROKEN LINK", "✗", self.s.red, limit)
        self._grouped(result.by_status(Status.MISSING_PACKAGE), "MISSING PACKAGE", "✗", self.s.red, limit)

        cycles = result.by_status(Status.CYCLE)
        if cycles:
            self.p(self.s.magenta(self.s.bold(f"⟳ INCLUDE MELINGKAR ({len(cycles)})")))
            for c in cycles:
                self.p("  " + self.s.magenta("•") + " " + " → ".join(self.short(x) for x in c.cycle))
            self.p()

        self._grouped(result.by_status(Status.OUTSIDE), "DI LUAR REPO (bukan error)", "○", self.s.yellow, limit)

        unresolved = result.by_status(Status.UNRESOLVED)
        if verbose:
            self._grouped(unresolved, "NILAI DINAMIS (tidak dicek)", "?", self.s.yellow, limit)

        self.summary(result, len(unresolved), verbose)

    def summary(self, result: ValidationResult, unresolved: int, verbose: bool) -> None:
        self.p(self.s.dim("─" * 64))
        errors = result.error_count
        status = self.s.green(self.s.bold("✓ SEMUA DEPENDENSI TERPENUHI")) if errors == 0 \
            else self.s.red(self.s.bold(f"✗ {errors} MASALAH DITEMUKAN"))
        extra = f"   {self.s.green(str(len(result.ok)))} OK"
        if unresolved:
            hint = "" if verbose else " (lihat dengan -v)"
            extra += f"   {self.s.yellow(str(unresolved))} dinamis{hint}"
        self.p(f"  {status}{extra}")
        if result.by_status(Status.MISSING_PACKAGE):
            self.p(self.s.dim("  Tip: cari paket penyedia binary dengan `pacman -F <nama>` / `apt-file search <nama>`"))

    # -- tree view ----------------------------------------------------------

    def tree(self, graph: DependencyGraph, roots: list[str], max_depth: int) -> None:
        shown: set[str] = set()

        def mark(node: str) -> str:
            if node.startswith("bin:"):
                from validator import find_binary
                ok = find_binary(label(node)) is not None
                return self.s.green("✓") if ok else self.s.red("✗")
            return self.s.green("✓") if Path(label(node)).exists() else self.s.red("✗")

        def walk(node: str, prefix: str, ancestors: tuple[str, ...], depth: int) -> None:
            children = sorted(graph.adj.get(node, ()), key=lambda n: (n.startswith("bin:"), n))
            for i, child in enumerate(children):
                last = i == len(children) - 1
                branch = "└── " if last else "├── "
                icon = "⚙ " if child.startswith("bin:") else "📄"
                text = self.node_text(child)
                suffix = ""
                if child in ancestors:
                    suffix = self.s.magenta("  ⟳ cycle")
                elif child in shown and graph.adj.get(child):
                    suffix = self.s.dim("  (sudah ditampilkan)")
                self.p(f"{prefix}{branch}{mark(child)} {icon} {text}{suffix}")
                if suffix or depth >= max_depth:
                    continue
                if child.startswith("file:"):
                    shown.add(child)
                    walk(child, prefix + ("    " if last else "│   "), ancestors + (child,), depth + 1)

        for root in roots:
            shown.add(root)
            self.p(f"{mark(root)} 📄 {self.s.bold(self.node_text(root))}")
            walk(root, "", (root,), 1)
            self.p()

    # -- search -------------------------------------------------------------

    def search(self, graph: DependencyGraph, query: str) -> int:
        matches = graph.search(query)
        if not matches:
            self.p(self.s.yellow(f"Tidak ada file/program yang cocok dengan '{query}'."))
            return 1
        for node in matches[:10]:
            kind = "Program" if node.startswith("bin:") else "File"
            deps = [d for d in graph.dependencies if not d.unresolved and
                    f"{'bin' if d.kind is DepKind.BINARY else 'file'}:{d.target}" == node]
            problem = next((check_dependency(d) for d in deps if check_dependency(d)), None)
            status = self.s.red(f"✗ {problem.message}") if problem else self.s.green("✓ tersedia")
            self.p(self.s.bold(self.s.cyan(f"◆ {kind}: {self.node_text(node)}")) + f"   {status}")

            self.p(self.s.bold("  Dipanggil di:"))
            for d in deps[:15]:
                self.p(f"    • {self.short(str(d.source))}:{d.line}  {self.s.dim('[' + d.directive + ']')}  {self.s.dim(d.raw[:60])}")

            chains = graph.paths_to(node)
            if chains and any(len(c) > 2 for c in chains):
                self.p(self.s.bold("  Rantai dependensi (root → target):"))
                for chain in chains[:10]:
                    self.p("    " + self.s.dim(" → ").join(self.node_text(n) for n in chain))

            children = sorted(graph.adj.get(node, ()))
            if children:
                self.p(self.s.bold(f"  Bergantung pada ({len(children)}):"))
                for child in children[:30]:
                    self.p(f"    • {self.node_text(child)}")
            self.p()
        if len(matches) > 10:
            self.p(self.s.dim(f"… {len(matches) - 10} hasil lain, perjelas kata kunci."))
        return 0

    # -- load order ---------------------------------------------------------

    def order(self, graph: DependencyGraph) -> None:
        self.p(self.s.bold("Urutan resolusi (dependensi lebih dulu):"))
        for i, node in enumerate(graph.load_order(), start=1):
            self.p(f"  {i:3d}. {self.node_text(node)}")


def to_json(graph: DependencyGraph, result: ValidationResult) -> str:
    def dep_dict(d):
        return {"kind": d.kind.value, "target": d.target, "source": str(d.source),
                "line": d.line, "directive": d.directive, "raw": d.raw}

    data = {
        "parsed_files": [str(p) for p in graph.parsed_files],
        "summary": {
            "dependencies": len(graph.dependencies),
            "ok": len(result.ok),
            "errors": result.error_count,
            **{s.value: len(result.by_status(s)) for s in Status if s is not Status.OK},
        },
        "issues": [
            {"status": i.status.value, "message": i.message,
             **({"dependency": dep_dict(i.dependency)} if i.dependency else {}),
             **({"cycle": list(i.cycle)} if i.cycle else {})}
            for i in result.issues
        ],
        "edges": {k: sorted(v) for k, v in graph.adj.items() if v},
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
