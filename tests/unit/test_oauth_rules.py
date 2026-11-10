"""Regras puras do OAuth do MCP: redirect_uri aceitos e scopes concedidos."""

from __future__ import annotations

import pytest

from code_index.domain.auth import ADMIN, INDEX_WRITE, SEARCH_READ, check_can_issue, Principal
from code_index.domain.oauth import check_redirect_uris, grant_scopes, redirect_uri_allowed


@pytest.mark.parametrize("uri", [
    "http://localhost:33418/callback",
    "http://127.0.0.1:9999/oauth/callback",
    "http://[::1]:8080/cb",
    "http://localhost/callback",
])
def test_loopback_sempre_aceito(uri: str) -> None:
    assert redirect_uri_allowed(uri)


@pytest.mark.parametrize("uri", [
    "https://evil.example/callback",
    "http://localhost.evil.example/callback",       # parece loopback, não é
    "http://127.0.0.1.evil.example/cb",
    "https://claude.ai.evil.example/api/mcp/auth_callback",
    "http://localhost:3000/cb#frag",
    "javascript:alert(1)",
    "",
])
def test_redirect_recusado(uri: str) -> None:
    assert not redirect_uri_allowed(uri, ["https://claude.ai/api/mcp/auth_callback"])


def test_prefixo_configurado() -> None:
    prefixes = ["https://claude.ai/api/mcp/auth_callback", "kiro://"]
    assert redirect_uri_allowed("https://claude.ai/api/mcp/auth_callback", prefixes)
    assert redirect_uri_allowed("kiro://kiro.kiroAgent/authenticate-success", prefixes)
    with pytest.raises(ValueError, match="não permitido"):
        check_redirect_uris(["http://localhost:1/cb", "https://evil.example/cb"], prefixes)
    with pytest.raises(ValueError, match="ao menos um"):
        check_redirect_uris([])


@pytest.mark.parametrize("uri", [
    "https://app.example.com@evil.com/cb",        # usuário no URL: o host é evil.com
    "https://app.example.com.evil.com/cb",
    "https://app.example.com:8443/cb",            # outra porta
    "https://claude.ai/api/mcp/auth_callbackevil",  # caminho sem fronteira de segmento
])
def test_prefixo_exige_mesmo_host_e_fronteira_de_caminho(uri: str) -> None:
    prefixes = ["https://app.example.com", "https://claude.ai/api/mcp/auth_callback"]
    assert not redirect_uri_allowed(uri, prefixes)
    assert redirect_uri_allowed("https://app.example.com/cb", prefixes)
    assert redirect_uri_allowed("https://claude.ai/api/mcp/auth_callback?x=1", prefixes)


def test_oauth_so_concede_leitura_mesmo_para_admin() -> None:
    admin = (ADMIN, INDEX_WRITE, SEARCH_READ)
    assert grant_scopes(None, admin) == (SEARCH_READ,)
    assert grant_scopes([SEARCH_READ], admin) == (SEARCH_READ,)
    with pytest.raises(ValueError, match="não disponível"):
        grant_scopes([INDEX_WRITE], admin)
    with pytest.raises(PermissionError):
        grant_scopes(None, ())


def test_token_oauth_nao_emite_token() -> None:
    oauth = Principal(subject="a@x.com", kind="oauth", scopes=frozenset({SEARCH_READ}))
    with pytest.raises(PermissionError, match="não podem emitir"):
        check_can_issue(oauth, [SEARCH_READ])
