"""Pembangunan dan penelusuran graph dependensi.

Node graph berupa string:
  "file:/abs/path/hyprland.conf"
  "bin:kitty"
Edge (A -> B) berarti "A bergantung pada B".
"""

from __future__ import annotations

from collections import defaultdict, deque
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from models import DepKind, Dependency
from parser import ParseContext, detect_format, parse_file, resolve_late


def node_id(dep: Dependency) -> str:
    prefix = "file" if dep.kind is DepKind.FILE else "bin"
    return f"{prefix}:{dep.target}"


def file_node(path: Path | str) -> str:
    return f"file:{path}"


def label(node: str) -> str:
    return node.split(":", 1)[1]


class DependencyGraph:
    def __init__(self) -> None:
        self.adj: dict[str, set[str]] = defaultdict(set)      # forward edges
        self.radj: dict[str, set[str]] = defaultdict(set)     # reverse edges
        self.dependencies: list[Dependency] = []
        self.roots: list[str] = []
        self.parsed_files: list[Path] = []

    # -- konstruksi ---------------------------------------------------------

    def add(self, dep: Dependency) -> None:
        self.dependencies.append(dep)
        if dep.unresolved:
            return
        src, dst = file_node(dep.source), node_id(dep)
        self.adj[src].add(dst)
        self.radj[dst].add(src)

    @classmethod
    def build(cls, entry_files: list[Path], ctx: ParseContext, follow_external: bool = True) -> "DependencyGraph":
        """Bangun graph dengan BFS: mulai dari file di ~/.config, lalu ikuti
        setiap `source`/`include`/`require` ke file berikutnya."""
        graph = cls()
        graph.roots = [file_node(p) for p in entry_files]
        queue: deque[Path] = deque(entry_files)
        visited: set[Path] = set()

        def follow(dep: Dependency) -> None:
            if dep.kind is not DepKind.FILE or dep.unresolved:
                return
            child = Path(dep.target)
            inside = child.is_relative_to(ctx.config_dir)
            if child.is_file() and detect_format(child) and (follow_external or inside):
                queue.append(child)

        while queue:
            while queue:
                path = queue.popleft()
                if path in visited:
                    continue
                visited.add(path)
                graph.parsed_files.append(path)
                graph.adj.setdefault(file_node(path), set())
                for dep in parse_file(path, ctx):
                    graph.add(dep)
                    follow(dep)

            # Tahap akhir: variabel yang baru dikenal setelah semua file dibaca.
            pending, graph.dependencies = graph.dependencies, []
            for dep in pending:
                for new in resolve_late(dep, ctx) or [dep]:
                    graph.add(new)
                    if new is not dep:
                        follow(new)
        return graph

    # -- penelusuran --------------------------------------------------------

    def bfs(self, start: str, reverse: bool = False) -> dict[str, int]:
        """Breadth-first search; return {node: kedalaman}."""
        edges = self.radj if reverse else self.adj
        depth = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for nxt in sorted(edges.get(node, ())):
                if nxt not in depth:
                    depth[nxt] = depth[node] + 1
                    queue.append(nxt)
        return depth

    def paths_to(self, target: str, limit: int = 20) -> list[list[str]]:
        """Cari rantai dependensi dari root ke `target` (DFS mundur).

        Menjawab pertanyaan: "file mana saja yang membuat X dibutuhkan?"
        """
        results: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            if len(results) >= limit:
                return
            # Abaikan parent yang sudah ada di rantai (mencegah loop pada cycle).
            parents = sorted(p for p in self.radj.get(node, ()) if p not in path)
            if not parents:
                results.append(list(reversed(path)))
                return
            for parent in parents:
                dfs(parent, path + [parent])

        dfs(target, [target])
        return results

    def search(self, query: str) -> list[str]:
        """Cari node yang labelnya mengandung `query` (case-insensitive)."""
        q = query.lower()
        nodes = set(self.adj) | set(self.radj)
        exact = [n for n in nodes if Path(label(n)).name.lower() == q or label(n).lower() == q]
        return sorted(exact) or sorted(n for n in nodes if q in label(n).lower())

    def top_level_roots(self) -> list[str]:
        """Titik masuk sebenarnya: file yang tidak di-include file lain.

        File yang hanya terjangkau lewat cycle (A -> B -> A) tidak punya root
        murni, jadi kita tambahkan root sampai semua entry file tercakup,
        dengan prioritas nama file utama seperti hyprland.conf.
        """
        main_names = ("hyprland.conf", "hyprland.lua", "config.jsonc", "config", "init.lua")

        def priority(node: str) -> tuple[bool, bool, str]:
            return (bool(self.radj.get(node)), Path(label(node)).name not in main_names, node)

        roots: list[str] = []
        covered: set[str] = set()
        for node in sorted(self.roots, key=priority):
            if node not in covered:
                roots.append(node)
                covered |= set(self.bfs(node))
        return roots

    # -- analisis -----------------------------------------------------------

    def file_subgraph(self) -> dict[str, set[str]]:
        return {
            n: {d for d in deps if d.startswith("file:")}
            for n, deps in self.adj.items() if n.startswith("file:")
        }

    def find_cycles(self) -> list[tuple[str, ...]]:
        """Deteksi include melingkar memakai graphlib.TopologicalSorter.

        CycleError hanya melaporkan satu cycle, jadi kita hapus satu edge
        dari cycle tersebut lalu ulangi sampai graph menjadi DAG.
        """
        graph = {n: set(d) for n, d in self.file_subgraph().items()}
        cycles: list[tuple[str, ...]] = []
        while True:
            try:
                tuple(TopologicalSorter(graph).static_order())
                return cycles
            except CycleError as err:
                cycle = tuple(err.args[1])  # [a, b, ..., a]
                cycles.append(cycle)
                # graphlib: node -> predecessors, jadi edge cycle[i+1] -> cycle[i]
                graph[cycle[1]].discard(cycle[0])

    def load_order(self) -> list[str]:
        """Urutan resolusi file: dependensi selalu muncul sebelum pemakainya."""
        graph = {n: set(d) for n, d in self.file_subgraph().items()}
        for cycle in self.find_cycles():
            graph[cycle[1]].discard(cycle[0])
        return list(TopologicalSorter(graph).static_order())
