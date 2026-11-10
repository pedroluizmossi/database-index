"""Persistência e relatórios dos eventos de uso (`usage_events`).

Os relatórios agregam no banco (um SELECT por bloco do dashboard) sobre uma janela de `days` dias, opcionalmente
de uma só pessoa (`user_id`). O dia é contado no fuso do servidor (`tz_offset_s`).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

from ..domain.usage import QUERY_PARAMS, QUERY_WRITE_ACTIONS, UsageEvent
from ..settings import Settings


def _json(value: Any) -> str | None:
    return json.dumps(value, default=str, ensure_ascii=False) if value else None


def _rows(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [{k: (v.isoformat(timespec="seconds") if isinstance(v, datetime) else v) for k, v in dict(r).items()}
            for r in rows]


class UsageStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = settings.qualified("usage_events")
        self._users = settings.qualified("users")
        self._tokens = settings.qualified("api_tokens")

    async def record(self, ev: UsageEvent, *, user_id: str | None) -> None:
        await self._pool.execute(
            f"""INSERT INTO {self._t}
                    (user_id, token_id, token_kind, channel, action, client, client_version, status, duration_ms,
                     result_count, response_bytes, embed_tokens, rerank_tokens, cost_usd, params, attrs)
                VALUES (COALESCE($1::uuid, (SELECT id FROM {self._users} WHERE email = $17)), $2::uuid, $3, $4, $5,
                        $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb, $16::jsonb)""",
            user_id, ev.token_id, ev.token_kind, ev.channel, ev.action, ev.client, ev.client_version, ev.status,
            ev.duration_ms, ev.result_count, ev.response_bytes, ev.llm.embed_tokens, ev.llm.rerank_tokens,
            ev.llm.cost_usd, _json(ev.params), _json(ev.attrs), (ev.user_email or "").lower(),
        )

    async def purge(self, older_than_days: int) -> int:
        res = await self._pool.execute(
            f"DELETE FROM {self._t} WHERE at < now() - make_interval(days => $1)", older_than_days
        )
        return int(res.split()[-1])

    # ------------------------------------------------------------ relatórios

    @staticmethod
    def _where(days: int, user_id: str | None, first_arg: int = 1) -> tuple[str, list[Any]]:
        args: list[Any] = [days]
        where = f"e.at >= now() - make_interval(days => ${first_arg})"
        if user_id:
            args.append(user_id)
            where += f" AND e.user_id = ${first_arg + 1}::uuid"
        return where, args

    async def totals(self, days: int, user_id: str | None = None) -> dict[str, Any]:
        where, args = self._where(days, user_id)
        row = await self._pool.fetchrow(
            f"""SELECT count(*) AS calls,
                       count(*) FILTER (WHERE e.status >= 400) AS errors,
                       count(DISTINCT e.user_id) AS users,
                       count(DISTINCT e.client) AS clients,
                       count(*) FILTER (WHERE e.channel = 'mcp') AS mcp_calls,
                       COALESCE(avg(e.duration_ms), 0)::float AS avg_ms,
                       COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms), 0)::float AS p95_ms,
                       COALESCE(sum(e.response_bytes), 0)::bigint AS response_bytes,
                       COALESCE(sum(e.response_bytes) FILTER (WHERE e.channel = 'mcp'), 0)::bigint AS context_bytes,
                       COALESCE(sum(e.embed_tokens), 0)::bigint AS embed_tokens,
                       COALESCE(sum(e.rerank_tokens), 0)::bigint AS rerank_tokens,
                       COALESCE(sum(e.cost_usd), 0)::float AS cost_usd,
                       count(*) FILTER (WHERE e.result_count = 0 AND e.status < 400) AS empty_results
                FROM {self._t} e WHERE {where}""",
            *args,
        )
        return dict(row) if row else {}

    async def daily(self, days: int, tz_offset_s: int, user_id: str | None = None) -> list[dict[str, Any]]:
        where, args = self._where(days, user_id, first_arg=2)
        rows = await self._pool.fetch(
            f"""SELECT to_char((e.at AT TIME ZONE 'UTC' + make_interval(secs => $1))::date, 'YYYY-MM-DD') AS day,
                       count(*) FILTER (WHERE e.channel = 'mcp') AS mcp,
                       count(*) FILTER (WHERE e.channel = 'api') AS api,
                       count(*) FILTER (WHERE e.channel = 'ui') AS ui,
                       count(*) FILTER (WHERE e.status >= 400) AS errors,
                       count(DISTINCT e.user_id) AS users,
                       COALESCE(sum(e.cost_usd), 0)::float AS cost_usd,
                       COALESCE(sum(e.response_bytes) FILTER (WHERE e.channel = 'mcp'), 0)::bigint AS context_bytes
                FROM {self._t} e WHERE {where} GROUP BY 1 ORDER BY 1""",
            tz_offset_s, *args,
        )
        return _rows(rows)

    async def by_action(self, days: int, user_id: str | None = None) -> list[dict[str, Any]]:
        where, args = self._where(days, user_id)
        rows = await self._pool.fetch(
            f"""SELECT e.channel, e.action, count(*) AS calls,
                       count(*) FILTER (WHERE e.status >= 400) AS errors,
                       count(DISTINCT e.user_id) AS users,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.duration_ms)::float AS p50_ms,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY e.duration_ms)::float AS p95_ms,
                       avg(e.result_count)::float AS avg_results,
                       count(*) FILTER (WHERE e.result_count = 0 AND e.status < 400) AS empty_results,
                       COALESCE(sum(e.response_bytes), 0)::bigint AS response_bytes,
                       COALESCE(avg(e.response_bytes), 0)::float AS avg_response_bytes,
                       COALESCE(sum(e.embed_tokens), 0)::bigint AS embed_tokens,
                       COALESCE(sum(e.rerank_tokens), 0)::bigint AS rerank_tokens,
                       COALESCE(sum(e.cost_usd), 0)::float AS cost_usd,
                       max(e.at) AS last_at
                FROM {self._t} e WHERE {where}
                GROUP BY e.channel, e.action ORDER BY calls DESC""",
            *args,
        )
        return _rows(rows)

    async def by_client(self, days: int, user_id: str | None = None) -> list[dict[str, Any]]:
        where, args = self._where(days, user_id)
        rows = await self._pool.fetch(
            f"""SELECT COALESCE(e.client, '(desconhecido)') AS client, e.channel,
                       count(*) AS calls, count(DISTINCT e.user_id) AS users,
                       (array_agg(e.client_version ORDER BY e.at DESC)
                            FILTER (WHERE e.client_version IS NOT NULL))[1] AS last_version,
                       max(e.at) AS last_at
                FROM {self._t} e WHERE {where}
                GROUP BY 1, 2 ORDER BY calls DESC LIMIT 50""",
            *args,
        )
        return _rows(rows)

    async def by_user(self, days: int) -> list[dict[str, Any]]:
        """Todas as pessoas (inclusive sem uso no período), com o uso na janela."""
        rows = await self._pool.fetch(
            f"""WITH ev AS (
                    SELECT e.user_id, count(*) AS calls,
                           count(*) FILTER (WHERE e.channel = 'mcp') AS mcp_calls,
                           count(*) FILTER (WHERE e.channel = 'api') AS api_calls,
                           count(*) FILTER (WHERE e.channel = 'ui') AS ui_calls,
                           count(*) FILTER (WHERE e.status >= 400) AS errors,
                           COALESCE(sum(e.response_bytes) FILTER (WHERE e.channel = 'mcp'), 0)::bigint AS context_bytes,
                           COALESCE(sum(e.embed_tokens + e.rerank_tokens), 0)::bigint AS llm_tokens,
                           COALESCE(sum(e.cost_usd), 0)::float AS cost_usd,
                           max(e.at) AS last_at,
                           mode() WITHIN GROUP (ORDER BY e.action) AS top_action,
                           (array_agg(DISTINCT e.client) FILTER (WHERE e.client IS NOT NULL)) AS clients
                    FROM {self._t} e WHERE e.at >= now() - make_interval(days => $1) GROUP BY e.user_id
                ), tk AS (
                    SELECT user_id, count(*) AS active_tokens FROM {self._tokens}
                    WHERE kind IN ('agent', 'oauth') AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
                    GROUP BY user_id
                )
                SELECT u.id::text AS id, u.email, u.display_name, u.created_at, u.last_login_at, u.last_seen_at,
                       u.disabled_at, COALESCE(tk.active_tokens, 0) AS active_tokens,
                       COALESCE(ev.calls, 0) AS calls, COALESCE(ev.mcp_calls, 0) AS mcp_calls,
                       COALESCE(ev.api_calls, 0) AS api_calls, COALESCE(ev.ui_calls, 0) AS ui_calls,
                       COALESCE(ev.errors, 0) AS errors, COALESCE(ev.context_bytes, 0) AS context_bytes,
                       COALESCE(ev.llm_tokens, 0) AS llm_tokens, COALESCE(ev.cost_usd, 0) AS cost_usd,
                       ev.last_at, ev.top_action, COALESCE(ev.clients, '{{}}') AS clients
                FROM {self._users} u
                LEFT JOIN ev ON ev.user_id = u.id
                LEFT JOIN tk ON tk.user_id = u.id
                ORDER BY COALESCE(ev.calls, 0) DESC, u.email""",
            days,
        )
        return _rows(rows)

    async def top_queries(
        self, days: int, user_id: str | None = None, *, empty_only: bool = False, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Consultas mais repetidas (o texto procurado em busca, grep, símbolo...). `empty_only`: só as que
        não trouxeram nada — o melhor sinal do que falta no índice ou na busca."""
        where, args = self._where(days, user_id)
        keys = ", ".join(f"e.params->>'{k}'" for k in QUERY_PARAMS)
        extra = " AND e.result_count = 0 AND e.status < 400" if empty_only else ""
        args.append(list(QUERY_WRITE_ACTIONS))
        extra += f" AND (e.channel = 'mcp' OR e.action LIKE 'GET %' OR e.action = ANY(${len(args)}::text[]))"
        args.append(min(max(limit, 1), 200))
        rows = await self._pool.fetch(
            f"""SELECT e.action, lower(btrim(COALESCE({keys}))) AS query, count(*) AS calls,
                       count(DISTINCT e.user_id) AS users, avg(e.result_count)::float AS avg_results,
                       max(e.at) AS last_at
                FROM {self._t} e
                WHERE {where}{extra} AND COALESCE({keys}) IS NOT NULL
                GROUP BY 1, 2 ORDER BY calls DESC, last_at DESC LIMIT ${len(args)}""",
            *args,
        )
        return _rows(rows)

    async def recent(self, days: int, user_id: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        where, args = self._where(days, user_id)
        args.append(min(max(limit, 1), 500))
        rows = await self._pool.fetch(
            f"""SELECT e.id, e.at, u.email, e.channel, e.action, e.client, e.client_version, e.token_kind, e.status,
                       e.duration_ms, e.result_count, e.response_bytes, e.embed_tokens, e.rerank_tokens, e.cost_usd,
                       e.params
                FROM {self._t} e LEFT JOIN {self._users} u ON u.id = e.user_id
                WHERE {where} ORDER BY e.at DESC LIMIT ${len(args)}""",
            *args,
        )
        return [{**r, "params": json.loads(r["params"]) if r["params"] else None} for r in _rows(rows)]
