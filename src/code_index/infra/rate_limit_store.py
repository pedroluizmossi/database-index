"""Contagem do rate limit no Postgres (`rate_limits`), compartilhada entre as réplicas da API.

Janela fixa: o início da janela é o instante do banco arredondado para baixo a um múltiplo de `window_s`
(todas as réplicas usam o relógio do Postgres, não o do pod). Cada evento é um único
`INSERT ... ON CONFLICT DO UPDATE SET hits = hits + 1 RETURNING hits`.
"""

from __future__ import annotations

import asyncpg

from ..settings import Settings


class RateLimitStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = settings.qualified("rate_limits")

    async def hit(self, bucket: str, key: str, *, window_s: int) -> tuple[int, float]:
        """Conta um evento na janela atual de (bucket, key). Devolve (eventos na janela, segundos até ela acabar)."""
        row = await self._pool.fetchrow(
            f"""WITH w AS (SELECT to_timestamp(floor(extract(epoch FROM now()) / $3::int) * $3::int) AS start)
                INSERT INTO {self._t} AS r (bucket, key, window_start, expires_at, hits)
                SELECT $1, $2, w.start, w.start + make_interval(secs => $3::int), 1 FROM w
                ON CONFLICT (bucket, key, window_start) DO UPDATE SET hits = r.hits + 1
                RETURNING r.hits, extract(epoch FROM r.expires_at - now())::float8 AS remaining""",
            bucket, key, window_s,
        )
        assert row is not None
        return int(row["hits"]), max(0.0, float(row["remaining"]))

    async def purge(self) -> int:
        """Apaga as janelas que já acabaram. Devolve quantas linhas saíram."""
        res = await self._pool.execute(f"DELETE FROM {self._t} WHERE expires_at < now()")
        return int(res.split()[-1])
