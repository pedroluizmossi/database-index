"""Consulta à tabela de símbolos (gerenciada pelo CocoIndex, preenchida na indexação)."""

from __future__ import annotations

import re
from collections.abc import Sequence

import asyncpg

from ..domain.models import SymbolHit
from ..settings import Settings

_KINDS = ("container", "member", "section")


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


class SymbolStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._table = settings.symbols_table_qualified

    async def find(
        self,
        name: str,
        *,
        kind: str | None = None,
        repos: Sequence[str] | None = None,
        language: str | None = None,
        limit: int = 20,
    ) -> list[SymbolHit]:
        """Onde um símbolo é declarado. Ordem: nome exato, depois prefixo, depois "contém";
        dentro de cada grupo, classes primeiro e nomes mais curtos (mais próximos do pedido) antes.

        `Classe.metodo` ou `Classe::metodo` filtram também pela classe que envolve o método; se nada
        casar com a classe, procura só pelo método. `Classe.*` lista os membros da classe.
        """
        raw = name.strip()
        if not raw:
            return []
        if kind is not None and kind not in _KINDS:
            raise ValueError(f"kind inválido: {kind} (use {', '.join(_KINDS)})")
        parts = [p for p in re.split(r"::|\.|#", raw) if p]
        target = parts[-1].lower() if parts else raw.lower()
        container = parts[-2].lower() if len(parts) > 1 else None
        language = language.strip().lower() if language else None
        limit = max(1, min(limit, 200))
        if target == "*":
            if not container:
                raise ValueError("use Classe.* para listar os membros de uma classe")
            return await self._fetch(None, container, "member", repos, language, limit)
        hits = await self._fetch(target, container, kind, repos, language, limit)
        if not hits and container:
            hits = await self._fetch(target, None, kind, repos, language, limit)
        return hits

    async def _fetch(
        self,
        target: str | None,
        container: str | None,
        kind: str | None,
        repos: Sequence[str] | None,
        language: str | None,
        limit: int,
    ) -> list[SymbolHit]:
        """target None: todos os nomes (listagem de membros, na ordem do arquivo)."""
        args: list[object] = []
        clauses: list[str] = []

        def arg(value: object) -> str:
            args.append(value)
            return f"${len(args)}"

        if target is not None:
            exact, prefix = arg(target), arg(_like_escape(target) + "%")
            clauses.append(f"lower(name) LIKE {arg('%' + _like_escape(target) + '%')}")
            rank = (f"CASE WHEN lower(name) = {exact} OR lower(name) LIKE '% ' || {exact} THEN 0 "
                    f"WHEN lower(name) LIKE {prefix} THEN 1 ELSE 2 END")
            order = "rank, (kind <> 'container'), length(name), repo, path, line"
        else:
            rank, order = "0", "repo, path, line"
        if container:
            clauses.append(f"lower(container) = {arg(container)}")
        if kind:
            clauses.append(f"kind = {arg(kind)}")
        if repos:
            clauses.append(f"repo = ANY({arg(list(repos))})")
        if language:
            clauses.append(f"language = {arg(language)}")
        sql = f"""
            SELECT repo, path, line, name, kind, container, signature, language, {rank} AS rank
            FROM {self._table}
            WHERE {" AND ".join(clauses) or "TRUE"}
            ORDER BY {order}
            LIMIT {arg(limit)}"""
        try:
            rows = await self._pool.fetch(sql, *args)
        except asyncpg.UndefinedTableError:  # ainda não houve sync com a tabela de símbolos
            return []
        return [
            SymbolHit(
                repo=r["repo"], path=r["path"], line=r["line"], name=r["name"], kind=r["kind"],
                container=r["container"], signature=r["signature"], language=r["language"],
            )
            for r in rows
        ]

    async def declarations(self, pairs: Sequence[tuple[str, str]]) -> dict[tuple[str, str], list[tuple[int, str, str]]]:
        """Declarações (linha, tipo, nome) de cada arquivo, em ordem de linha. Arquivo sem nada → ausente."""
        keys = list(dict.fromkeys(pairs))
        if not keys:
            return {}
        try:
            rows = await self._pool.fetch(
                f"""SELECT s.repo, s.path, s.line, s.kind, s.name FROM {self._table} s
                    JOIN unnest($1::text[], $2::text[]) AS k(repo, path) USING (repo, path)
                    ORDER BY s.repo, s.path, s.line""",
                [k[0] for k in keys], [k[1] for k in keys],
            )
        except asyncpg.UndefinedTableError:
            return {}
        out: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
        for r in rows:
            out.setdefault((r["repo"], r["path"]), []).append((r["line"], r["kind"], r["name"]))
        return out

    async def repo_overview(self, repo: str) -> list[tuple[str, str, int, str]]:
        """Declarações principais de um repo: (path, nome, nº de membros do arquivo, kind).
        Classes/tipos (container) e funções de nível superior (member sem container, comuns em Go/JS).
        Usado no pacote do cartão do repo. Sem tabela de símbolos → []."""
        try:
            rows = await self._pool.fetch(
                f"""SELECT path, name, kind,
                           (SELECT count(*) FROM {self._table} m
                            WHERE m.repo = s.repo AND m.path = s.path AND m.kind = 'member') AS members
                    FROM {self._table} s
                    WHERE s.repo = $1 AND (s.kind = 'container' OR (s.kind = 'member' AND s.container = ''))
                    ORDER BY s.path, s.line""",
                repo,
            )
        except asyncpg.UndefinedTableError:
            return []
        return [(r["path"], r["name"], int(r["members"]), r["kind"]) for r in rows]

    async def classes(self, repo: str) -> list[tuple[str, str, int]]:
        """(nome, path, linha) das classes/tipos de um repo. Sem tabela de símbolos → []."""
        try:
            rows = await self._pool.fetch(
                f"SELECT name, path, line FROM {self._table} WHERE repo = $1 AND kind = 'container'", repo,
            )
        except asyncpg.UndefinedTableError:
            return []
        return [(r["name"], r["path"], int(r["line"])) for r in rows]
