"""Grafo de chamadas (`code_edges`, `code_graph_scans`): gravação por repo, consultas de quem chama/é chamado."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import asyncpg

from ..domain.code_graph import GraphResult
from ..settings import Settings

_COLUMNS = ["repo", "src_path", "src_line", "src_name", "dst_path", "dst_line", "dst_name", "via", "line", "calls"]


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


class GraphStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._schema = settings.pg_schema
        self._edges = f'"{self._schema}".code_edges'
        self._scans = f'"{self._schema}".code_graph_scans'

    async def scanned(self) -> dict[str, str]:
        """repo → chave da última leitura (revisão + versão do extrator)."""
        rows = await self._pool.fetch(f"SELECT repo, revision FROM {self._scans}")
        return {r["repo"]: r["revision"] for r in rows}

    async def replace(self, repo: str, revision: str, result: GraphResult) -> None:
        """Troca as arestas do repo pelas lidas agora (numa transação) e registra a leitura."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"DELETE FROM {self._edges} WHERE repo = $1", repo)
            if result.edges:
                await conn.copy_records_to_table(
                    "code_edges", schema_name=self._schema, columns=_COLUMNS,
                    records=[(repo, e.src_path, e.src_line, e.src_name, e.dst_path, e.dst_line, e.dst_name, e.via,
                              e.line, e.calls) for e in result.edges],
                )
            await conn.execute(
                f"""INSERT INTO {self._scans} (repo, revision, files, definitions, calls, resolved, inferred, edges)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (repo) DO UPDATE SET revision = EXCLUDED.revision, files = EXCLUDED.files,
                        definitions = EXCLUDED.definitions, calls = EXCLUDED.calls, resolved = EXCLUDED.resolved,
                        inferred = EXCLUDED.inferred, edges = EXCLUDED.edges, scanned_at = now()""",
                repo, revision, result.files, result.definitions, result.calls, result.resolved, result.inferred,
                len(result.edges),
            )

    async def prune(self, keep: Sequence[str]) -> int:
        """Apaga arestas e leituras de repos que saíram (removidos, ignorados ou sem clone)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(f"DELETE FROM {self._edges} WHERE repo <> ALL($1::text[])", list(keep))
            res = await conn.execute(f"DELETE FROM {self._scans} WHERE repo <> ALL($1::text[])", list(keep))
        return int(res.split()[-1])

    async def status(self) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            f"""SELECT count(*) AS repos, coalesce(sum(files), 0) AS files, coalesce(sum(calls), 0) AS calls,
                       coalesce(sum(resolved), 0) AS resolved, coalesce(sum(inferred), 0) AS inferred,
                       coalesce(sum(edges), 0) AS edges, max(scanned_at) AS last_scan
                FROM {self._scans}""")
        vias = await self._pool.fetch(f"SELECT via, count(*) AS n FROM {self._edges} GROUP BY via ORDER BY n DESC")
        return {
            "repos": int(row["repos"]), "files": int(row["files"]), "calls": int(row["calls"]),
            "resolved": int(row["resolved"]), "inferred": int(row["inferred"]), "edges": int(row["edges"]),
            "edges_by_via": {r["via"]: int(r["n"]) for r in vias},
            "last_scan": row["last_scan"].isoformat() if row["last_scan"] else None,
        }

    async def caller_counts(self, files: Sequence[tuple[str, str]]) -> dict[tuple[str, str], int]:
        """Quantos trechos de OUTROS arquivos chamam algo de cada arquivo (repo, caminho)."""
        if not files:
            return {}
        try:
            rows = await self._pool.fetch(
                f"""SELECT e.repo, e.dst_path, count(DISTINCT (e.src_path, e.src_line)) AS n FROM {self._edges} e
                    JOIN unnest($1::text[], $2::text[]) AS f(repo, path) ON f.repo = e.repo AND f.path = e.dst_path
                    WHERE e.src_path <> e.dst_path GROUP BY e.repo, e.dst_path""",
                [f[0] for f in files], [f[1] for f in files])
        except asyncpg.UndefinedTableError:
            return {}
        return {(r["repo"], r["dst_path"]): int(r["n"]) for r in rows}

    async def file_counts(self, repo: str, path: str) -> dict[str, int]:
        """Chamadas entre este arquivo e os outros do repo: quem chama algo dele e quantos alvos ele chama."""
        row = await self._pool.fetchrow(
            f"""SELECT count(DISTINCT (src_path, src_line)) FILTER (WHERE dst_path = $2) AS callers,
                       count(DISTINCT (dst_path, dst_line, dst_name)) FILTER (WHERE src_path = $2) AS callees
                FROM {self._edges} WHERE repo = $1 AND (dst_path = $2 OR src_path = $2) AND src_path <> dst_path""",
            repo, path)
        return {"callers": int(row["callers"]), "callees": int(row["callees"])}

    async def central_files(self, repo: str, limit: int = 10) -> list[dict[str, Any]]:
        """Arquivos com mais chamadores vindos de outros arquivos (os centrais do repo), com o membro mais chamado."""
        rows = await self._pool.fetch(
            f"""WITH per_def AS (
                    SELECT dst_path, dst_name, dst_line, count(DISTINCT (src_path, src_line)) AS n
                    FROM {self._edges} WHERE repo = $1 AND src_path <> dst_path
                    GROUP BY dst_path, dst_name, dst_line),
                per_file AS (
                    SELECT dst_path, count(DISTINCT (e.src_path, e.src_line)) AS callers,
                           count(DISTINCT e.src_path) AS files
                    FROM {self._edges} e WHERE repo = $1 AND src_path <> dst_path GROUP BY dst_path)
                SELECT f.dst_path AS path, f.callers, f.files,
                       (SELECT d.dst_name FROM per_def d WHERE d.dst_path = f.dst_path ORDER BY d.n DESC, d.dst_line
                        LIMIT 1) AS top_name,
                       (SELECT d.dst_line FROM per_def d WHERE d.dst_path = f.dst_path ORDER BY d.n DESC, d.dst_line
                        LIMIT 1) AS top_line
                FROM per_file f ORDER BY f.callers DESC, f.dst_path LIMIT $2""", repo, limit)
        return [{"path": r["path"], "callers": int(r["callers"]), "files": int(r["files"]),
                 "top": {"name": r["top_name"], "line": r["top_line"]}} for r in rows]

    async def neighbors(self, direction: str, *, repos: Sequence[str] | None, name: str | None = None,
                        path: str | None = None, line: int | None = None, external: bool = False,
                        limit: int = 50) -> tuple[list[dict[str, Any]], int]:
        """Arestas que chegam (`callers`) ou saem (`callees`) de um símbolo. `name` casa o nome qualificado
        ("Classe.metodo") ou só o final ("metodo"); `path` casa o fim do caminho; `line`, a linha da definição.
        Devolve (arestas, total), as mais confiáveis primeiro."""
        if direction not in ("callers", "callees"):
            raise ValueError(f"direção desconhecida: {direction}")
        side = "dst" if direction == "callers" else "src"
        clauses, args = [], []

        def arg(value: object) -> str:
            args.append(value)
            return f"${len(args)}"

        if repos:
            clauses.append(f"repo = ANY({arg(list(repos))}::text[])")
        if name:
            n = name.lower()
            clauses.append(f"(lower({side}_name) = {arg(n)} OR lower({side}_name) LIKE {arg('%.' + _like(n))})")
        if path:
            clean = path.lstrip("/")
            clauses.append(f"({side}_path = {arg(clean)} OR {side}_path LIKE {arg('%/' + _like(clean))})")
        if line is not None:
            clauses.append(f"{side}_line = {arg(int(line))}")
        if external:
            clauses.append("src_path <> dst_path")
        if not (name or path or line is not None):
            raise ValueError("informe o símbolo (nome, caminho ou caminho:linha)")
        where = " AND ".join(clauses)
        order = ("CASE via WHEN 'arquivo' THEN 0 WHEN 'pacote' THEN 1 WHEN 'import' THEN 2 WHEN 'tipo' THEN 3 "
                 "WHEN 'herança' THEN 4 ELSE 5 END")
        try:
            total = await self._pool.fetchval(f"SELECT count(*) FROM {self._edges} WHERE {where}", *args)
            rows = await self._pool.fetch(
                f"""SELECT {', '.join(_COLUMNS)} FROM {self._edges} WHERE {where}
                    ORDER BY {order}, repo, src_path, line LIMIT {int(limit)}""", *args)
        except asyncpg.UndefinedTableError:
            return [], 0
        return [dict(r) for r in rows], int(total)
