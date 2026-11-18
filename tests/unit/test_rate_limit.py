"""Rate limit compartilhado (services/rate_limit.py): limite por janela, fail-open e limpeza ocasional."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from code_index.services import rate_limit
from code_index.services.rate_limit import RateLimitMixin


class FakeStore:
    """Janela fixa em memória, com o mesmo contrato do RateLimitStore."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], int] = {}
        self.purges = 0
        self.fail = False
        self.delay = 0.0

    async def hit(self, bucket: str, key: str, *, window_s: int) -> tuple[int, float]:
        if self.fail:
            raise OSError("conexão recusada")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.hits[(bucket, key)] = self.hits.get((bucket, key), 0) + 1
        return self.hits[(bucket, key)], 17.0

    async def purge(self) -> int:
        self.purges += 1
        return 0


def service(store: FakeStore | None) -> RateLimitMixin:
    svc = RateLimitMixin.__new__(RateLimitMixin)  # só o que o mixin usa
    svc._rate_limits = store  # type: ignore[assignment]
    svc.settings = SimpleNamespace(rate_limit_db_timeout_s=0.05)  # type: ignore[assignment]
    return svc


async def test_limite_por_chave_e_balde() -> None:
    svc = service(FakeStore())
    assert await svc.rate_limit_hit("search", "t1", limit=2, window=60) is None
    assert await svc.rate_limit_hit("search", "t1", limit=2, window=60) is None
    assert await svc.rate_limit_hit("search", "t1", limit=2, window=60) == 17.0
    assert await svc.rate_limit_hit("search", "t2", limit=2, window=60) is None  # outra chave
    assert await svc.rate_limit_hit("read", "t1", limit=2, window=60) is None  # outro balde


async def test_banco_fora_deixa_passar(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore()
    store.fail = True
    svc = service(store)
    for _ in range(5):
        assert await svc.rate_limit_hit("search", "t1", limit=0, window=60) is None
    assert sum("rate limit indisponível" in r.getMessage() for r in caplog.records) == 1  # sem uma linha por requisição


async def test_banco_lento_deixa_passar() -> None:
    store = FakeStore()
    store.delay = 1.0  # pool cheio ou Postgres pendurado: a contagem não segura a requisição
    assert await service(store).rate_limit_hit("search", "t1", limit=0, window=60) is None


async def test_sem_store_deixa_passar() -> None:
    assert await service(None).rate_limit_hit("search", "t1", limit=0, window=60) is None


async def test_limpeza_de_vez_em_quando(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    store = FakeStore()
    svc = service(store)
    for _ in range(50):
        await svc.rate_limit_hit("search", "t1", limit=10**6, window=60)
    await asyncio.sleep(0)  # a limpeza roda numa task: a requisição não espera por ela
    assert store.purges == 1
    now[0] += rate_limit.RATE_LIMIT_PURGE_EVERY_S
    await svc.rate_limit_hit("search", "t1", limit=10**6, window=60)
    await asyncio.sleep(0)
    assert store.purges == 2
