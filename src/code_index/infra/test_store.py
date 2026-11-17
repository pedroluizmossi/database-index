"""Casos de teste (`test_cases`, `test_scans`): gravação por repo, consultas de find_tests e contagens."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from ..settings import Settings


@dataclass(frozen=True)
class TestRow:
    path: str
    line: int
    name: str
    context: str
    language: str
    subject: str | None
    target_path: str | None
    link: str
    target_symbols: tuple[str, ...]


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


class TestStore:
    __test__ = False   # o pytest não deve coletar esta classe

    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        schema = settings.pg_schema
        self._cases = f'"{schema}".test_cases'
        self._scans = f'"{schema}".test_scans'
        self._symbols = settings.symbols_table_qualified
        self._chunks = settings.chunks_table_qualified

    async def scanned(self) -> dict[str, str]:
        """repo → chave da última leitura (revisão + versão do extrator)."""
        rows = await self._pool.fetch(f"SELECT repo, revision FROM {self._scans}")
        return {r["repo"]: r["revision"] for r in rows}

    async def declarations(self, repo: str) -> list[tuple[str, str, str, str]]:
        """(name, path, kind, container) das declarações de produção do repo (tabela de símbolos)."""
        try:
            rows = await self._pool.fetch(
                f"SELECT name, path, kind, container FROM {self._symbols} WHERE repo = $1 AND kind <> 'section'", repo)
        except asyncpg.UndefinedTableError:
            return []
        return [(r["name"], r["path"], r["kind"], r["container"]) for r in rows]

    async def production_paths(self, repo: str) -> list[str]:
        """Arquivos indexados do repo (para ligar teste a arquivo sem classe declarada, como módulos JS/Python)."""
        try:
            rows = await self._pool.fetch(f"SELECT DISTINCT path FROM {self._chunks} WHERE repo = $1", repo)
        except asyncpg.UndefinedTableError:
            return []
        return [r["path"] for r in rows]

    async def replace(self, repo: str, revision: str, files: int, rows: Sequence[TestRow]) -> None:
        """Troca os casos do repo pelos lidos agora (numa transação) e registra a leitura."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"DELETE FROM {self._cases} WHERE repo = $1", repo)
            await conn.executemany(
                f"""INSERT INTO {self._cases} (repo, path, line, name, context, language, subject, target_path, link,
                                               target_symbols)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT DO NOTHING""",
                [(repo, r.path, r.line, r.name, r.context, r.language, r.subject, r.target_path, r.link,
                  list(r.target_symbols)) for r in rows],
            )
            await conn.execute(
                f"""INSERT INTO {self._scans} (repo, revision, files, cases, linked) VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (repo) DO UPDATE SET revision = EXCLUDED.revision, files = EXCLUDED.files,
                        cases = EXCLUDED.cases, linked = EXCLUDED.linked, scanned_at = now()""",
                repo, revision, files, len(rows), sum(1 for r in rows if r.target_path),
            )

    async def prune(self, keep: Sequence[str]) -> int:
        """Apaga casos e leituras de repos que saíram (removidos, ignorados ou sem clone)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"DELETE FROM {self._cases} WHERE repo <> ALL($1::text[])", list(keep))
            res = await conn.execute(f"DELETE FROM {self._scans} WHERE repo <> ALL($1::text[])", list(keep))
        return int(res.split()[-1])

    async def find(self, *, repos: Sequence[str] | None, target_path: str | None = None,
                   subject: str | None = None, symbol: str | None = None, name: str | None = None,
                   limit: int = 50) -> tuple[list[dict[str, Any]], int]:
        """Casos que testam um arquivo (fim do caminho), uma classe (sujeito ou nome da classe do arquivo testado)
        e/ou um membro, ou cujo nome contém `name`. Devolve (casos, total). Os que chamam o membro vêm primeiro."""
        clauses, args = [], []

        def arg(value: object) -> str:
            args.append(value)
            return f"${len(args)}"

        if repos:
            clauses.append(f"repo = ANY({arg(list(repos))}::text[])")
        if target_path:
            clean = target_path.lstrip("/")
            clauses.append(f"(target_path = {arg(clean)} OR target_path LIKE {arg('%/' + _like(clean))})")
        if subject:
            s = arg(subject.lower())
            clauses.append(
                f"""(lower(subject) = {s} OR EXISTS (
                        SELECT 1 FROM {self._symbols} sy WHERE sy.repo = test_cases.repo AND sy.path = test_cases.target_path
                        AND sy.kind = 'container' AND lower(sy.name) = {s}))""")
        if symbol:
            clauses.append(f"{arg(symbol)} = ANY(target_symbols)")
        if name:
            n = arg("%" + _like(name.lower()) + "%")
            clauses.append(f"(lower(name) LIKE {n} OR lower(context) LIKE {n})")
        where = " AND ".join(clauses) or "TRUE"
        where_args = list(args)
        order = f"({arg(symbol)} = ANY(target_symbols)) DESC, " if symbol else ""
        try:
            total = await self._pool.fetchval(f"SELECT count(*) FROM {self._cases} WHERE {where}", *where_args)
            rows = await self._pool.fetch(
                f"""SELECT repo, path, line, name, context, language, target_path, link, target_symbols
                    FROM {self._cases} WHERE {where} ORDER BY {order}repo, path, line LIMIT {int(limit)}""", *args)
        except asyncpg.UndefinedTableError:
            return [], 0
        return [{"repo": r["repo"], "path": r["path"], "line": r["line"], "name": r["name"], "context": r["context"],
                 "language": r["language"], "target_path": r["target_path"], "link": r["link"],
                 "target_symbols": list(r["target_symbols"])} for r in rows], int(total)

    async def counts(self, files: Sequence[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """Casos de teste por arquivo de produção (repo, caminho)."""
        if not files:
            return {}
        try:
            rows = await self._pool.fetch(
                f"""SELECT t.repo, t.target_path, count(*) AS n FROM {self._cases} t
                    JOIN unnest($1::text[], $2::text[]) AS f(repo, path) ON f.repo = t.repo AND f.path = t.target_path
                    GROUP BY t.repo, t.target_path""",
                [f[0] for f in files], [f[1] for f in files])
        except asyncpg.UndefinedTableError:
            return {}
        return {(r["repo"], r["target_path"]): int(r["n"]) for r in rows}

    async def status(self) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            f"SELECT count(*) AS repos, coalesce(sum(files), 0) AS files, coalesce(sum(cases), 0) AS cases, "
            f"coalesce(sum(linked), 0) AS linked, max(scanned_at) AS last FROM {self._scans}")
        return {"repos": int(row["repos"]), "files": int(row["files"]), "cases": int(row["cases"]),
                "linked": int(row["linked"]),
                "last_scan": row["last"].astimezone().isoformat(timespec="seconds") if row["last"] else None}
