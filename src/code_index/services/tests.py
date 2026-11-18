"""Testes: leitura dos casos de teste dos clones (sem embedding), find_tests e a contagem nos resultados.

Os testes continuam fora do índice de trechos e dos embeddings. O sync relê os testes dos repos cuja revisão
mudou (`scan_tests`, sem custo de API) e liga cada arquivo de teste ao código de produção (domain/test_cases).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from ..domain.test_cases import EXTRACTOR_VERSION, group_declarations, link_file
from ..indexing.orchestrator import configured_repos, repo_for_row
from ..indexing.test_scan import scan_tests
from ..infra.test_store import TestRow, TestStore
from ..retrieval.redaction import mask_output
from .base import ServiceBase

log = logging.getLogger(__name__)

MAX_TARGET_CHARS = 300
_PATHLIKE = re.compile(r"/|\.[A-Za-z]{1,5}$")
_MEMBER = re.compile(r"^(?P<cls>[A-Za-z_][\w.]*?)(?:\.|::|#)(?P<member>[A-Za-z_]\w*)$")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _scan_key(revision: str | None) -> str:
    return f"{revision or ''}#tests{EXTRACTOR_VERSION}"


class TestsMixin(ServiceBase):
    __test__ = False

    @property
    def _tests(self) -> TestStore:
        return TestStore(self._chunks.pool, self.settings)

    async def scan_tests(self, repos: Sequence[str] | None = None, *, force: bool = False) -> dict[str, int]:
        """Lê os casos de teste dos repos de código indexados cuja revisão mudou desde a última leitura
        (ou todos, com `force`). Sem custo de API. Apaga os de repos que saíram. Uma leitura por vez entre
        processos e pods (ScanBusy, um RuntimeError, se outra já roda)."""
        async with self._scan_lock("tests"):
            return await self._scan_tests(repos, force=force)

    async def _scan_tests(self, repos: Sequence[str] | None, *, force: bool) -> dict[str, int]:
        store = self._tests
        wanted = await self._resolve_repos(repos)
        rows = await self._repos.all_rows()
        orgs = await self._config.orgs()
        manual = await configured_repos(self._config)
        done = await store.scanned()
        alive: list[str] = []
        scanned = cases = 0
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
            read, files = await asyncio.to_thread(scan_tests, root, tuple(repo.include), tuple(repo.all_excludes))
            declarations = group_declarations(await store.declarations(row.name))
            production = [p for p in await store.production_paths(row.name)]
            out: list[TestRow] = []
            for f in files:
                linked = link_file(f.path, f.cases, declarations, production)
                for case, called in linked.cases:
                    out.append(TestRow(
                        path=f.path, line=case.line, name=mask_output(case.name, f.language),
                        context=mask_output(" > ".join(case.context), f.language), language=f.language,
                        subject=linked.subject, target_path=linked.target_path, link=linked.how,
                        target_symbols=tuple(called)))
            await store.replace(row.name, key, read, out)
            scanned += 1
            cases += len(out)
        pruned = await store.prune(alive) if wanted is None else 0
        if scanned:
            log.info("testes: %d repo(s) lidos, %d casos", scanned, cases)
        return {"repos": scanned, "cases": cases, "pruned": pruned}

    async def find_tests(self, target: str, *, repos: Sequence[str] | None = None,
                         limit: int = 50) -> dict[str, Any]:
        """Casos de teste que cobrem um arquivo (caminho), uma classe/módulo, um membro (`Classe.metodo` ou só
        `metodo`) ou, como último recurso, cujo nome contém o texto. Devolve os nomes e linhas, não o corpo."""
        target = target.strip()
        if not target or len(target) > MAX_TARGET_CHARS:
            raise ValueError(f"alvo vazio ou maior que {MAX_TARGET_CHARS} caracteres")
        limit = max(1, min(limit, 200))
        names = await self._resolve_repos(repos)
        store = self._tests
        attempts: list[tuple[str, dict[str, Any]]] = []
        member = _MEMBER.match(target)
        if " " not in target and _PATHLIKE.search(target) and not member:
            attempts.append(("arquivo", {"target_path": target}))
        elif member:
            cls, sym = member.group("cls").split(".")[-1], member.group("member")
            attempts += [("membro", {"subject": cls, "symbol": sym}), ("classe", {"subject": cls})]
        elif _IDENT.match(target):
            if target[0].isupper():
                attempts += [("classe", {"subject": target}), ("membro", {"symbol": target})]
            else:
                attempts += [("membro", {"symbol": target}), ("classe", {"subject": target})]
        attempts.append(("nome do teste", {"name": target}))
        for how, query in attempts:
            found, total = await store.find(repos=names, limit=limit, **query)
            if found:
                return {"matched_by": how, "total": total, "truncated": total > len(found),
                        "tests": [_public(t) for t in found]}
        return {"matched_by": None, "total": 0, "truncated": False, "tests": []}

    async def tests_status(self) -> dict[str, Any]:
        return await self._tests.status()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    name = f"{row['context']} > {row['name']}" if row["context"] else row["name"]
    out: dict[str, Any] = {"repo": row["repo"], "path": row["path"], "line": row["line"], "name": name}
    if row["target_path"]:
        out["tests_file"] = row["target_path"]
    if row["target_symbols"]:
        out["calls"] = row["target_symbols"]
    return out
