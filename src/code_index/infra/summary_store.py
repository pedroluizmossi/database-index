"""Resumos de arquivos de código (`file_summaries`) e a aplicação deles nas colunas de busca por termos.

Pendente = arquivo de código indexado sem resumo ou cujo conteúdo mudou desde o resumo (`content_hash`), e
sem reserva ativa. A aplicação é um UPDATE em `code_chunks` (tabela do CocoIndex): acrescenta os termos do
resumo depois de um marcador em `search_symbols` e `search_terms`. É idempotente (reaplica a partir do que
vem antes do marcador), não toca no embedding e é refeita depois de cada sync, porque o CocoIndex regrava as
linhas dos arquivos que reprocessa.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from ..domain.file_summary import MARK, SUMMARY_LANGUAGES, symbol_terms, text_terms
from ..settings import Settings
from .enrich_store import EnrichTask, _task

_SEP = f" {MARK} "
KIND = "file_summaries"


def _with_summary(column: str, terms: str) -> str:
    """Expressão SQL: o que o índice gravou em `column` + marcador + termos do resumo."""
    return f"split_part(coalesce({column}, ''), '{_SEP}', 1) || '{_SEP}' || {terms}"


class SummaryStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        schema = settings.pg_schema
        self._chunks = settings.chunks_table_qualified
        self._summaries = f'"{schema}".file_summaries'
        self._tasks = f'"{schema}".enrich_tasks'
        self._repos = f'"{schema}".repos'

    def _files_sql(self) -> str:
        """Arquivos de código indexados, com o hash do conteúdo ($1 = linguagens, $2 = repo ou NULL)."""
        return f"""SELECT c.repo, c.path, min(c.language) AS language,
                          md5(string_agg(md5(c.code), '' ORDER BY c.start_line)) AS hash
                   FROM {self._chunks} c
                   WHERE c.language = ANY($1::text[]) AND ($2::text IS NULL OR c.repo = $2)
                     AND EXISTS (SELECT 1 FROM {self._repos} r WHERE r.name = c.repo AND r.kind = 'code' AND NOT r.ignored)
                   GROUP BY c.repo, c.path"""

    async def pending(self, repo: str | None = None) -> list[dict[str, Any]]:
        """Arquivos sem resumo em dia e sem reserva ativa: [{repo, path, language, hash}], por repo e caminho."""
        try:
            rows = await self._pool.fetch(
                f"""SELECT f.* FROM ({self._files_sql()}) f
                    LEFT JOIN {self._summaries} s ON s.repo = f.repo AND s.path = f.path
                    WHERE (s.path IS NULL OR s.content_hash <> f.hash)
                      AND NOT EXISTS (SELECT 1 FROM {self._tasks} t WHERE t.kind = '{KIND}' AND t.status = 'leased'
                                      AND t.repo = f.repo
                                      AND t.payload->'files' @> jsonb_build_array(jsonb_build_object('path', f.path)))
                    ORDER BY f.repo, f.path""",
                list(SUMMARY_LANGUAGES), repo,
            )
        except asyncpg.UndefinedTableError:  # nada indexado ainda
            return []
        return [dict(r) for r in rows]

    async def file_code(self, repo: str, paths: list[str]) -> dict[str, str]:
        """Conteúdo indexado de cada arquivo (trechos em ordem)."""
        rows = await self._pool.fetch(
            f"""SELECT path, string_agg(code, E'\\n' ORDER BY start_line) AS code FROM {self._chunks}
                WHERE repo = $1 AND path = ANY($2::text[]) GROUP BY path""", repo, paths)
        return {r["path"]: r["code"] for r in rows}

    async def lease_batch(self, *, agent: str, lease_minutes: int, repo: str,
                          files: list[dict[str, Any]]) -> EnrichTask | None:
        """Reserva um lote de arquivos de um repo. None se outra reserva começa no mesmo arquivo."""
        payload = {"files": [{"path": f["path"], "hash": f["hash"]} for f in files]}
        row = await self._pool.fetchrow(
            f"""INSERT INTO {self._tasks} (kind, repo, subject, input_hash, payload, agent, status, lease_until)
                VALUES ('{KIND}', $1, $2, $3, $4::jsonb, $5, 'leased', now() + make_interval(mins => $6))
                ON CONFLICT DO NOTHING RETURNING *""",
            repo, files[0]["path"], files[0]["hash"], json.dumps(payload, ensure_ascii=False), agent, lease_minutes,
        )
        return _task(row) if row else None

    async def complete(self, conn: asyncpg.Connection, task: EnrichTask, results: list[dict[str, Any]],
                       agent: str) -> None:
        """Grava os resumos do lote e fecha a tarefa."""
        hashes = {f["path"]: f["hash"] for f in (task.payload or {}).get("files", [])}
        for r in results:
            await conn.execute(
                f"""INSERT INTO {self._summaries}
                        (repo, path, content_hash, summary, questions, keywords, symbol_terms, text_terms, agent)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9)
                    ON CONFLICT (repo, path) DO UPDATE SET content_hash = EXCLUDED.content_hash,
                        summary = EXCLUDED.summary, questions = EXCLUDED.questions, keywords = EXCLUDED.keywords,
                        symbol_terms = EXCLUDED.symbol_terms, text_terms = EXCLUDED.text_terms,
                        agent = EXCLUDED.agent, created_at = now()""",
                task.repo, r["path"], hashes.get(r["path"], ""), r["summary"],
                json.dumps(r["questions"], ensure_ascii=False), json.dumps(r["keywords"], ensure_ascii=False),
                symbol_terms(r["summary"], r["keywords"]), text_terms(r["summary"], r["questions"], r["keywords"]),
                agent,
            )
        await conn.execute(
            f"UPDATE {self._tasks} SET status = 'done', finished_at = now(), last_error = NULL, "
            "agent = CASE WHEN $2 = '' THEN agent ELSE $2 END WHERE id = $1::uuid", task.id, agent,
        )

    async def apply(self, conn: asyncpg.Connection | asyncpg.Pool | None = None, *, repo: str | None = None,
                    paths: list[str] | None = None) -> int:
        """Põe os termos dos resumos nas colunas de busca dos trechos. Devolve quantos trechos mudaram.
        Sem `repo`/`paths`, vale para tudo (depois de um sync). Não mexe no embedding."""
        symbols = _with_summary("c.search_symbols", "s.symbol_terms")
        terms = _with_summary("c.search_terms", "s.text_terms")
        try:
            res = await (conn or self._pool).execute(
                f"""UPDATE {self._chunks} c SET search_symbols = {symbols}, search_terms = {terms}
                    FROM {self._summaries} s
                    WHERE c.repo = s.repo AND c.path = s.path
                      AND ($1::text IS NULL OR c.repo = $1) AND ($2::text[] IS NULL OR c.path = ANY($2::text[]))
                      AND (c.search_symbols IS DISTINCT FROM {symbols} OR c.search_terms IS DISTINCT FROM {terms})""",
                repo, paths,
            )
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):  # nada indexado / índice antigo
            return 0
        return int(res.split()[-1])

    async def prune(self) -> int:
        """Apaga resumos de arquivos que saíram do índice."""
        try:
            res = await self._pool.execute(
                f"""DELETE FROM {self._summaries} s
                    WHERE NOT EXISTS (SELECT 1 FROM {self._chunks} c WHERE c.repo = s.repo AND c.path = s.path)""")
        except asyncpg.UndefinedTableError:
            return 0
        return int(res.split()[-1])

    async def stats(self) -> dict[str, int]:
        """Arquivos de código indexados: total, com resumo em dia, com resumo desatualizado e sem resumo."""
        try:
            row = await self._pool.fetchrow(
                f"""SELECT count(*) AS files,
                           count(*) FILTER (WHERE s.content_hash = f.hash) AS fresh,
                           count(*) FILTER (WHERE s.path IS NOT NULL AND s.content_hash <> f.hash) AS stale
                    FROM ({self._files_sql()}) f
                    LEFT JOIN {self._summaries} s ON s.repo = f.repo AND s.path = f.path""",
                list(SUMMARY_LANGUAGES), None,
            )
        except asyncpg.UndefinedTableError:
            return {"files": 0, "fresh": 0, "stale": 0, "missing": 0}
        files, fresh, stale = int(row["files"]), int(row["fresh"]), int(row["stale"])
        return {"files": files, "fresh": fresh, "stale": stale, "missing": files - fresh - stale}

    @staticmethod
    def _row(row: asyncpg.Record) -> dict[str, Any]:
        return {**dict(row), "questions": json.loads(row["questions"]), "keywords": json.loads(row["keywords"])}

    async def get(self, repo: str, path: str) -> dict[str, Any] | None:
        """Resumo de um arquivo, com `stale` = o arquivo mudou no índice desde que o resumo foi escrito."""
        try:
            row = await self._pool.fetchrow(
                f"""SELECT s.repo, s.path, s.summary, s.questions, s.keywords, s.agent, s.created_at,
                           s.content_hash IS DISTINCT FROM (
                               SELECT md5(string_agg(md5(c.code), '' ORDER BY c.start_line)) FROM {self._chunks} c
                               WHERE c.repo = s.repo AND c.path = s.path) AS stale
                    FROM {self._summaries} s WHERE s.repo = $1 AND s.path = $2""", repo, path)
        except asyncpg.UndefinedTableError:
            return None
        return self._row(row) if row else None

    async def get_many(self, files: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        """Resumo e palavras-chave de vários arquivos de uma vez (para anotar resultados de busca)."""
        if not files:
            return {}
        try:
            rows = await self._pool.fetch(
                f"""SELECT s.repo, s.path, s.summary, s.keywords FROM {self._summaries} s
                    JOIN unnest($1::text[], $2::text[]) AS f(repo, path) ON f.repo = s.repo AND f.path = s.path""",
                [f[0] for f in files], [f[1] for f in files])
        except asyncpg.UndefinedTableError:
            return {}
        return {(r["repo"], r["path"]): {"summary": r["summary"], "keywords": json.loads(r["keywords"])} for r in rows}

    async def list_repo(self, repo: str, *, limit: int = 2000) -> list[dict[str, Any]]:
        """Resumos de um repo, por caminho."""
        rows = await self._pool.fetch(
            f"""SELECT repo, path, summary, questions, keywords, agent, created_at FROM {self._summaries}
                WHERE repo = $1 ORDER BY path LIMIT $2""", repo, limit)
        return [self._row(r) for r in rows]

    async def repo_files(self, repo: str) -> list[dict[str, Any]]:
        """Arquivos indexados de um repo (todas as linguagens), com o resumo de cada um quando existe.
        `summarizable`: linguagem que entra na fila de resumos; `stale`: o arquivo mudou depois do resumo."""
        try:
            rows = await self._pool.fetch(
                f"""SELECT f.path, f.language, f.chunks, f.lines, s.summary, s.keywords,
                           s.path IS NOT NULL AND s.content_hash <> f.hash AS stale
                    FROM (SELECT c.path, min(c.language) AS language, count(*) AS chunks, max(c.end_line) AS lines,
                                 md5(string_agg(md5(c.code), '' ORDER BY c.start_line)) AS hash
                          FROM {self._chunks} c WHERE c.repo = $1 GROUP BY c.path) f
                    LEFT JOIN {self._summaries} s ON s.repo = $1 AND s.path = f.path
                    ORDER BY f.path""", repo)
        except asyncpg.UndefinedTableError:
            return []
        return [{"path": r["path"], "language": r["language"], "chunks": int(r["chunks"]), "lines": int(r["lines"]),
                 "summarizable": r["language"] in SUMMARY_LANGUAGES, "summary": r["summary"],
                 "keywords": json.loads(r["keywords"]) if r["keywords"] else [], "stale": bool(r["stale"])}
                for r in rows]

    async def coverage(self) -> list[dict[str, Any]]:
        """Por repo: arquivos de código indexados, com resumo em dia e com resumo desatualizado."""
        try:
            rows = await self._pool.fetch(
                f"""SELECT f.repo, count(*) AS files,
                           count(*) FILTER (WHERE s.content_hash = f.hash) AS fresh,
                           count(*) FILTER (WHERE s.path IS NOT NULL AND s.content_hash <> f.hash) AS stale
                    FROM ({self._files_sql()}) f
                    LEFT JOIN {self._summaries} s ON s.repo = f.repo AND s.path = f.path
                    GROUP BY f.repo ORDER BY f.repo""",
                list(SUMMARY_LANGUAGES), None,
            )
        except asyncpg.UndefinedTableError:
            return []
        return [{"repo": r["repo"], "files": int(r["files"]), "fresh": int(r["fresh"]), "stale": int(r["stale"])}
                for r in rows]
