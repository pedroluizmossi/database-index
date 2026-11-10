"""Persistência de pessoas (`users`) e das preferências que elas mudaram (`user_settings`).

A pessoa é a entidade central: tokens (api_tokens.user_id) e eventos de uso (usage_events.user_id) apontam
para ela. Atributos novos de perfil entram em `attrs` (jsonb) sem migration; viram coluna quando precisarem
de índice ou de regra no banco.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg

from ..settings import Settings


_USER_COLS = "id::text, email, display_name, created_at, last_login_at, last_seen_at, disabled_at, attrs"


def _user(row: asyncpg.Record) -> dict[str, Any]:
    out = dict(row)
    attrs = out.get("attrs")
    out["attrs"] = json.loads(attrs) if isinstance(attrs, str) else (attrs or {})
    return out


class UserStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._users, self._settings_t = settings.qualified("users"), settings.qualified("user_settings")

    async def record_login(self, email: str, display_name: str | None = None) -> dict[str, Any]:
        """Cria a pessoa no primeiro login; nos seguintes, atualiza o nome (se veio) e as datas.
        Devolve a pessoa (com `disabled_at`, para quem chama recusar o login de quem foi desativado)."""
        row = await self._pool.fetchrow(
            f"""INSERT INTO {self._users} (email, display_name, last_login_at, last_seen_at)
                VALUES ($1, $2, now(), now())
                ON CONFLICT (email) DO UPDATE
                SET display_name = COALESCE(EXCLUDED.display_name, {self._users}.display_name),
                    last_login_at = now(), last_seen_at = now()
                RETURNING {_USER_COLS}""",
            email.lower(), display_name,
        )
        return _user(row)

    async def get(self, email: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(f"SELECT {_USER_COLS} FROM {self._users} WHERE email = $1", email.lower())
        return _user(row) if row else None

    async def set_disabled(self, email: str, disabled: bool) -> dict[str, Any] | None:
        """Desativa (todas as credenciais param de valer) ou reativa a pessoa. None se ela não existe."""
        row = await self._pool.fetchrow(
            f"""UPDATE {self._users} SET disabled_at = CASE WHEN $2 THEN COALESCE(disabled_at, now()) END
                WHERE email = $1 RETURNING {_USER_COLS}""",
            email.lower(), disabled,
        )
        return _user(row) if row else None

    async def update_attrs(self, email: str, changes: Mapping[str, Any]) -> dict[str, Any] | None:
        """Mescla atributos livres do perfil; valor None remove a chave."""
        to_set = {k: v for k, v in changes.items() if v is not None}
        to_unset = [k for k, v in changes.items() if v is None]
        row = await self._pool.fetchrow(
            f"""UPDATE {self._users} SET attrs = (attrs || $2::jsonb) - $3::text[]
                WHERE email = $1 RETURNING {_USER_COLS}""",
            email.lower(), json.dumps(to_set), to_unset,
        )
        return _user(row) if row else None

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
