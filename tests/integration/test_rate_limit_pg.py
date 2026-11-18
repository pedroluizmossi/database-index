"""Rate limit no Postgres: contagem compartilhada entre réplicas, janela fixa, limpeza e fail-open."""

from __future__ import annotations

import pytest

from code_index.infra.db import open_pool
from code_index.infra.rate_limit_store import RateLimitStore
from code_index.settings import Settings

pytestmark = pytest.mark.integration


def open_service(settings: Settings):  # type: ignore[no-untyped-def]
    from code_index.services import open_service as _open

    return _open(settings)


async def test_contagem_compartilhada_entre_replicas(int_settings: Settings) -> None:
    # Duas "réplicas" = dois serviços (dois pools) no mesmo banco: o limite vale para a soma.
    async with open_service(int_settings) as a, open_service(int_settings) as b:
        assert await a.rate_limit_hit("search", "tok-1", limit=3, window=3600) is None
        assert await b.rate_limit_hit("search", "tok-1", limit=3, window=3600) is None
        assert await a.rate_limit_hit("search", "tok-1", limit=3, window=3600) is None
        retry = await b.rate_limit_hit("search", "tok-1", limit=3, window=3600)
        assert retry is not None and 0 < retry <= 3600
        assert await a.rate_limit_hit("search", "tok-2", limit=3, window=3600) is None  # outra chave
        assert await a.rate_limit_hit("read", "tok-1", limit=3, window=3600) is None  # outro balde


async def test_janela_alinhada_e_limpeza(int_settings: Settings) -> None:
    async with open_service(int_settings), open_pool(int_settings) as pool:
        store = RateLimitStore(pool, int_settings)
        hits, remaining = await store.hit("sync", "tok-1", window_s=3600)
        assert hits == 1 and 0 < remaining <= 3600
        assert (await store.hit("sync", "tok-1", window_s=3600))[0] == 2
        t = int_settings.qualified("rate_limits")
        row = await pool.fetchrow(f"SELECT window_start, expires_at, key FROM {t} WHERE bucket = 'sync'")
        assert row["key"] == "tok-1"
        assert int(row["window_start"].timestamp()) % 3600 == 0  # mesma janela em todas as réplicas
        assert (row["expires_at"] - row["window_start"]).total_seconds() == 3600
        await pool.execute(f"INSERT INTO {t} (bucket, key, window_start, expires_at, hits) "
                           "VALUES ('read', 'velho', now() - interval '2 hours', now() - interval '1 hour', 9)")
        assert await store.purge() == 1
        assert await pool.fetchval(f"SELECT count(*) FROM {t}") == 1


async def test_banco_fora_deixa_passar(int_settings: Settings) -> None:
    async with open_service(int_settings) as svc:
        await svc._rate_limits._pool.execute(f"DROP TABLE {int_settings.qualified('rate_limits')}")
        assert await svc.rate_limit_hit("search", "tok-1", limit=0, window=60) is None  # fail-open
