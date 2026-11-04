"""Regras de autorização (domain/auth) e rate limiter."""

from __future__ import annotations

import pytest

from code_index.domain.auth import (
    ADMIN, INDEX_WRITE, SEARCH_READ, Principal, check_can_issue, effective_scopes, scopes_for_user, validate_scopes,
)
from code_index.interfaces.http.security import RateLimiter


def session(*scopes: str) -> Principal:
    return Principal(subject="ana@example.com", kind="session", scopes=effective_scopes(scopes))


def test_admin_implica_todos_os_scopes() -> None:
    assert effective_scopes([ADMIN]) == {ADMIN, INDEX_WRITE, SEARCH_READ}
    assert effective_scopes([SEARCH_READ]) == {SEARCH_READ}


def test_scopes_de_usuario_google() -> None:
    assert set(scopes_for_user("Chefe@Example.com", ["chefe@example.com"])) == {ADMIN, INDEX_WRITE, SEARCH_READ}
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


def test_rate_limiter_janela_deslizante(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("code_index.interfaces.http.security.time.monotonic", lambda: now[0])
    rl = RateLimiter()
    assert rl.hit("search", "t1", limit=2, window=60) is None
    assert rl.hit("search", "t1", limit=2, window=60) is None
    assert rl.hit("search", "t1", limit=2, window=60) == pytest.approx(60)
    assert rl.hit("search", "t2", limit=2, window=60) is None  # outra chave, outro balde
    now[0] += 61
    assert rl.hit("search", "t1", limit=2, window=60) is None
