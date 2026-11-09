"""Persistência de pessoas (`users`) e das preferências que elas mudaram (`user_settings`)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg

from ..settings import Settings


class UserStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._users, self._settings_t = settings.qualified("users"), settings.qualified("user_settings")

    async def record_login(self, email: str, display_name: str | None = None) -> None:
        """Cria a pessoa no primeiro login; nos seguintes, atualiza o nome (se veio) e a data."""
        await self._pool.execute(
            f"""INSERT INTO {self._users} (email, display_name, last_login_at) VALUES ($1, $2, now())
                ON CONFLICT (email) DO UPDATE
                SET display_name = COALESCE(EXCLUDED.display_name, {self._users}.display_name),
                    last_login_at = now()""",
            email.lower(), display_name,
        )

    async def get(self, email: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"SELECT id::text, email, display_name, created_at, last_login_at FROM {self._users} WHERE email = $1",
            email.lower(),
        )
        return dict(row) if row else None

    async def all(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"""SELECT u.email, u.display_name, u.created_at, u.last_login_at,
                       (SELECT count(*) FROM {self._settings_t} s WHERE s.user_id = u.id) AS changed
                FROM {self._users} u ORDER BY u.email""",
        )
        return [dict(r) for r in rows]

    async def settings(self, email: str) -> dict[str, Any]:
        """Só o que a pessoa mudou (chave → valor)."""
        rows = await self._pool.fetch(
            f"""SELECT s.key, s.value FROM {self._settings_t} s
                JOIN {self._users} u ON u.id = s.user_id WHERE u.email = $1""",
            email.lower(),
        )
        return {r["key"]: json.loads(r["value"]) for r in rows}

    async def apply_settings(self, email: str, to_set: Mapping[str, Any], to_unset: Sequence[str]) -> None:
        """Grava e apaga numa transação. Cria a pessoa se ainda não existe."""
        async with self._pool.acquire() as conn, conn.transaction():
            user_id = await conn.fetchval(
                f"""INSERT INTO {self._users} (email) VALUES ($1)
                    ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id""",
                email.lower(),
            )
            for key, value in to_set.items():
                await conn.execute(
                    f"""INSERT INTO {self._settings_t} (user_id, key, value) VALUES ($1, $2, $3::jsonb)
                        ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                    user_id, key, json.dumps(value),
                )
            if to_unset:
                await conn.execute(
                    f"DELETE FROM {self._settings_t} WHERE user_id = $1 AND key = ANY($2::text[])",
                    user_id, list(to_unset),
                )
