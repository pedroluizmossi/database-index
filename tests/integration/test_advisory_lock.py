"""Trava de sessão no Postgres (infra/db.try_advisory_lock): exclusão entre conexões e perda da conexão."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest

from code_index.infra.db import AdvisoryLockLost, create_pool, try_advisory_lock
from code_index.settings import Settings

pytestmark = pytest.mark.integration


async def _scenario(settings: Settings) -> dict[str, Any]:
    pool = await create_pool(settings)
    out: dict[str, Any] = {}
    try:
        key = f"code_index.teste.{settings.pg_schema}"
        async with try_advisory_lock(pool, key) as got:
            async with try_advisory_lock(pool, key) as again:
                out["first"], out["second"] = got, again
        async with try_advisory_lock(pool, key) as got:
            out["after_release"] = got

        # conexão da trava derrubada no servidor: o bloco é interrompido com AdvisoryLockLost e a trava fica livre
        try:
            async with try_advisory_lock(pool, key, heartbeat_s=0.2) as got:
                pid = await pool.fetchval(
                    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid <> pg_backend_pid() "
                    "LIMIT 1")
                conn = await asyncpg.connect(settings.database_url)
                try:
                    await conn.execute("SELECT pg_terminate_backend($1)", pid)
                finally:
                    await conn.close()
                await asyncio.sleep(5)
                out["lost"] = False
        except AdvisoryLockLost:
            out["lost"] = True
        async with try_advisory_lock(pool, key) as got:
            out["after_lost"] = got
    finally:
        await pool.close()
    return out


def test_trava_exclusiva_e_perda_da_conexao(int_settings: Settings) -> None:
    out = asyncio.run(_scenario(int_settings))
    assert out == {"first": True, "second": False, "after_release": True, "lost": True, "after_lost": True}
