"""Linkers do núcleo e o recálculo das relações (ver ARQUITETURA.md, domain/relations.py).

`rebuild_relations` roda no fim de cada sync (e em `code-index relations rebuild`):
1. atualiza o catálogo `documents` de cada fonte de documentação (plugin `documents()`, só arquivos locais);
2. roda todos os linkers (núcleo + plugins das fontes + perfis de organização ativos) e troca, numa transação, as relações das origens
   derivadas. Vínculos gravados por pessoas/agents e sugestões de LLM não são tocados.
Sem LLM e sem Voyage.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import asyncpg

from ..domain.ownership import Assignment, OwnershipRules, derived_owner_origins, infer, owner_from_key
from ..domain.contracts import ContractRoutes
from ..domain.documents import WIKI_PAGE, DocumentMeta
from ..domain.models import DocSource
from ..domain.naming import NameRules
from ..domain.relations import (
    BELONGS_TO, CORE_ORIGIN_NAMES, DOCUMENTS, RELATED_TO, Relation, doc_key, origin_info, repo_key,
)
from ..infra.document_store import DocumentStore
from ..infra.enrich_store import facts_subset_sql
from ..infra.relation_store import RelationStore
from ..profiles.base import ActiveProfiles
from ..profiles.registry import active_profiles
from ..settings import Settings
from ..sources.base import DocSourcePlugin, Linker
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
                     AND (lower(r.name) LIKE m.term OR lower(r.name) LIKE '%\\_\\_' || m.term)"""
        )
        return [Relation(doc_key(r["doc_repo"], r["doc_path"]), repo_key(r["name"]), DOCUMENTS, "explicit",
                         evidence={"rule": r["term"]}) for r in rows]


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


class OwnershipLinker:
    """Repo (ou pasta de monorepo) → serviço/domínio do DDD, sem LLM: as regras de dono dos perfis ativos (ex.: no
    perfil `senior`, SDL do próprio repo, repo irmão e rotas chamadas) e o uso de lib do núcleo
    (domain/ownership.infer). Lê os fatos e o material coletados em `enrich facts`."""

    name = "ownership"

    def __init__(self, naming: NameRules = NameRules(), rules: Sequence[OwnershipRules] = ()) -> None:
        self._naming = naming  # regras de nome dos perfis (nome-base dos repos irmãos)
        self._rules = tuple(rules)
        self.origins = derived_owner_origins(self._rules)
        self._sections = sorted({s for r in self._rules for s in r.fact_sections})

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        s = f'"{settings.pg_schema}"'
        rows = await conn.fetch(
            f"""SELECT r.name, {facts_subset_sql("c.material", "$1")} AS facts, c.material->'ownership' AS own
                FROM {s}.repos r LEFT JOIN {s}.repo_cards c ON c.repo = r.name
                WHERE r.kind = 'code' AND NOT r.ignored""",
            self._sections,
        )
        manual_rows = await conn.fetch(
            f"""SELECT src, dst FROM {s}.relations
                WHERE type = $1 AND origin = 'manual' AND status = 'active' AND action = 'add'""",
            BELONGS_TO,
        )
        facts = {r["name"]: f for r in rows if r["facts"] and (f := json.loads(r["facts"]))}
        own = {r["name"]: json.loads(r["own"]) for r in rows if r["own"]}
        manual = [Assignment(m["src"], owner, "manual") for m in manual_rows
                  if (owner := owner_from_key(m["dst"])) is not None]
        return [
            Relation(a.entity, a.owner.key, BELONGS_TO, a.origin, evidence=dict(a.evidence), confidence=a.confidence)
            for a in infer([r["name"] for r in rows], facts, own, manual, rules=self._rules, naming=self._naming)
        ]



def core_linkers(naming: NameRules = NameRules(), ownership: Sequence[OwnershipRules] = ()) -> list[Linker]:
    return [AliasLinker(), RuleLinker(), CardLinker(), OwnershipLinker(naming, ownership)]


def all_linkers(sources: list[DocSource], profiles: ActiveProfiles) -> list[Linker]:
    """Núcleo + linkers dos plugins das fontes configuradas (uma vez por tipo de fonte) + linkers dos perfis de
    organização ativos (`profiles`: também as regras de nome dos repos e as regras de dono). Linker de perfil com o
    nome de um do núcleo ou de uma fonte → ValueError."""
    naming = profiles.naming
    out: list[Linker] = core_linkers(naming, profiles.ownership_rules)
    seen = {linker.name for linker in out}
    for source_type in sorted({s.type for s in sources}):
        for linker in doc_plugin(source_type).linkers(naming):
            if linker.name not in seen:
                seen.add(linker.name)
                out.append(linker)
    for linker in profiles.linkers:
        if linker.name in seen:
            raise ValueError(f"linker de perfil com nome já usado pelo núcleo ou por uma fonte: {linker.name!r}")
        seen.add(linker.name)
        out.append(linker)
    return out


def _documents_call(plugin: DocSourcePlugin, source: DocSource, dest: Path,
                    routes: ContractRoutes) -> Callable[[], list[DocumentMeta]]:
    """`plugin.documents` com as rotas de contrato, se o plugin as aceita (`routes` é opcional no contrato: plugin
    que não descreve APIs pode não ter o parâmetro)."""
    params = inspect.signature(plugin.documents).parameters
    if "routes" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return functools.partial(plugin.documents, source, dest, routes=routes)
    return functools.partial(plugin.documents, source, dest)


async def refresh_documents(documents: DocumentStore, settings: Settings, sources: list[DocSource],
                            routes: ContractRoutes = ContractRoutes()) -> dict[str, int]:
    """Catálogo `documents` de cada fonte, a partir do que ela materializou em disco. `routes`: rotas das operações
    de contrato dos perfis ativos (página de API → operação)."""
    out: dict[str, int] = {}
    for source in sources:
        dest = settings.repos_dir / source.repo_name
        if not dest.is_dir():
            continue
        plugin = doc_plugin(source.type)
        metas = await asyncio.to_thread(_documents_call(plugin, source, dest, routes))
        out[source.repo_name] = (await documents.replace_source(source.repo_name, metas))["documents"]
    await documents.prune_sources([s.repo_name for s in sources])
    return out


def _check_origins(linker: Linker, found: Sequence[Relation]) -> None:
    """Toda relação de um linker tem uma origem dele e registrada (núcleo ou perfil carregado): a validação
    da `Relation` aceita qualquer nome bem formado, para ler linhas antigas."""
    bad = sorted({r.origin for r in found if r.origin not in linker.origins or origin_info(r.origin) is None})
    if bad:
        raise ValueError(f"linker {linker.name!r} produziu origens fora das suas ou não registradas: {', '.join(bad)}")


async def rebuild_relations(pool: asyncpg.Pool, settings: Settings, sources: list[DocSource], *,
                            only: Sequence[str] | None = None,
                            profiles: ActiveProfiles | None = None) -> dict[str, Any]:
    """Recalcula documentos e relações derivadas. Idempotente; sem custo de API.
    `only`: roda só estes linkers (pelo nome) e não recalcula o catálogo de documentos.
    `profiles`: perfis ativos (None = os de `settings.profiles`).

    Limpeza: as origens dos linkers que rodaram são trocadas pelas novas. No recálculo completo (sem `only`) sai
    também toda origem derivada que não é do núcleo e que nenhum linker ativo produz: as de um perfil desligado
    (registradas ou não neste processo) e as desconhecidas. Assim um perfil que sai não deixa relações velhas
    (ex.: `declared`/`sibling`/`api_calls` sem o perfil `senior`). Gravadas (manual, agent, llm) nunca saem."""
    started = time.monotonic()
    documents, relations = DocumentStore(pool, settings), RelationStore(pool, settings)
    profiles = profiles if profiles is not None else active_profiles(settings)
    docs = await refresh_documents(documents, settings, sources, profiles.contract_routes) if only is None else {}
    linkers = all_linkers(sources, profiles)
    if only is not None:  # nomes sem linker ativo (ex.: fonte não cadastrada) são ignorados
        linkers = [linker for linker in linkers if linker.name in set(only)]
    produced: dict[str, int] = {}
    async with pool.acquire() as conn:
        rels: list[Relation] = []
        for linker in linkers:
            found = await linker.run(conn, settings)
            _check_origins(linker, found)
            produced[linker.name] = len(found)
            rels += found
        origins = {o for linker in linkers for o in linker.origins}
        async with conn.transaction():
            total = await relations.replace_derived(conn, origins, rels,
                                                    keep_only=CORE_ORIGIN_NAMES if only is None else None)
    took = round(time.monotonic() - started, 2)
    log.info("relações recalculadas: %d (%s) em %.2fs", total, produced, took)
    return {"documents": docs, "linkers": produced, "relations": total, "seconds": took}
