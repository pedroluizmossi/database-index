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
        """Onde um símbolo é declarado. Ordem: nome exato, depois prefixo, depois "contém".

        `Classe.metodo` ou `Classe::metodo` filtram também pela classe que envolve o método.
        """
        raw = name.strip()
        if not raw:
            return []
        if kind is not None and kind not in _KINDS:
            raise ValueError(f"kind inválido: {kind} (use {', '.join(_KINDS)})")
        parts = [p for p in re.split(r"::|\.|#", raw) if p]
        target = parts[-1].lower() if parts else raw.lower()
        container = parts[-2].lower() if len(parts) > 1 else None

        args: list[object] = [target, _like_escape(target) + "%", "%" + _like_escape(target) + "%"]
        clauses = ["lower(name) LIKE $3"]
        if container:
            args.append(container)
            clauses.append(f"lower(container) = ${len(args)}")
        if kind:
            args.append(kind)
            clauses.append(f"kind = ${len(args)}")
        if repos:
            args.append(list(repos))
            clauses.append(f"repo = ANY(${len(args)})")
        if language:
            args.append(language)
            clauses.append(f"language = ${len(args)}")
        args.append(max(1, min(limit, 200)))
        sql = f"""
            SELECT repo, path, line, name, kind, container, signature, language,
                   CASE WHEN lower(name) = $1 OR lower(name) LIKE '% ' || $1 THEN 0
                        WHEN lower(name) LIKE $2 THEN 1 ELSE 2 END AS rank
            FROM {self._table}
            WHERE {" AND ".join(clauses)}
            ORDER BY rank, (kind <> 'container'), repo, path, line
            LIMIT ${len(args)}"""
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
