"""API HTTP + MCP (/mcp) num uvicorn real: autenticação, scopes, CSRF, rate limit, auditoria.

Isolado como os demais testes de integração: schema code_index_test, pastas em tmp_path,
embeddings/rerank falsos (fixture fake_llm). Nenhuma chamada ao Google ou à Voyage.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from code_index.interfaces.http.app import create_app
from code_index.services import open_service
from code_index.settings import Settings, use_settings

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "chefe@example.com"
USER_EMAIL = "dev@example.com"


@dataclass
class Server:
    url: str
    settings: Settings
    tokens: dict[str, str]      # nome -> valor
    token_ids: dict[str, str]   # nome -> id


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _setup_tokens(settings: Settings) -> tuple[dict[str, str], dict[str, str]]:
    tokens, ids = {}, {}
    async with open_service(settings) as svc:
        for name, owner, scopes in [
            ("leitor", USER_EMAIL, ["search:read"]),
            ("indexador", USER_EMAIL, ["search:read", "index:write"]),
            ("admin-bot", ADMIN_EMAIL, ["admin"]),
            ("revogado", USER_EMAIL, ["search:read"]),
        ]:
            tok, info = await svc.create_agent_token(None, name=name, scopes=scopes, owner_email=owner)
            tokens[name], ids[name] = tok, info.id
        await svc.revoke_token(None, ids["revogado"])
        tokens["sessao-admin"], _ = await svc.start_session(ADMIN_EMAIL)
        tokens["sessao-dev"], _ = await svc.start_session(USER_EMAIL)
    return tokens, ids


@pytest.fixture
def server(int_settings: Settings, fake_llm: Any, tmp_path: Any) -> Iterator[Server]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    settings = int_settings.model_copy(update={
        "auth_secret": SecretStr("s" * 48), "public_url": url, "http_port": port,
        "admin_emails": [ADMIN_EMAIL], "rate_limit_search_per_min": 3,
    })
    use_settings(settings)
    tokens, ids = asyncio.run(_setup_tokens(settings))
    (tmp_path / "meu-repo").mkdir()
    (tmp_path / "meu-repo" / "app.py").write_text("def ola():\n    return 'mundo'\n")

    srv = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("servidor não subiu")
        time.sleep(0.05)
    try:
        yield Server(url=url, settings=settings, tokens=tokens, token_ids=ids)
    finally:
        srv.should_exit = True
        thread.join(timeout=15)


def client(server: Server, token: str | None = None, **headers: str) -> httpx.Client:
    if token:
        headers["Authorization"] = f"Bearer {server.tokens.get(token, token)}"
    return httpx.Client(base_url=server.url, headers=headers, timeout=30)


def test_saude_e_autenticacao(server: Server) -> None:
    assert client(server).get("/healthz").json() == {"status": "ok"}
    assert client(server).get("/api/v1/me").status_code == 401
    assert client(server, "cix_inexistente").get("/api/v1/me").status_code == 401
    assert client(server, "revogado").get("/api/v1/me").status_code == 401
    me = client(server, "leitor").get("/api/v1/me").json()
    assert me == {"subject": USER_EMAIL, "kind": "agent", "scopes": ["search:read"], "token_name": "leitor"}
    # token na URL não é aceito
    assert client(server).get("/api/v1/me", params={"token": server.tokens["leitor"]}).status_code == 401


def test_scopes(server: Server) -> None:
    leitor, indexador, admin = client(server, "leitor"), client(server, "indexador"), client(server, "admin-bot")
    assert leitor.get("/api/v1/repos").status_code == 200
    assert leitor.post("/api/v1/sync", json={"dry_run": True}).status_code == 403
    assert leitor.post("/api/v1/repos", json={"source": "org/x"}).status_code == 403
    assert leitor.get("/api/v1/audit").status_code == 403
    assert indexador.post("/api/v1/sync", json={"dry_run": True, "discover": False}).status_code == 202
    assert indexador.get("/api/v1/audit").status_code == 403
    assert admin.get("/api/v1/audit").status_code == 200


def test_index_write_nao_registra_repo_desconhecido(server: Server) -> None:
    r = client(server, "indexador").post(
        "/api/v1/sync", json={"repos": ["evil/repo"], "dry_run": True, "discover": False})
    assert r.status_code == 403 and "só admins" in r.json()["detail"]


def test_admin_gerencia_repos(server: Server, tmp_path: Any) -> None:
    admin = client(server, "admin-bot")
    r = admin.post("/api/v1/repos", json={"source": str(tmp_path / "meu-repo"), "name": "meu-repo"})
    assert r.status_code == 201, r.text
    assert admin.post("/api/v1/repos", json={"source": "lixo sem formato"}).status_code == 400
    dry = admin.post("/api/v1/sync", json={"dry_run": True, "discover": False}).json()
    assert dry["selected"] == ["meu-repo"]
    assert admin.delete("/api/v1/repos/meu-repo").json()["result"] == "removed"


def test_tokens_so_por_sessao_google_e_com_csrf(server: Server) -> None:
    origin = {"Origin": server.url}
    # agent (mesmo admin) não emite token
    assert client(server, "admin-bot").post("/api/v1/tokens", json={"name": "x"}).status_code == 403
    # sessão via cookie sem Origin => CSRF
    cookie = {"cix_session": server.tokens["sessao-dev"]}
    c = httpx.Client(base_url=server.url, cookies=cookie, timeout=30)
    assert c.post("/api/v1/tokens", json={"name": "x"}).status_code == 403
    assert c.post("/api/v1/tokens", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    # dev não concede o que não tem
    r = c.post("/api/v1/tokens", json={"name": "x", "scopes": ["index:write"]}, headers=origin)
    assert r.status_code == 403
    r = c.post("/api/v1/tokens", json={"name": "meu-claude"}, headers=origin)
    assert r.status_code == 201, r.text
    novo = r.json()
    assert novo["token"].startswith("cix_") and novo["scopes"] == ["search:read"]
    assert client(server, novo["token"]).get("/api/v1/me").json()["token_name"] == "meu-claude"
    # dev lista só os próprios; não revoga token de outro dono
    nomes = {t["name"] for t in c.get("/api/v1/tokens").json()}
    assert "admin-bot" not in nomes and "meu-claude" in nomes
    assert c.delete(f"/api/v1/tokens/{server.token_ids['admin-bot']}", headers=origin).status_code == 403
    assert c.delete(f"/api/v1/tokens/{novo['id']}", headers=origin).status_code == 200
    assert client(server, novo["token"]).get("/api/v1/me").status_code == 401


def test_rate_limit_de_busca(server: Server) -> None:
    c = client(server, "leitor")
    codes = [c.post("/api/v1/search", json={"query": "ola mundo", "mode": "keyword"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_host_desconhecido_recusado(server: Server) -> None:
    r = client(server, "leitor", Host="evil.example").get("/api/v1/me")
    assert r.status_code == 400


def test_auditoria(server: Server) -> None:
    client(server, "leitor").post("/api/v1/sync", json={"dry_run": True})
    client(server, "admin-bot").post("/api/v1/repos", json={"source": "lixo sem formato"})
    log = client(server, "admin-bot").get("/api/v1/audit").json()
    acoes = {(e["action"], e["status"]) for e in log}
    assert ("repo:add", 400) in acoes
    assert all("token" not in str(e["params"] or "").lower() or "cix_" not in str(e["params"]) for e in log)


async def _mcp(server: Server, token: str) -> tuple[list[str], Any, Any]:
    headers = {"Authorization": f"Bearer {server.tokens[token]}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(f"{server.url}/mcp", http_client=http) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            search = await s.call_tool("search_code", {"query": "ola", "mode": "keyword"})
            reindex = await s.call_tool("reindex", {"batch": 0})
            return tools, search, reindex


def test_mcp_exige_token(server: Server) -> None:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    hdrs = {"Accept": "application/json, text/event-stream"}
    assert client(server, **hdrs).post("/mcp", json=init).status_code == 401
    assert client(server, "revogado", **hdrs).post("/mcp", json=init).status_code == 401


def test_mcp_com_token_de_leitura(server: Server) -> None:
    tools, search, reindex = asyncio.run(_mcp(server, "leitor"))
    assert tools == ["search_code", "read_file_lines", "list_repos", "reindex"]
    assert not search.is_error
    assert reindex.is_error and "index:write" in reindex.content[0].text
