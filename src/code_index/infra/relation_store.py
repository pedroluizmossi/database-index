"""Relações entre entidades (tabela `relations` e view `effective_relations`). Ver domain/relations.py.

Único ponto que lê ou grava vínculos: página → repo, página de API → operação, operação → handler,
repo → repo e o dono do código no DDD (repo/pasta → serviço/domínio). Linkers derivados são regravados por `replace_derived` (indexing/linkers.py); vínculos de
pessoas/agents e sugestões de LLM são gravados pelos métodos de escrita abaixo.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import asyncpg

from ..domain.relations import (
    BELONGS_TO, DESCRIBES, DOCUMENTS, IMPLEMENTED_BY, RELATED_TO, STORED, Relation, doc_key, origin_rank, repo_key,
    sort_origins, split_code_key,
)
from ..settings import Settings


# doc_repo e caminho a partir da chave 'doc:<doc_repo>/<caminho>' (vínculos gravados sobrevivem à página)
_DOC_REPO = "substring(r.src from '^doc:([^/]+)/')"
_DOC_PATH = "substring(r.src from '^doc:[^/]+/(.*)$')"


def _jsonb(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class RelationStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings
        schema = settings.pg_schema
        self.table = f'"{schema}".relations'
        self.effective = f'"{schema}".effective_relations'
        self._docs = f'"{schema}".documents'

    # ------------------------------------------------------------ projeções usadas em outras consultas

    def doc_links_sql(self) -> str:
        """SELECT doc_repo, doc_path, repo, origin dos vínculos efetivos página → repo (subconsulta)."""
        return f"""SELECT d.doc_repo, d.path AS doc_path, substr(e.dst, 6) AS repo, e.origin
                   FROM {self.effective} e JOIN {self._docs} d ON d.key = e.src
                   WHERE e.type = '{DOCUMENTS}'"""

    # ------------------------------------------------------------ linkers derivados

    async def replace_derived(self, conn: asyncpg.Connection, origins: Iterable[str],
                              relations: Sequence[Relation]) -> int:
        """Troca todas as relações destas origens derivadas pelas novas (dentro da transação de `conn`)."""
        names = sorted(set(origins))
        if set(names) & STORED:
            raise ValueError("origens gravadas por pessoas/agents não são recalculadas")
        await conn.execute(f"DELETE FROM {self.table} WHERE origin = ANY($1::text[])", names)
        rows = list({(r.src, r.dst, r.type, r.origin): r for r in relations}.values())
        if rows:
            await conn.executemany(
                f"""INSERT INTO {self.table} (src, dst, type, origin, contestable, confidence, evidence)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
                [(r.src, r.dst, r.type, r.origin, r.contestable, r.confidence, _jsonb(r.evidence)) for r in rows],
            )
        return len(rows)

    # ------------------------------------------------------------ página → repo: leitura

    async def docs_for_repo(self, repo: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Páginas ligadas a um repo, da origem mais confiável para a menos."""
        rows = await self._pool.fetch(
            f"""SELECT d.doc_repo, d.path, d.url, d.title, array_agg(DISTINCT e.origin) AS origins
                FROM {self.effective} e JOIN {self._docs} d ON d.key = e.src
                WHERE e.type = $3 AND e.dst = $1
                GROUP BY d.doc_repo, d.path, d.url, d.title LIMIT $2""",
            repo_key(repo), max(1, min(limit, 500)), DOCUMENTS,
        )
        out = [{"doc_repo": r["doc_repo"], "path": r["path"], "url": r["url"] or None, "title": r["title"] or None,
                "origins": sort_origins(r["origins"])} for r in rows]
        return sorted(out, key=lambda d: (origin_rank(d["origins"][0]), d["title"] or d["path"]))

    async def links_for_docs(self, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, set[str]]]:
        """(doc_repo, path) → {repo: {origens}} dos vínculos efetivos."""
        keys = [doc_key(r, p) for r, p in dict.fromkeys(pairs)]
        if not keys:
            return {}
        rows = await self._pool.fetch(
            f"""SELECT d.doc_repo, d.path, substr(e.dst, 6) AS repo, e.origin
                FROM {self.effective} e JOIN {self._docs} d ON d.key = e.src
                WHERE e.type = $2 AND e.src = ANY($1::text[])""",
            keys, DOCUMENTS,
        )
        out: dict[tuple[str, str], dict[str, set[str]]] = {}
        for r in rows:
            out.setdefault((r["doc_repo"], r["path"]), {}).setdefault(r["repo"], set()).add(r["origin"])
        return out

    async def impls_for_docs(self, pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[str]]:
        """Página de API → handlers da operação descrita ('repo/caminho:linha')."""
        keys = [doc_key(r, p) for r, p in dict.fromkeys(pairs)]
        if not keys:
            return {}
        rows = await self._pool.fetch(
            f"""SELECT d.doc_repo, d.path, i.dst
                FROM {self.effective} x JOIN {self._docs} d ON d.key = x.src
                JOIN {self.effective} i ON i.src = x.dst AND i.type = $3
                WHERE x.type = $2 AND x.src = ANY($1::text[])
                ORDER BY i.dst""",
            keys, DESCRIBES, IMPLEMENTED_BY,
        )
        out: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            repo, path, line = split_code_key(r["dst"])
            out.setdefault((r["doc_repo"], r["path"]), []).append(f"{repo}/{path}:{line}")
        return out

    async def all_doc_links(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT DISTINCT doc_repo, doc_path, repo, origin FROM ({self.doc_links_sql()}) l "
            "ORDER BY doc_repo, doc_path, repo, origin")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ página → repo: gravados por pessoa/agent

    async def set_stored_link(self, doc_repo: str, path: str, repo: str, *, origin: str, reason: str,
                              agent: str = "") -> None:
        """Grava (ou atualiza) um vínculo manual/agent. Vale na hora (sem reindexar)."""
        if origin not in ("manual", "agent"):
            raise ValueError("origem inválida: use manual ou agent")
        await self._pool.execute(
            f"""INSERT INTO {self.table} (src, dst, type, origin, evidence, agent)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (src, dst, type, origin) DO UPDATE SET evidence = EXCLUDED.evidence, status = 'active',
                    action = 'add', agent = EXCLUDED.agent, updated_at = now()""",
            doc_key(doc_repo, path), repo_key(repo), DOCUMENTS, origin, _jsonb({"reason": reason} if reason else {}),
            agent,
        )

    async def remove_stored_link(self, doc_repo: str, path: str, repo: str) -> bool:
        """Remove vínculos gravados (manual, agent ou LLM aprovado) deste par."""
        res = await self._pool.execute(
            f"DELETE FROM {self.table} WHERE src = $1 AND dst = $2 AND type = $3 AND origin = ANY($4::text[]) "
            "AND action = 'add'",
            doc_key(doc_repo, path), repo_key(repo), DOCUMENTS, sorted(STORED),
        )
        return not res.endswith(" 0")

    async def stored_links(self, *, doc_repo: str | None = None, repo: str | None = None) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"""SELECT {_DOC_REPO} AS doc_repo, {_DOC_PATH} AS doc_path, substr(r.dst, 6) AS repo, r.origin,
                       coalesce(r.evidence->>'reason', '') AS reason, r.updated_at AS created_at
                FROM {self.table} r
                WHERE r.type = $3 AND r.origin = ANY($4::text[]) AND r.status = 'active' AND r.action = 'add'
                  AND ($1::text IS NULL OR {_DOC_REPO} = $1) AND ($2::text IS NULL OR r.dst = 'repo:' || $2)
                ORDER BY r.updated_at DESC""",
            doc_repo, repo, DOCUMENTS, sorted(STORED),
        )
        return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]

    async def stored_reasons(self) -> dict[tuple[str, str, str], str]:
        rows = await self._pool.fetch(
            f"""SELECT {_DOC_REPO} AS doc_repo, {_DOC_PATH} AS path, substr(r.dst, 6) AS repo,
                       coalesce(r.evidence->>'reason', '') AS reason
                FROM {self.table} r
                WHERE r.type = $1 AND r.origin = ANY($2::text[]) AND r.status = 'active' AND r.action = 'add'""",
            DOCUMENTS, sorted(STORED),
        )
        return {(r["doc_repo"], r["path"], r["repo"]): r["reason"] for r in rows}

    # ------------------------------------------------------------ sugestões de LLM (adicionar/remover)

    async def suggest(self, conn: asyncpg.Connection, *, doc_repo: str, path: str, repo: str, action: str,
                      reason: str, confidence: str, basis: str | None, agent: str) -> bool:
        """Grava uma sugestão (status 'suggested'). Não sobrescreve sugestão já revisada do mesmo par."""
        res = await conn.execute(
            f"""INSERT INTO {self.table} (src, dst, type, origin, status, action, confidence, evidence, agent)
                VALUES ($1, $2, $3, 'llm', 'suggested', $4, $5, $6::jsonb, $7)
                ON CONFLICT (src, dst, type, origin) DO NOTHING""",
            doc_key(doc_repo, path), repo_key(repo), DOCUMENTS, action, confidence,
            _jsonb({k: v for k, v in {"reason": reason, "basis": basis}.items() if v}), agent,
        )
        return res.endswith(" 1")

    async def suggestions(self, *, status: str = "suggested", doc_repo: str | None = None,
                          confidence: str | None = None, action: str | None = None, limit: int = 200) -> list[dict]:
        rows = await self._pool.fetch(
            f"""SELECT {_DOC_REPO} AS doc_repo, {_DOC_PATH} AS doc_path, substr(r.dst, 6) AS repo, r.action,
                       coalesce(r.evidence->>'reason', '') AS reason, r.evidence->>'basis' AS basis, r.confidence,
                       r.agent, r.status, r.updated_at AS created_at, nullif(d.title, '') AS title,
                       nullif(d.url, '') AS url
                FROM {self.table} r LEFT JOIN {self._docs} d ON d.key = r.src
                WHERE r.type = $6 AND r.origin = 'llm' AND r.status = $1 AND ($2::text IS NULL OR {_DOC_REPO} = $2)
                  AND ($3::text IS NULL OR r.confidence = $3) AND ($5::text IS NULL OR r.action = $5)
                ORDER BY CASE r.confidence WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, r.src, r.dst
                LIMIT $4""",
            status, doc_repo, confidence, max(1, min(limit, 2000)), action, DOCUMENTS,
        )
        return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]

    async def set_suggestion_status(self, doc_repo: str, path: str, repo: str, status: str) -> bool:
        """active = aprovada; rejected = não volta a ser sugerida (e desfaz uma remoção aprovada)."""
        res = await self._pool.execute(
            f"UPDATE {self.table} SET status = $4, updated_at = now() "
            "WHERE origin = 'llm' AND src = $1 AND dst = $2 AND type = $3 AND status <> $4",
            doc_key(doc_repo, path), repo_key(repo), DOCUMENTS, status,
        )
        return res.endswith(" 1")

    async def suggestion_stats(self) -> dict[str, int]:
        row = await self._pool.fetchrow(
            f"""SELECT count(*) FILTER (WHERE status = 'suggested' AND action = 'add') AS suggested_add,
                       count(*) FILTER (WHERE status = 'suggested' AND action = 'remove') AS suggested_remove,
                       count(*) FILTER (WHERE status = 'active' AND action = 'add') AS approved_add,
                       count(*) FILTER (WHERE status = 'active' AND action = 'remove') AS approved_remove,
                       count(*) FILTER (WHERE status = 'rejected') AS rejected
                FROM {self.table} WHERE origin = 'llm' AND type = $1""", DOCUMENTS)
        return {k: int(v) for k, v in dict(row).items()}

    # ------------------------------------------------------------ operação → handler

    async def find_impls(self, operation: str, *, service: str | None = None, domain: str | None = None,
                         limit: int = 20) -> list[dict[str, Any]]:
        """Onde uma operação é implementada. Nome exato (sem diferenciar maiúsculas) ou, sem resultado, prefixo."""
        base = f"""SELECT e.evidence, e.origin FROM {self.effective} e
                   WHERE e.type = '{IMPLEMENTED_BY}'
                     AND ($2::text IS NULL OR lower(e.evidence->>'service') = lower($2))
                     AND ($3::text IS NULL OR lower(e.evidence->>'domain') = lower($3)) AND """
        order = (" ORDER BY e.evidence->>'domain', e.evidence->>'service', e.evidence->>'operation', "
                 "e.evidence->>'repo' LIMIT $4")
        rows = await self._pool.fetch(base + "lower(e.evidence->>'operation') = lower($1)" + order,
                                      operation, service, domain, limit)
        if not rows:
            like = operation.lower().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
            rows = await self._pool.fetch(base + "lower(e.evidence->>'operation') LIKE $1" + order,
                                          like, service, domain, limit)
        return [_impl_row(r) for r in rows]

    async def impls_for_repo(self, repo: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT e.evidence, e.origin FROM {self.effective} e WHERE e.type = $2 AND e.evidence->>'repo' = $1 "
            "ORDER BY e.evidence->>'service', e.evidence->>'kind', e.evidence->>'operation'",
            repo, IMPLEMENTED_BY,
        )
        return [{k: v for k, v in _impl_row(r).items() if k != "repo"} for r in rows]

    async def count(self, type_: str) -> int:
        return int(await self._pool.fetchval(f"SELECT count(*) FROM {self.effective} WHERE type = $1", type_))

    # ------------------------------------------------------------ repo → repo

    async def related_repos(self, repo: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT substr(dst, 6) AS repo, origin, evidence FROM {self.effective} "
            "WHERE type = $2 AND src = $1 ORDER BY dst",
            repo_key(repo), RELATED_TO,
        )
        return [{"repo": r["repo"], "origin": r["origin"], **json.loads(r["evidence"])} for r in rows]

    # ------------------------------------------------------------ dono no DDD: repo|pasta → serviço|domínio

    async def owner_rows(self) -> list[dict[str, Any]]:
        """Todas as atribuições efetivas (derivadas e manuais), para resolver por origem (domain/ownership)."""
        rows = await self._pool.fetch(
            f"SELECT src, dst, origin, confidence, evidence, agent, updated_at FROM {self.effective} WHERE type = $1",
            BELONGS_TO,
        )
        return [{**dict(r), "evidence": json.loads(r["evidence"])} for r in rows]

    async def set_manual_owner(self, entities: Sequence[str], owner_key: str, *, actor: str, reason: str = "") -> int:
        """Troca o dono manual destas entidades ('repo:x' ou 'dir:x/caminho'). Vale na hora, sem reindexar."""
        keys = sorted(set(entities))
        evidence = _jsonb({"reason": reason} if reason else {})
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"DELETE FROM {self.table} WHERE type = $1 AND origin = 'manual' AND src = ANY($2::text[])",
                BELONGS_TO, keys,
            )
            await conn.executemany(
                f"""INSERT INTO {self.table} (src, dst, type, origin, evidence, agent)
                    VALUES ($1, $2, $3, 'manual', $4::jsonb, $5)""",
                [(k, owner_key, BELONGS_TO, evidence, actor) for k in keys],
            )
        return len(keys)

    async def clear_manual_owner(self, entities: Sequence[str]) -> int:
        """Apaga o dono manual (a entidade volta para o que a inferência disser)."""
        res = await self._pool.execute(
            f"DELETE FROM {self.table} WHERE type = $1 AND origin = 'manual' AND src = ANY($2::text[])",
            BELONGS_TO, sorted(set(entities)),
        )
        return int(res.split()[-1])

    # ------------------------------------------------------------ genérico: tudo ligado a uma entidade

    async def neighbors(self, key: str, *, type_: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Relações efetivas que saem de ou chegam a `key`, agrupadas por (outra ponta, tipo, direção)."""
        rows = await self._pool.fetch(
            f"""SELECT CASE WHEN e.src = $1 THEN e.dst ELSE e.src END AS other, e.type,
                       CASE WHEN e.src = $1 THEN 'out' ELSE 'in' END AS direction,
                       array_agg(DISTINCT e.origin) AS origins, (array_agg(e.evidence))[1] AS evidence
                FROM {self.effective} e
                WHERE (e.src = $1 OR e.dst = $1) AND ($2::text IS NULL OR e.type = $2)
                GROUP BY 1, 2, 3 ORDER BY 2, 1 LIMIT $3""",
            key, type_, max(1, min(limit, 1000)),
        )
        return [{"entity": r["other"], "type": r["type"], "direction": r["direction"],
                 "origins": sort_origins(r["origins"]), "evidence": json.loads(r["evidence"])} for r in rows]

    # ------------------------------------------------------------ estatísticas

    async def has_derived(self) -> bool:
        return bool(await self._pool.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {self.table} WHERE NOT (origin = ANY($1::text[])))", sorted(STORED)))

    async def stats(self) -> dict[str, Any]:
        rows = await self._pool.fetch(
            f"SELECT type, origin, status, action, count(*) AS n FROM {self.table} GROUP BY 1, 2, 3, 4 ORDER BY 1, 2")
        effective = await self._pool.fetch(f"SELECT type, count(*) AS n FROM {self.effective} GROUP BY 1")
        return {"rows": [dict(r) for r in rows], "effective": {r["type"]: int(r["n"]) for r in effective}}


def _impl_row(r: asyncpg.Record) -> dict[str, Any]:
    ev = json.loads(r["evidence"])
    return {"domain": ev.get("domain"), "service": ev.get("service"), "operation": ev.get("operation"),
            "kind": ev.get("kind"), "repo": ev.get("repo"), "symbol": ev.get("symbol"), "path": ev.get("path"),
            "line": int(ev.get("line") or 0), "origin": r["origin"]}
