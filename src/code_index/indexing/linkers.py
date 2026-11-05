"""Linkers do núcleo e o recálculo das relações (ver ARQUITETURA.md, domain/relations.py).

`rebuild_relations` roda no fim de cada sync (e em `code-index relations rebuild`):
1. atualiza o catálogo `documents` de cada fonte de documentação (plugin `documents()`, só arquivos locais);
2. roda todos os linkers (núcleo + plugins das fontes) e troca, numa transação, as relações das origens
   derivadas. Vínculos gravados por pessoas/agents e sugestões de LLM não são tocados.
Sem LLM e sem Voyage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import Any

import asyncpg

from ..domain.api_impl import match_handlers
from ..domain.documents import WIKI_PAGE
from ..domain.models import DocSource
from ..domain.relations import (
    DOCUMENTS, IMPLEMENTED_BY, RELATED_TO, Relation, code_key, doc_key, op_key, repo_key,
)
from ..infra.document_store import DocumentStore
from ..infra.relation_store import RelationStore
from ..settings import Settings
from ..sources.base import Linker
from ..sources.registry import doc_plugin

log = logging.getLogger(__name__)

TEXT_LINKED_TYPES = (WIKI_PAGE,)  # documentos em que menção/tag/link no texto liga a repos


async def _exists(conn: asyncpg.Connection, *tables: str) -> bool:
    return all([await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t) for t in tables])


class AliasLinker:
    """Página → repo por tag, link do GitHub ou menção no texto, casando com os apelidos do repo
    (nome e manifestos). Menção só com apelido forte e em página que não cita repos demais."""

    name = "alias"
    origins = frozenset({"tag", "url", "mention"})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        m, a = settings.qualified(settings.mentions_table), settings.qualified(settings.aliases_table)
        if not await _exists(conn, m, a):
            return []
        s = f'"{settings.pg_schema}"'
        max_repos = int(settings.doc_mention_max_repos)
        rows = await conn.fetch(
            f"""WITH texts AS MATERIALIZED (SELECT doc_repo, path FROM {s}.documents WHERE type = ANY($1::text[])),
                hits AS MATERIALIZED (
                  SELECT m.doc_repo, m.doc_path, a.repo, m.origin, m.term, a.strong
                  FROM {m} m JOIN {a} a ON a.alias = m.term AND a.repo <> m.doc_repo
                  WHERE m.origin IN ('tag', 'url') OR (m.origin = 'mention' AND a.strong)),
                crowded AS MATERIALIZED (
                  SELECT doc_repo, doc_path FROM hits WHERE origin = 'mention'
                  GROUP BY 1, 2 HAVING count(DISTINCT repo) > $2)
                SELECT DISTINCT h.doc_repo, h.doc_path, h.repo, h.origin, h.term
                FROM hits h
                JOIN texts t ON t.doc_repo = h.doc_repo AND t.path = h.doc_path
                JOIN {s}.repos r ON r.name = h.repo AND r.kind = 'code' AND NOT r.ignored
                LEFT JOIN crowded c ON h.origin = 'mention' AND c.doc_repo = h.doc_repo AND c.doc_path = h.doc_path
                WHERE c.doc_repo IS NULL""",
            list(TEXT_LINKED_TYPES), max_repos,
        )
        terms: dict[tuple[str, str, str], list[str]] = {}
        for r in rows:
            terms.setdefault((doc_key(r["doc_repo"], r["doc_path"]), repo_key(r["repo"]), r["origin"]), []).append(r["term"])
        return [Relation(src, dst, DOCUMENTS, origin, evidence={"terms": sorted(t)[:5]})
                for (src, dst, origin), t in terms.items()]


class RuleLinker:
    """Página → repo pela regra `--link 'caminho_glob=repo_glob'` da fonte de documentação."""

    name = "rule"
    origins = frozenset({"explicit"})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        m = settings.qualified(settings.mentions_table)
        if not await _exists(conn, m):
            return []
        rows = await conn.fetch(
            f"""SELECT DISTINCT m.doc_repo, m.doc_path, r.name, m.term FROM {m} m
                JOIN "{settings.pg_schema}".repos r ON m.origin = 'explicit' AND r.kind = 'code' AND NOT r.ignored
                     AND (r.name LIKE m.term OR r.name LIKE '%\\_\\_' || m.term)"""
        )
        return [Relation(doc_key(r["doc_repo"], r["doc_path"]), repo_key(r["name"]), DOCUMENTS, "explicit",
                         evidence={"rule": r["term"]}) for r in rows]


class HandlerLinker:
    """Operação do `.sdl` → classe `<operação>Handler` do mesmo repo (convenção do SDK, sem LLM)."""

    name = "handler"
    origins = frozenset({"handler"})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        s = f'"{settings.pg_schema}"'
        cards = await conn.fetch(
            f"""SELECT c.repo, c.material->'facts'->'sdl' AS sdl FROM {s}.repo_cards c
                JOIN {s}.repos r ON r.name = c.repo AND r.kind = 'code' AND NOT r.ignored
                WHERE jsonb_array_length(coalesce(c.material->'facts'->'sdl', '[]'::jsonb)) > 0"""
        )
        symbols = settings.symbols_table_qualified
        if not cards or not await _exists(conn, symbols):
            return []
        out: list[Relation] = []
        for card in cards:
            classes = [(r["name"], r["path"], int(r["line"])) for r in await conn.fetch(
                f"SELECT name, path, line FROM {symbols} WHERE repo = $1 AND kind = 'container'", card["repo"])]
            for impl in match_handlers(json.loads(card["sdl"]), classes):
                out.append(Relation(
                    op_key(impl["domain"], impl["service"], impl["operation"]),
                    code_key(card["repo"], impl["path"], impl["line"]), IMPLEMENTED_BY, "handler",
                    evidence={**impl, "repo": card["repo"]},
                ))
        return out


class CardLinker:
    """Repo → repo pelos relacionados do cartão do repo (escrito por agent), com a base (código/nome)."""

    name = "card"
    origins = frozenset({"card"})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        rows = await conn.fetch(
            f"""SELECT c.repo, rel FROM "{settings.pg_schema}".repo_cards c
                CROSS JOIN LATERAL jsonb_array_elements(coalesce(c.card->'related_repos', '[]'::jsonb)) rel
                WHERE c.card IS NOT NULL"""
        )
        out = []
        for r in rows:
            rel = json.loads(r["rel"])
            if isinstance(rel, dict) and rel.get("repo") and rel["repo"] != r["repo"]:
                out.append(Relation(repo_key(r["repo"]), repo_key(rel["repo"]), RELATED_TO, "card",
                                    evidence={"basis": rel.get("basis", "nome"), "why": rel.get("why", "")}))
        return out


CORE_LINKERS: tuple[Linker, ...] = (AliasLinker(), RuleLinker(), HandlerLinker(), CardLinker())


def all_linkers(sources: list[DocSource]) -> list[Linker]:
    """Núcleo + linkers dos plugins das fontes configuradas (uma vez por tipo de fonte)."""
    out: list[Linker] = list(CORE_LINKERS)
    seen = {linker.name for linker in out}
    for source_type in sorted({s.type for s in sources}):
        for linker in doc_plugin(source_type).linkers():
            if linker.name not in seen:
                seen.add(linker.name)
                out.append(linker)
    return out


async def refresh_documents(documents: DocumentStore, settings: Settings, sources: list[DocSource]) -> dict[str, int]:
    """Catálogo `documents` de cada fonte, a partir do que ela materializou em disco."""
    out: dict[str, int] = {}
    for source in sources:
        dest = settings.repos_dir / source.repo_name
        if not dest.is_dir():
            continue
        plugin = doc_plugin(source.type)
        metas = await asyncio.to_thread(plugin.documents, source, dest)
        out[source.repo_name] = (await documents.replace_source(source.repo_name, metas))["documents"]
    await documents.prune_sources([s.repo_name for s in sources])
    return out


async def rebuild_relations(pool: asyncpg.Pool, settings: Settings, sources: list[DocSource], *,
                            only: Sequence[str] | None = None) -> dict[str, Any]:
    """Recalcula documentos e relações derivadas. Idempotente; sem custo de API.
    `only`: roda só estes linkers (pelo nome) e não recalcula o catálogo de documentos."""
    started = time.monotonic()
    documents, relations = DocumentStore(pool, settings), RelationStore(pool, settings)
    docs = await refresh_documents(documents, settings, sources) if only is None else {}
    linkers = all_linkers(sources)
    if only is not None:  # nomes sem linker ativo (ex.: fonte não cadastrada) são ignorados
        linkers = [linker for linker in linkers if linker.name in set(only)]
    produced: dict[str, int] = {}
    async with pool.acquire() as conn:
        rels: list[Relation] = []
        for linker in linkers:
            found = await linker.run(conn, settings)
            produced[linker.name] = len(found)
            rels += found
        origins = {o for linker in linkers for o in linker.origins}
        async with conn.transaction():
            total = await relations.replace_derived(conn, origins, rels)
    took = round(time.monotonic() - started, 2)
    log.info("relações recalculadas: %d (%s) em %.2fs", total, produced, took)
    return {"documents": docs, "linkers": produced, "relations": total, "seconds": took}
