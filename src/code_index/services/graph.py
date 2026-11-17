"""Grafo de chamadas: leitura dos clones com tree-sitter (sem embedding) e as consultas de quem chama quem.

O sync relê os repos de código cuja revisão mudou (`scan_calls`, sem custo de API) e grava as arestas em
`code_edges`. A extração está em `indexing/call_scan.py` e a resolução, pura, em `domain/code_graph.py`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from ..domain.code_graph import EXTRACTOR_VERSION, INFERRED_VIAS
from ..indexing.call_scan import scan_calls
from ..indexing.orchestrator import configured_repos, repo_for_row
from ..infra.graph_store import GraphStore
from .base import ServiceBase

log = logging.getLogger(__name__)

MAX_TARGET_CHARS = 300
NO_EDGES_NOTE = ("nenhuma chamada ligada. O grafo é por repo (chamadas entre repos não entram), cobre Java, "
                 "TS/JS, Python e Go e só liga o que dá para provar: chamada por reflexão, injeção sem tipo "
                 "declarado, evento ou framework fica de fora. Confirme com grep_code.")
_PATH_LINE = re.compile(r"^(?P<path>[^:\s]+\.[A-Za-z]{1,5}):(?P<line>\d+)$")
_PATHLIKE = re.compile(r"/|\.[A-Za-z]{1,5}$")


def _scan_key(revision: str | None) -> str:
    return f"{revision or ''}#graph{EXTRACTOR_VERSION}"


class GraphMixin(ServiceBase):
    @property
    def _graph(self) -> GraphStore:
        return GraphStore(self._chunks.pool, self.settings)

    async def scan_calls(self, repos: Sequence[str] | None = None, *, force: bool = False) -> dict[str, int]:
        """Lê as chamadas dos repos de código indexados cuja revisão mudou desde a última leitura (ou todos, com
        `force`). Sem custo de API. Apaga as arestas de repos que saíram."""
        store = self._graph
        wanted = await self._resolve_repos(repos)
        rows = await self._repos.all_rows()
        orgs = await self._config.orgs()
        manual = await configured_repos(self._config)
        done = await store.scanned()
        alive: list[str] = []
        scanned = edges = 0
        for row in sorted(rows.values(), key=lambda r: r.name):
            if row.kind != "code" or row.ignored or not row.indexed:
                continue
            repo = repo_for_row(row, orgs, manual)
            root = repo.local_path(self.settings.repos_dir)
            if not root.is_dir():
                continue
            alive.append(row.name)
            key = _scan_key(row.revision)
            if (wanted is not None and row.name not in wanted) or (not force and done.get(row.name) == key):
                continue
            result = await asyncio.to_thread(
                scan_calls, root, tuple(repo.include), tuple(repo.all_excludes), self.settings.max_file_bytes)
            await store.replace(row.name, key, result)
            scanned += 1
            edges += len(result.edges)
        pruned = await store.prune(alive) if wanted is None else 0
        if scanned:
            log.info("grafo de chamadas: %d repo(s) lidos, %d arestas", scanned, edges)
        return {"repos": scanned, "edges": edges, "pruned": pruned}

    async def graph_status(self) -> dict[str, Any]:
        return await self._graph.status()

    async def call_graph(self, target: str, *, direction: str = "callers", repos: Sequence[str] | None = None,
                         limit: int = 50) -> dict[str, Any]:
        """Quem chama (`callers`) ou o que é chamado por (`callees`) um símbolo: `Classe.metodo`, `metodo`,
        `caminho/arquivo.java` ou `caminho:linha` (linha da definição)."""
        target = target.strip()
        if not target or len(target) > MAX_TARGET_CHARS:
            raise ValueError(f"alvo vazio ou maior que {MAX_TARGET_CHARS} caracteres")
        limit = max(1, min(limit, 200))
        names = await self._resolve_repos(repos)
        query: dict[str, Any] = {}
        m = _PATH_LINE.match(target)
        if m:
            query = {"path": m.group("path"), "line": int(m.group("line"))}
        elif " " not in target and _PATHLIKE.search(target) and "/" in target:
            query = {"path": target}
        else:
            query = {"name": target}
        found, total = await self._graph.neighbors(direction, repos=names, limit=limit, **query)
        out: dict[str, Any] = {"direction": direction, "total": total, "truncated": total > len(found),
                               "edges": [_public(e, direction) for e in found]}
        if not found:
            out["note"] = NO_EDGES_NOTE
        return out


def _public(e: dict[str, Any], direction: str) -> dict[str, Any]:
    """Do ponto de vista de quem pergunta: em `callers`, quem chama; em `callees`, quem é chamado."""
    if direction == "callers":
        other = {"path": e["src_path"], "line": e["src_line"] or e["line"], "name": e["src_name"] or "(arquivo)"}
        this = {"path": e["dst_path"], "line": e["dst_line"], "name": e["dst_name"]}
    else:
        other = {"path": e["dst_path"], "line": e["dst_line"], "name": e["dst_name"]}
        this = {"path": e["src_path"], "line": e["src_line"], "name": e["src_name"] or "(arquivo)"}
    return {"repo": e["repo"], **other, "call_line": e["line"], "calls": e["calls"], "via": e["via"],
            "inferred": e["via"] in INFERRED_VIAS, "of": this}
