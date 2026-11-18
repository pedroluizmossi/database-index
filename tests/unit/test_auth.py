"""Regras de autorização (domain/auth) e rate limiter."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from code_index.domain.auth import (
    ADMIN, ENRICH_WRITE, INDEX_WRITE, SEARCH_READ, Principal, check_can_issue, effective_scopes, scopes_for_user,
    validate_scopes,
)
from code_index.interfaces.http.security import RateLimiter, enforce_rate_limit
from code_index.settings import Settings


def session(*scopes: str) -> Principal:
    return Principal(subject="ana@example.com", kind="session", scopes=effective_scopes(scopes))


def test_admin_implica_todos_os_scopes() -> None:
    assert effective_scopes([ADMIN]) == {ADMIN, INDEX_WRITE, ENRICH_WRITE, SEARCH_READ}
    assert effective_scopes([SEARCH_READ]) == {SEARCH_READ}


def test_scopes_de_usuario_google() -> None:
    assert set(scopes_for_user("Chefe@Example.com", ["chefe@example.com"])) == {
        ADMIN, INDEX_WRITE, ENRICH_WRITE, SEARCH_READ}
    assert scopes_for_user("dev@example.com", ["chefe@example.com"]) == (SEARCH_READ,)


@pytest.mark.parametrize("scopes", [[], ["root"], ["search:read", "x"]])
def test_scopes_invalidos(scopes: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_scopes(scopes)


def test_sessao_emite_so_scopes_que_tem() -> None:
    assert check_can_issue(session(SEARCH_READ), [SEARCH_READ]) == (SEARCH_READ,)
    with pytest.raises(PermissionError, match="index:write"):
        check_can_issue(session(SEARCH_READ), [SEARCH_READ, INDEX_WRITE])
    assert set(check_can_issue(session(ADMIN), [INDEX_WRITE, ADMIN])) == {INDEX_WRITE, ADMIN}


def test_token_de_agent_nao_emite_token() -> None:
    agent = Principal(subject="ana@example.com", kind="agent", scopes=effective_scopes([ADMIN]),
                      token_id="1", token_name="bot")
    with pytest.raises(PermissionError, match="não podem emitir"):
        check_can_issue(agent, [SEARCH_READ])
    assert agent.actor == "ana@example.com [bot]"


class FakeService:
    def __init__(self, retry: float | None = None) -> None:
        self.retry = retry
        self.calls: list[tuple[str, str, int, float]] = []

    async def rate_limit_hit(self, bucket: str, key: str, *, limit: int, window: float) -> float | None:
        self.calls.append((bucket, key, limit, window))
        return self.retry


async def test_rate_limiter_delega_ao_servico() -> None:
    svc = FakeService(retry=12.5)
    rl = RateLimiter(lambda: svc)  # type: ignore[arg-type, return-value]
    assert await rl.hit("search", "t1", limit=2, window=60) == 12.5
    assert svc.calls == [("search", "t1", 2, 60)]


async def test_rate_limiter_sem_servico_deixa_passar() -> None:
    def not_started() -> Any:
        raise RuntimeError("serviço ainda não iniciado")

    assert await RateLimiter(not_started).hit("oauth-register", "1.2.3.4", limit=1, window=3600) is None


async def test_enforce_rate_limit_429_com_retry_after() -> None:
    settings = Settings(_env_file=None, rate_limit_search_per_min=5)
    agent = Principal(subject="ana@example.com", kind="agent", scopes=effective_scopes([SEARCH_READ]),
                      token_id="tok-1", token_name="bot")
    svc = FakeService()
    rl = RateLimiter(lambda: svc)  # type: ignore[arg-type, return-value]
    await enforce_rate_limit(rl, settings, agent, "search")
    assert svc.calls == [("search", "tok-1", 5, 60.0)]  # chave = id do token, nunca o valor
    svc.retry = 41.3
    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limit(rl, settings, agent, "search")
    assert exc.value.status_code == 429 and exc.value.headers == {"Retry-After": "42"}
