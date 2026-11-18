"""Rate limit compartilhado entre réplicas: contagem por janela fixa no Postgres (infra/rate_limit_store.py).

Semântica: no máximo `limit` eventos por (balde, chave) em cada janela fixa de `window` segundos (alinhada ao
relógio do banco). Diferente da antiga janela deslizante em memória, na virada da janela a contagem recomeça
(no pior caso, até 2 x `limit` em pouco tempo, metade em cada lado da virada).

Fail-open: se o banco falhar ou demorar mais que `rate_limit_db_timeout_s` (ou o store não existir), a requisição passa e a falha é registrada no log (no
máximo uma linha por minuto). Um Postgres fora do ar não deve derrubar a API por causa do rate limit; as rotas
que dependem do banco falham por conta própria.

Limpeza: as janelas vencidas são apagadas de vez em quando, numa task disparada pela requisição que conta (no
máximo uma vez a cada `RATE_LIMIT_PURGE_EVERY_S` por processo; a requisição não espera por ela), sem job extra.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .base import ServiceBase

log = logging.getLogger(__name__)

RATE_LIMIT_PURGE_EVERY_S = 300.0
_LOG_EVERY_S = 60.0


class RateLimitMixin(ServiceBase):
    _rate_purge_at: float = 0.0  # time.monotonic() da próxima limpeza (0: na primeira contagem)
    _rate_logged_at: float | None = None  # última falha registrada (evita uma linha de log por requisição)
    _rate_purge_task: "asyncio.Task[Any] | None" = None  # limpeza em andamento (referência: a task não some no GC)

    async def rate_limit_hit(self, bucket: str, key: str, *, limit: int, window: float) -> float | None:
        """Conta um evento de `key` no balde `bucket`. None se permitido; senão, os segundos até a janela acabar.
        `key` é o id do principal ou o IP, nunca um token em claro."""
        store = self._rate_limits
        if store is None:
            return None
        try:
            hits, remaining = await asyncio.wait_for(store.hit(bucket, key, window_s=max(1, int(window))),
                                                     self.settings.rate_limit_db_timeout_s)
        except Exception as e:  # noqa: BLE001 - fail-open (inclui TimeoutError): ver docstring do módulo
            self._rate_log_failure("rate limit indisponível; requisição liberada (%s: %s)", e)
            return None
        self._rate_maybe_purge()
        return None if hits <= limit else remaining

    def _rate_maybe_purge(self) -> None:
        now = time.monotonic()
        if now < self._rate_purge_at or self._rate_limits is None:
            return
        self._rate_purge_at = now + RATE_LIMIT_PURGE_EVERY_S  # requisições concorrentes não repetem
        if self._rate_purge_task is None or self._rate_purge_task.done():
            self._rate_purge_task = asyncio.create_task(self._rate_purge())

    async def _rate_purge(self) -> None:
        if self._rate_limits is None:
            return
        try:
            removed = await self._rate_limits.purge()
            if removed:
                log.debug("rate limit: %d janelas vencidas apagadas", removed)
        except Exception as e:  # noqa: BLE001 - tenta de novo na próxima vez
            self._rate_log_failure("limpeza do rate limit falhou (%s: %s)", e)

    def _rate_log_failure(self, msg: str, e: Exception) -> None:
        now = time.monotonic()
        if self._rate_logged_at is None or now - self._rate_logged_at >= _LOG_EVERY_S:
            self._rate_logged_at = now
            log.warning(msg, type(e).__name__, e)
