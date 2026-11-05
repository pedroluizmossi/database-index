"""Tabelas do enriquecimento offline: `repo_cards` (fatos + cartão) e `enrich_tasks` (fila de tarefas).

A fila não é uma tabela de pendências: pendente = repo indexado cujo cartão não corresponde ao material
atual (`card_input_hash <> input_hash`) e sem reserva ativa. Uma reserva (lease) expira sozinha.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ..settings import Settings
from ..domain.documents import WIKI_PAGE
from ..domain.relations import doc_key
from .relation_store import RelationStore

TEXT_TYPES = (WIKI_PAGE,)  # documentos que agents revisam (páginas de texto)


@dataclass(frozen=True)
class EnrichTask:
    id: str
    kind: str
    repo: str
    input_hash: str
    candidates: list[str]
    agent: str
    status: str
    lease_until: datetime
    rejections: int
    last_error: str | None
    created_at: datetime
    subject: str = ""
    payload: dict[str, Any] | None = None


def _task(row: asyncpg.Record) -> EnrichTask:
    payload = row["payload"] if "payload" in row.keys() else None
    return EnrichTask(
        subject=row["subject"] if "subject" in row.keys() else "",
        payload=json.loads(payload) if payload else {},
        id=str(row["id"]), kind=row["kind"], repo=row["repo"], input_hash=row["input_hash"],
        candidates=json.loads(row["candidates"]), agent=row["agent"], status=row["status"],
        lease_until=row["lease_until"], rejections=row["rejections"], last_error=row["last_error"],
        created_at=row["created_at"],
    )


_ACCENTS_FROM = "áàâãäéèêëíìîïóòôõöúùûüçñ"
_ACCENTS_TO = "aaaaaeeeeiiiiooooouuuucn"


def _fold(sql_text: str) -> str:
    """Expressão SQL: minúsculas, sem acento e sem hífen (para casar 'notificação' com 'notificacao')."""
    return f"translate(lower({sql_text}), '{_ACCENTS_FROM}-_', '{_ACCENTS_TO}  ')"


def _arr(path: str) -> str:
    return f"coalesce(array_to_string(ARRAY(SELECT jsonb_array_elements_text({path})), ' '), '')"


def material_hash_pages(pages: list[dict[str, Any]]) -> str:
    return "|".join(f"{p['path']}@{p['updated_at']}" for p in pages)[:2000]


def _jsonb(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class EnrichStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._cards = f'"{settings.pg_schema}".repo_cards'
        self._tasks = f'"{settings.pg_schema}".enrich_tasks'
        self._repos = f'"{settings.pg_schema}".repos'
        self._impls = f'"{settings.pg_schema}".api_impls'
        self._settings = settings

    # ------------------------------------------------------------ fatos / material

    async def revisions(self) -> dict[str, str | None]:
        """repo → revisão em que o material foi coletado."""
        rows = await self._pool.fetch(f"SELECT repo, revision FROM {self._cards}")
        return {r["repo"]: r["revision"] for r in rows}

    async def save_material(self, repo: str, *, revision: str | None, commit: str | None,
                            input_hash: str, material: dict[str, Any]) -> None:
        await self._pool.execute(
            f"""INSERT INTO {self._cards} (repo, revision, commit, input_hash, material, facts_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, now())
                ON CONFLICT (repo) DO UPDATE SET revision = EXCLUDED.revision, commit = EXCLUDED.commit,
                    input_hash = EXCLUDED.input_hash, material = EXCLUDED.material, facts_at = now()""",
            repo, revision, commit, input_hash, _jsonb(material),
        )

    async def has_cards(self) -> bool:
        return bool(await self._pool.fetchval(f"SELECT EXISTS (SELECT 1 FROM {self._cards})"))

    async def all_facts(self) -> dict[str, dict[str, Any]]:
        rows = await self._pool.fetch(f"SELECT repo, material->'facts' AS facts FROM {self._cards}")
        return {r["repo"]: json.loads(r["facts"]) if r["facts"] else {} for r in rows}

    async def get(self, repo: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(f"SELECT * FROM {self._cards} WHERE repo = $1", repo)
        if row is None:
            return None
        out = dict(row)
        out["material"] = json.loads(row["material"])
        out["card"] = json.loads(row["card"]) if row["card"] else None
        return out

    async def cards(self, repos: list[str]) -> dict[str, dict[str, Any]]:
        """Cartões prontos (pode estar desatualizado; `stale` diz se o material mudou)."""
        rows = await self._pool.fetch(
            f"SELECT repo, card, card_input_hash <> input_hash AS stale FROM {self._cards} "
            "WHERE repo = ANY($1::text[]) AND card IS NOT NULL", repos,
        )
        return {r["repo"]: {**json.loads(r["card"]), "stale": r["stale"]} for r in rows}

    async def find(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Repos cujo cartão (ou fatos: nome, SDL) casa com a consulta. Full-text do Postgres, sem LLM.
        Pesos: nome e tags (A) > capacidades, glossário e SDL (B) > resumo (C). Termos em OU."""
        doc = f"""
            setweight(to_tsvector('portuguese', {_fold("replace(c.repo, '__', ' ')")}), 'A') ||
            setweight(to_tsvector('portuguese', {_fold(_arr("c.card->'domain_tags'"))}), 'A') ||
            setweight(to_tsvector('portuguese', {_fold(_arr("c.card->'capabilities'") + " || ' ' || " + _arr("c.card->'glossary'"))}), 'B') ||
            setweight(to_tsvector('portuguese', {_fold("coalesce((SELECT string_agg((s->>'service') || ' ' || "
                                                        "array_to_string(ARRAY(SELECT jsonb_array_elements_text(s->'queries') "
                                                        "UNION ALL SELECT jsonb_array_elements_text(s->'actions')), ' '), ' ') "
                                                        "FROM jsonb_array_elements(c.material->'facts'->'sdl') s), '')")}), 'B') ||
            setweight(to_tsvector('portuguese', {_fold("coalesce(c.card->>'summary', '')")}), 'C')"""
        rows = await self._pool.fetch(
            f"""WITH q AS (
                    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS q
                    FROM unnest(to_tsvector('portuguese', {_fold("$1::text")}))
                )
                SELECT c.repo, c.card, c.card IS NOT NULL AND c.card_input_hash <> c.input_hash AS stale,
                       ts_rank_cd({doc}, q.q, 1) AS score
                FROM {self._cards} c JOIN {self._repos} r ON r.name = c.repo, q
                WHERE q.q IS NOT NULL AND NOT r.ignored AND ({doc}) @@ q.q
                ORDER BY score DESC, c.repo LIMIT $2""",
            query, max(1, min(limit, 50)),
        )
        out = []
        for r in rows:
            card = json.loads(r["card"]) if r["card"] else {}
            out.append({"repo": r["repo"], "score": round(float(r["score"]), 4), "summary": card.get("summary"),
                        "kind": card.get("kind"), "tags": card.get("domain_tags", []), "stale": r["stale"]})
        return out

    async def sdl_operation_count(self) -> int:
        """Operações (query/action/signal) declaradas nos .sdl dos repos."""
        return int(await self._pool.fetchval(
            f"""SELECT coalesce(sum(jsonb_array_length(coalesce(s->'queries', '[]'::jsonb))
                                 + jsonb_array_length(coalesce(s->'actions', '[]'::jsonb))
                                 + jsonb_array_length(coalesce(s->'signals', '[]'::jsonb))), 0)
                FROM {self._cards} c, jsonb_array_elements(c.material->'facts'->'sdl') s"""))

    # ------------------------------------------------------------ fila

    async def expire_leases(self) -> int:
        res = await self._pool.execute(
            f"UPDATE {self._tasks} SET status = 'expired', finished_at = now() "
            "WHERE status = 'leased' AND lease_until < now()"
        )
        return int(res.split()[-1])

    def _pending_sql(self) -> str:
        return f"""FROM {self._cards} c JOIN {self._repos} r ON r.name = c.repo
                   WHERE r.kind = 'code' AND NOT r.ignored AND r.revision IS NOT NULL
                     AND c.card_input_hash IS DISTINCT FROM c.input_hash
                     AND NOT EXISTS (SELECT 1 FROM {self._tasks} t
                                     WHERE t.kind = 'card' AND t.repo = c.repo AND t.status = 'leased')"""

    async def pending_count(self) -> int:
        return int(await self._pool.fetchval(f"SELECT count(*) {self._pending_sql()}"))

    async def lease_next(self, *, agent: str, lease_minutes: int, repo: str | None = None) -> EnrichTask | None:
        """Reserva o próximo repo pendente (sem cartão primeiro). Seguro com vários agents em paralelo."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                where = " AND c.repo = $1" if repo else ""
                row = await conn.fetchrow(
                    f"SELECT c.repo, c.input_hash {self._pending_sql()}{where} "
                    "ORDER BY (c.card IS NOT NULL), c.repo LIMIT 1 FOR UPDATE OF c SKIP LOCKED",
                    *([repo] if repo else []),
                )
                if row is None:
                    return None
                task = await conn.fetchrow(
                    f"""INSERT INTO {self._tasks} (kind, repo, input_hash, agent, status, lease_until)
                        VALUES ('card', $1, $2, $3, 'leased', now() + make_interval(mins => $4))
                        ON CONFLICT DO NOTHING RETURNING *""",
                    row["repo"], row["input_hash"], agent, lease_minutes,
                )
        return _task(task) if task else None

    async def set_candidates(self, task_id: str, candidates: list[str]) -> None:
        await self._pool.execute(f"UPDATE {self._tasks} SET candidates = $2::jsonb WHERE id = $1::uuid",
                                 task_id, _jsonb(candidates))

    async def task(self, task_id: str) -> EnrichTask | None:
        try:
            row = await self._pool.fetchrow(f"SELECT * FROM {self._tasks} WHERE id = $1::uuid", task_id)
        except asyncpg.DataError:  # id que não é uuid
            return None
        return _task(row) if row else None

    async def reject(self, task_id: str, error: str) -> None:
        await self._pool.execute(
            f"UPDATE {self._tasks} SET rejections = rejections + 1, last_error = $2 WHERE id = $1::uuid",
            task_id, error[:2000],
        )

    async def complete_card(self, task: EnrichTask, card: dict[str, Any], agent: str) -> bool:
        """Grava o cartão e fecha a tarefa. False se outra tarefa do mesmo repo está reservada."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                other = await conn.fetchval(
                    f"SELECT 1 FROM {self._tasks} WHERE kind = 'card' AND repo = $1 AND status = 'leased' "
                    "AND id <> $2::uuid", task.repo, task.id,
                )
                if other:
                    return False
                await conn.execute(
                    f"""UPDATE {self._cards} SET card = $2::jsonb, card_input_hash = $3, card_agent = $4,
                            card_at = now() WHERE repo = $1""",
                    task.repo, _jsonb(card), task.input_hash, agent,
                )
                await conn.execute(
                    f"UPDATE {self._tasks} SET status = 'done', finished_at = now(), last_error = NULL, "
                    "agent = CASE WHEN $2 = '' THEN agent ELSE $2 END WHERE id = $1::uuid",
                    task.id, agent,
                )
        return True

    async def release(self, task_id: str) -> bool:
        res = await self._pool.execute(
            f"UPDATE {self._tasks} SET status = 'released', finished_at = now() "
            "WHERE id = $1::uuid AND status = 'leased'", task_id,
        )
        return res.endswith(" 1")

    # ------------------------------------------------------------ fila de páginas da wiki → repos

    def _pending_pages_sql(self, scope: str = "todas") -> str:
        """Páginas de texto ainda não analisadas (ou que mudaram desde a análise), sem reserva ativa.
        scope: "todas"; "sem-vinculo" (nenhum vínculo nem sugestão); "varios" (ligadas a 2+ repos)."""
        s = self._settings
        docs, rel, reviews = f'"{s.pg_schema}".documents', f'"{s.pg_schema}".relations', f'"{s.pg_schema}".enrich_reviews'
        links = RelationStore(self._pool, s).doc_links_sql()
        if scope == "sem-vinculo":
            scope_sql = f"""AND NOT EXISTS (SELECT 1 FROM linked x WHERE x.doc_repo = d.doc_repo AND x.doc_path = d.path)
              AND NOT EXISTS (SELECT 1 FROM {rel} r WHERE r.src = d.key AND r.type = 'documents'
                              AND r.status IN ('active', 'suggested') AND r.action = 'add')"""
        elif scope == "varios":
            scope_sql = "AND (SELECT count(DISTINCT repo) FROM linked x WHERE x.doc_repo = d.doc_repo AND x.doc_path = d.path) > 1"
        elif scope == "todas":
            scope_sql = ""
        else:
            raise ValueError("escopo inválido: todas, sem-vinculo ou varios")
        return f"""
            WITH linked AS ({links})
            SELECT d.doc_repo AS repo, d.path, d.key, d.title, d.tags, d.url, d.source_version AS updated_at
            FROM {docs} d
            WHERE d.present AND NOT d.ignored AND d.type = ANY('{{{",".join(TEXT_TYPES)}}}'::text[])
              {scope_sql}
              AND NOT EXISTS (SELECT 1 FROM {reviews} v WHERE v.kind = 'doc_links' AND v.entity = d.key
                              AND v.input_hash = d.source_version)
              AND NOT EXISTS (SELECT 1 FROM {self._tasks} t WHERE t.kind = 'doc_links' AND t.status = 'leased'
                              AND t.repo = d.doc_repo
                              AND t.payload->'pages' @> jsonb_build_array(jsonb_build_object('path', d.path)))"""

    async def pending_pages(self, doc_repo: str | None = None, *, scope: str = "todas") -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT * FROM ({self._pending_pages_sql(scope)}) q WHERE ($1::text IS NULL OR q.repo = $1) "
            "ORDER BY q.repo, q.path", doc_repo,
        )
        return [{**dict(r), "tags": json.loads(r["tags"])} for r in rows]

    async def lease_doc_batch(self, *, agent: str, lease_minutes: int, doc_repo: str, folder: str,
                              pages: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> EnrichTask | None:
        """Reserva um lote de páginas de uma pasta. None se outra reserva da mesma pasta está ativa.
        `pages[i]["links"]`: vínculos atuais {repo: [origens]} (guardados para validar a entrega)."""
        payload = {"pages": [{"path": p["path"], "hash": p["updated_at"], "links": p.get("links", {})} for p in pages],
                   "candidates": candidates}
        row = await self._pool.fetchrow(
            f"""INSERT INTO {self._tasks} (kind, repo, subject, input_hash, payload, agent, status, lease_until)
                VALUES ('doc_links', $1, $2, $3, $4::jsonb, $5, 'leased', now() + make_interval(mins => $6))
                ON CONFLICT DO NOTHING RETURNING *""",
            doc_repo, folder, material_hash_pages(pages), _jsonb(payload), agent, lease_minutes,
        )
        return _task(row) if row else None

    async def complete_doc_task(self, conn: asyncpg.Connection, task: EnrichTask, results: list[dict[str, Any]],
                                agent: str) -> None:
        """Registra a análise de cada página (não volta para a fila enquanto não mudar) e fecha a tarefa."""
        reviews = f'"{self._settings.pg_schema}".enrich_reviews'
        hashes = {p["path"]: p["hash"] for p in (task.payload or {}).get("pages", [])}
        for page in results:
            await conn.execute(
                f"""INSERT INTO {reviews} (kind, entity, input_hash, agent, result) VALUES ('doc_links', $1, $2, $3, $4::jsonb)
                    ON CONFLICT (kind, entity) DO UPDATE SET input_hash = EXCLUDED.input_hash, agent = EXCLUDED.agent,
                        result = EXCLUDED.result, created_at = now()""",
                doc_key(task.repo, page["path"]), hashes.get(page["path"], ""), agent, _jsonb(page),
            )
        await conn.execute(
            f"UPDATE {self._tasks} SET status = 'done', finished_at = now(), last_error = NULL, "
            "agent = CASE WHEN $2 = '' THEN agent ELSE $2 END WHERE id = $1::uuid", task.id, agent,
        )

    async def doc_queue_stats(self) -> dict[str, int]:
        pending = {}
        for scope in ("todas", "sem-vinculo", "varios"):
            pending[scope] = int(await self._pool.fetchval(f"SELECT count(*) FROM ({self._pending_pages_sql(scope)}) q"))
        reviewed = await self._pool.fetchval(
            f"""SELECT count(*) FROM "{self._settings.pg_schema}".enrich_reviews WHERE kind = 'doc_links'""")
        return {"pending_pages": pending["todas"], "pending_unlinked": pending["sem-vinculo"],
                "pending_multi": pending["varios"], "reviewed": int(reviewed)}

    async def status(self) -> dict[str, Any]:
        row = await self._pool.fetchrow(
            f"""SELECT count(*) AS repos,
                       count(*) FILTER (WHERE c.repo IS NOT NULL) AS with_facts,
                       count(*) FILTER (WHERE c.card IS NOT NULL AND c.card_input_hash = c.input_hash) AS cards_ok,
                       count(*) FILTER (WHERE c.card IS NOT NULL AND c.card_input_hash <> c.input_hash) AS cards_stale,
                       count(*) FILTER (WHERE c.repo IS NOT NULL AND c.card IS NULL) AS cards_missing,
                       count(*) FILTER (WHERE jsonb_array_length(c.material->'facts'->'sdl') > 0) AS with_sdl
                FROM {self._repos} r LEFT JOIN {self._cards} c ON c.repo = r.name
                WHERE r.kind = 'code' AND NOT r.ignored AND r.revision IS NOT NULL"""
        )
        tasks = await self._pool.fetch(
            f"SELECT status, count(*) AS n, sum(rejections) AS rejections FROM {self._tasks} "
            "GROUP BY status"
        )
        leased = await self._pool.fetch(
            f"SELECT id, kind, repo, subject, agent, lease_until FROM {self._tasks} WHERE status = 'leased' "
            "ORDER BY created_at"
        )
        return {
            **dict(row),
            "tasks": {r["status"]: int(r["n"]) for r in tasks},
            "rejections": sum(int(r["rejections"] or 0) for r in tasks),
            "leased": [dict(r) | {"id": str(r["id"])} for r in leased],
        }
