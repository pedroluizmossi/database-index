"""API HTTP + MCP (/mcp) num uvicorn real: autenticação, scopes, CSRF, rate limit, auditoria.

Isolado como os demais testes de integração: schema code_index_test, pastas em tmp_path,
embeddings/rerank falsos (fixture fake_llm). Nenhuma chamada ao Google ou à Voyage.
"""

from __future__ import annotations

import asyncio
import os
import sys
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from code_index.interfaces.http.app import create_app, server_config
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
    srv: Any = None             # uvicorn.Server (para testar o desligamento)
    thread: Any = None


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
            ("enriquecedor", ADMIN_EMAIL, ["enrich:write"]),
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

    srv = uvicorn.Server(server_config(create_app(settings), "127.0.0.1", port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("servidor não subiu")
        time.sleep(0.05)
    try:
        yield Server(url=url, settings=settings, tokens=tokens, token_ids=ids, srv=srv, thread=thread)
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
    assert me == {"subject": USER_EMAIL, "kind": "agent", "scopes": ["search:read"], "token_name": "leitor",
                  "provider": None}
    # sessão do navegador diz por qual provedor entrou (start_session usa "google" por padrão)
    sessao = httpx.Client(base_url=server.url, cookies={"cix_session": server.tokens["sessao-dev"]}).get("/api/v1/me")
    assert sessao.json()["provider"] == "google"
    # token na URL não é aceito
    assert client(server).get("/api/v1/me", params={"token": server.tokens["leitor"]}).status_code == 401


def test_scopes(server: Server) -> None:
    leitor, indexador, admin = client(server, "leitor"), client(server, "indexador"), client(server, "admin-bot")
    assert leitor.get("/api/v1/repos").status_code == 200
    assert client(server).get("/api/v1/xray").status_code == 401
    assert leitor.get("/api/v1/xray").status_code == 200
    assert leitor.post("/api/v1/sync", json={"dry_run": True}).status_code == 403
    assert leitor.post("/api/v1/repos", json={"source": "org/x"}).status_code == 403
    assert leitor.get("/api/v1/audit").status_code == 403
    assert indexador.post("/api/v1/sync", json={"dry_run": True, "discover": False}).status_code == 202
    assert indexador.get("/api/v1/audit").status_code == 403
    assert admin.get("/api/v1/audit").status_code == 200


def test_openapi_scopes_e_previa(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    from code_index.services import CodeIndexService

    async def catalog(self: CodeIndexService) -> list[dict[str, Any]]:
        return [{"domain": "platform", "service": "scheduler", "key": "platform_scheduler.swagger.yaml",
                 "modified": "2026-01-01", "size": 100}]

    monkeypatch.setattr(CodeIndexService, "openapi_catalog", catalog)
    reader = client(server, "leitor")
    assert client(server).get("/api/v1/openapi/status").status_code == 401
    assert reader.get("/api/v1/openapi/status").json() == []
    selection = {"domain": "platform", "services": ["scheduler"]}
    assert reader.get("/api/v1/openapi/catalog").status_code == 200
    assert reader.post("/api/v1/openapi/preview", json=selection).json()["services"] == ["scheduler"]
    assert reader.post("/api/v1/openapi/preview", json={"domain": "platform", "services": []}).status_code == 400
    assert reader.post("/api/v1/openapi/index", json=selection).status_code == 403
    assert client(server, "indexador").post("/api/v1/openapi/index", json=selection).status_code == 403


def test_cartao_do_repo_pela_api(server: Server) -> None:
    leitor = client(server, "leitor")
    assert leitor.get("/api/v1/repos/find", params={"q": "envio de e-mail"}).json() == []
    assert leitor.get("/api/v1/repos/find").status_code == 422
    assert leitor.get("/api/v1/repos/nao-existe/card").status_code == 404
    assert leitor.get("/api/v1/api-impls", params={"operation": "email.sendEmail"}).json() == []
    assert leitor.get("/api/v1/docs/suggestions").json() == []
    origins = leitor.get("/api/v1/relations/origins").json()
    assert [o["name"] for o in origins][:3] == ["manual", "agent", "llm"]
    assert {o["name"] for o in origins if o["contestable"]} == {"tag", "url", "mention"}
    assert leitor.get("/api/v1/relations", params={"entity": "op:platform/email/x"}).json() == {
        "entity": "op:platform/email/x", "relations": []}
    assert leitor.post("/api/v1/relations/rebuild").status_code == 403
    assert client(server, "admin-bot").post("/api/v1/relations/rebuild").status_code == 200
    body = {"doc_repo": "docs__wiki", "doc_path": "pt-br/x.md", "repo": "meu-repo", "approve": True}
    assert leitor.post("/api/v1/docs/suggestions/review", json=body).status_code == 403
    assert client(server, "admin-bot").post("/api/v1/docs/suggestions/review", json=body).status_code == 404
    assert client(server).get("/api/v1/repos/find", params={"q": "x"}).status_code == 401


def _fake_model(tmp_path: Any) -> str:
    """Comando que faz o papel do modelo: lê a tarefa de resumos e devolve uma entrega válida."""
    script = tmp_path / "modelo.py"
    script.write_text(
        "import json, re, sys\n"
        "paths = re.findall(r'^--- ARQUIVO \\d+: (.+)$', sys.stdin.read(), re.M)\n"
        "print('Aqui está:')\n"
        "print(json.dumps({'files': [{'path': p, 'resumo': 'Devolve uma saudação fixa para quem chama.',\n"
        "    'perguntas': ['O que faz?', 'O que devolve?', 'Quem usa?'], 'termos': ['saudação', 'exemplo', 'função']}\n"
        "    for p in paths]}))\n", encoding="utf-8")
    return f"{sys.executable} {script}"


def test_fila_de_enriquecimento_pela_api(server: Server, tmp_path: Any) -> None:
    import subprocess

    async def index() -> None:
        async with open_service(server.settings) as svc:
            await svc.add_repo(str(tmp_path / "meu-repo"), name="meu-repo")
            res = await svc.sync(["meu-repo"], batch=None, do_discover=False, progress=False)
            assert res.errors == {}
    asyncio.run(index())

    body = {"kind": "file_summaries"}
    assert client(server).post("/api/v1/enrich/next", json=body).status_code == 401
    for sem_scope in ("leitor", "indexador"):   # ler e indexar não dão direito a enriquecer
        assert client(server, sem_scope).post("/api/v1/enrich/next", json=body).status_code == 403
        assert client(server, sem_scope).get("/api/v1/enrich/status").status_code == 403
    bot = client(server, "enriquecedor")
    assert bot.post("/api/v1/search", json={"query": "ola"}).status_code == 403
    assert bot.post("/api/v1/enrich/next", json={"kind": "outro"}).status_code == 422

    task = bot.post("/api/v1/enrich/next", json=body).json()
    assert task["kind"] == "file_summaries" and task["files"] == ["app.py"] and "def ola()" in task["text"]
    assert bot.post("/api/v1/enrich/next", json=body).json() is None      # lote reservado; nada mais pendente
    url = f"/api/v1/enrich/tasks/{task['id']}"
    bad = bot.post(f"{url}/submit", content="não é json")
    assert bad.status_code == 400 and "JSON inválido" in bad.json()["detail"]
    assert bot.post("/api/v1/enrich/tasks/nao-existe/submit", content="{}").status_code == 400
    assert bot.post(f"{url}/release").json() == {"released": True}
    assert bot.post(f"{url}/release").json() == {"released": False}

    # o trabalhador remoto (só stdlib) esvazia a fila usando um "modelo" local
    env = {**os.environ, "CODE_INDEX_TOKEN": server.tokens["enriquecedor"]}
    worker = Path(__file__).parents[2] / "scripts" / "enrich_worker.py"
    run = subprocess.run([sys.executable, str(worker), "--server", server.url, "--cmd", _fake_model(tmp_path)],
                         env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert "ok   meu-repo" in run.stdout and "fila vazia. 1 entregue(s), 0 devolvida(s)." in run.stdout
    st = bot.get("/api/v1/enrich/status").json()
    assert st["summaries"] == {"files": 1, "fresh": 1, "stale": 0, "missing": 0}
    summary = client(server, "leitor").get("/api/v1/summaries/file/meu-repo/app.py").json()
    assert summary["summary"].startswith("Devolve uma saudação") and "enriquecedor" in summary["agent"]
    files = client(server, "leitor").get("/api/v1/repos/meu-repo/files").json()
    assert [(f["path"], bool(f["summary"])) for f in files] == [("app.py", True)]
    actions = [e["action"] for e in client(server, "admin-bot").get("/api/v1/audit", params={"limit": 50}).json()]
    assert "enrich:submit" in actions and "enrich:release" in actions


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


def test_dono_ddd_pela_api(server: Server, tmp_path: Any) -> None:
    admin, leitor = client(server, "admin-bot"), client(server, "leitor")
    assert admin.post("/api/v1/repos", json={"source": str(tmp_path / "meu-repo"), "name": "meu-repo"}).status_code == 201
    admin.post("/api/v1/sync", json={"dry_run": True, "discover": False})  # registra o repo na tabela de estado
    catalog = leitor.get("/api/v1/ownership/catalog").json()
    assert [d["name"] for d in catalog["domains"]] == ["transversal"]
    assert catalog["coverage"]["owned"] == 0

    body = {"repos": ["meu-repo"], "owner": "financeiro/contas"}
    assert leitor.put("/api/v1/ownership", json=body).status_code == 403
    r = admin.put("/api/v1/ownership", json=body)
    assert r.status_code == 400 and "desconhecido" in r.json()["detail"]
    r = admin.put("/api/v1/ownership", json={**body, "create": True, "reason": "teste"})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "owner": "financeiro/contas", "entities": ["repo:meu-repo"]}

    rows = leitor.get("/api/v1/ownership", params={"domain": "financeiro"}).json()
    assert [(x["repo"], x["owners"], x["origin"]) for x in rows] == [("meu-repo", ["financeiro/contas"], "manual")]
    repo = next(x for x in leitor.get("/api/v1/repos").json() if x["name"] == "meu-repo")
    assert (repo["owners"], repo["owner_origin"]) == (["financeiro/contas"], "manual")
    assert leitor.post("/api/v1/search", json={"query": "ola", "domain": "nada"}).status_code == 400

    assert leitor.post("/api/v1/ownership/clear", json={"repos": ["meu-repo"]}).status_code == 403
    assert admin.post("/api/v1/ownership/clear", json={"repos": ["meu-repo"]}).json()["cleared"] == 1
    assert leitor.get("/api/v1/ownership", params={"unowned": True}).json()[0]["repo"] == "meu-repo"
    audit = admin.get("/api/v1/audit").json()
    assert {"owner:set:api", "owner:clear:api"} <= {e["action"] for e in audit}


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


def test_configuracoes_da_pessoa(server: Server) -> None:
    origin = {"Origin": server.url}
    c = httpx.Client(base_url=server.url, cookies={"cix_session": server.tokens["sessao-dev"]}, timeout=30)
    inicial = c.get("/api/v1/me/settings").json()
    assert inicial["email"] == USER_EMAIL and inicial["changed"] == []
    assert inicial["settings"]["ui.theme"] == "system" and {i["key"] for i in inicial["schema"]} >= {"ui.theme"}
    # grava só com sessão + CSRF; token de agent (mesmo do dono) lê, mas não altera
    assert c.patch("/api/v1/me/settings", json={"settings": {"ui.theme": "dark"}}).status_code == 403
    assert client(server, "leitor").patch("/api/v1/me/settings", json={"settings": {"ui.theme": "dark"}}).status_code == 403
    r = c.patch("/api/v1/me/settings", json={"settings": {"ui.theme": "dark"}}, headers=origin)
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["ui.theme"] == "dark" and r.json()["changed"] == ["ui.theme"]
    assert client(server, "leitor").get("/api/v1/me/settings").json()["settings"]["ui.theme"] == "dark"
    # inválido não grava nada; cada pessoa tem as suas
    assert c.patch("/api/v1/me/settings", json={"settings": {"ui.theme": "roxo"}}, headers=origin).status_code == 400
    assert c.patch("/api/v1/me/settings", json={"settings": {"nao.existe": 1}}, headers=origin).status_code == 400
    assert client(server, "admin-bot").get("/api/v1/me/settings").json()["settings"]["ui.theme"] == "system"
    # null volta ao padrão
    r = c.patch("/api/v1/me/settings", json={"settings": {"ui.theme": None}}, headers=origin)
    assert r.json()["settings"]["ui.theme"] == "system" and r.json()["changed"] == []


def test_configuracoes_pelo_operador_local(int_settings: Settings, fake_llm: Any) -> None:
    settings = int_settings.model_copy(update={"auth_secret": SecretStr("s" * 48)})

    async def scenario() -> None:
        async with open_service(settings) as svc:
            with pytest.raises(ValueError):  # só quem já fez login
                await svc.update_user_settings(None, {"ui.theme": "dark"}, email="ninguem@example.com")
            await svc.start_session("Ana@Example.com", display_name="Ana")
            info = await svc.update_user_settings(None, {"ui.results_per_page": 16}, email="ana@example.com")
            assert info["settings"]["ui.results_per_page"] == 16 and info["display_name"] == "Ana"
            with pytest.raises(ValueError):
                await svc.update_user_settings(None, {"ui.results_per_page": 999}, email="ana@example.com")
            users = {u["email"]: u for u in await svc.list_users()}
            assert users["ana@example.com"]["changed"] == 1
            info = await svc.update_user_settings(None, {"ui.results_per_page": None}, email="ana@example.com")
            assert info["changed"] == [] and (await svc.user_settings(None, email="ana@example.com"))["settings"]["ui.results_per_page"] == 8

    asyncio.run(scenario())


def test_rate_limit_de_busca(server: Server) -> None:
    c = client(server, "leitor")
    codes = [c.post("/api/v1/search", json={"query": "ola mundo", "mode": "keyword"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_previa_nao_gasta_o_balde_de_sync(server: Server) -> None:
    # A prévia (dry_run) não custa nada e a interface a chama a cada mudança de lote: não pode esgotar o balde
    # de sync (poucas indexações por hora). Só iniciar a indexação gasta esse balde.
    c = client(server, "indexador")
    codes = [c.post("/api/v1/sync", json={"repos": None, "batch": 1, "dry_run": True}).status_code for _ in range(8)]
    assert codes == [202] * 8  # a rota responde 202 também na prévia (comportamento existente)


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
            symbol = await s.call_tool("find_symbol", {"name": "Qualquer"})
            grep = await s.call_tool("grep_code", {"pattern": "ola"})
            find = await s.call_tool("find_repos", {"query": "envio de e-mail"})
            assert not find.is_error and (find.structured_content or {}).get("result") == []
            card = await s.call_tool("repo_card", {"repo": "nao-existe"})
            assert card.is_error
            return tools, search, symbol, grep


def test_mcp_exige_token(server: Server) -> None:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    hdrs = {"Accept": "application/json, text/event-stream"}
    assert client(server, **hdrs).post("/mcp", json=init).status_code == 401
    assert client(server, "revogado", **hdrs).post("/mcp", json=init).status_code == 401


def test_mcp_so_expoe_leitura(server: Server) -> None:
    tools, search, symbol, grep = asyncio.run(_mcp(server, "leitor"))
    assert tools == ["search_code", "read_file_lines", "list_repos", "list_domains", "find_repos", "find_api_impl",
                     "related", "repo_card", "find_symbol", "grep_code"]
    assert not search.is_error and not symbol.is_error and not grep.is_error
    assert json.loads(grep.content[0].text) == {"matches": [], "truncated": False}


async def _mcp_guide(server: Server, token: str) -> tuple[str, list[str], str, str, str]:
    headers = {"Authorization": f"Bearer {server.tokens[token]}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(f"{server.url}/mcp", http_client=http) as (r, w), ClientSession(r, w) as s:
            init = await s.initialize()
            prompts = sorted(p.name for p in (await s.list_prompts()).prompts)
            guia = await s.get_prompt("guia_de_uso")
            investigar = await s.get_prompt("investigar", {"pergunta": "onde validamos o captcha?"})
            recurso = await s.read_resource("code-index://guia")
            return (init.instructions or "", prompts, guia.messages[0].content.text,  # type: ignore[union-attr]
                    investigar.messages[0].content.text, recurso.contents[0].text)  # type: ignore[union-attr]


def test_mcp_serve_guia_de_uso(server: Server) -> None:
    instructions, prompts, guia, investigar, recurso = asyncio.run(_mcp_guide(server, "leitor"))
    assert "find_symbol" in instructions and "guia_de_uso" in instructions
    assert prompts == ["guia_de_uso", "investigar"]
    assert guia.startswith("# Guia de uso do code-index") and guia == recurso
    for tool in ("search_code", "find_symbol", "grep_code", "read_file_lines", "list_repos", "find_repos", "repo_card",
                 "find_api_impl", "related"):
        assert tool in guia
    assert investigar.endswith("Pergunta: onde validamos o captcha?")


def test_servidor_desliga_com_cliente_mcp_conectado(server: Server) -> None:
    """Cliente MCP conectado e parado (como o Claude Code entre chamadas) não pode prender o desligamento."""
    conectado, liberar = threading.Event(), threading.Event()

    async def cliente() -> None:
        headers = {"Authorization": f"Bearer {server.tokens['leitor']}"}
        async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
            async with streamable_http_client(f"{server.url}/mcp", http_client=http) as (r, w), \
                    ClientSession(r, w) as s:
                await s.initialize()
                await s.list_tools()
                conectado.set()
                await asyncio.to_thread(liberar.wait, 30)

    t = threading.Thread(target=lambda: asyncio.run(cliente()), daemon=True)
    t.start()
    assert conectado.wait(15)
    inicio = time.monotonic()
    server.srv.should_exit = True
    server.thread.join(timeout=20)
    demorou = time.monotonic() - inicio
    liberar.set()
    assert not server.thread.is_alive(), "servidor ficou preso esperando o cliente MCP"
    assert demorou < 10, f"desligamento levou {demorou:.1f}s"


# ------------------------------------------------------------------ jobs persistentes


def test_job_roda_ate_o_fim_e_fica_no_historico(server: Server, tmp_path: Any) -> None:
    """Sync real (com LLM falso) em background: o job vai de running a done e fica consultável."""
    repo_dir = tmp_path / "job-repo"
    repo_dir.mkdir()
    (repo_dir / "servico.py").write_text("def processa_pedido(pedido):\n    return pedido.total\n")
    admin = client(server, "admin-bot")
    assert admin.post("/api/v1/repos", json={"source": str(repo_dir), "name": "job-repo"}).status_code == 201

    r = admin.post("/api/v1/sync", json={"repos": ["job-repo"], "discover": False})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    deadline = time.monotonic() + 60
    job = admin.get(f"/api/v1/sync/jobs/{job_id}").json()
    while job["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.3)
        job = admin.get(f"/api/v1/sync/jobs/{job_id}").json()
    assert job["status"] == "done", job
    assert list(job["result"]["indexed"]) == ["job-repo"]
    assert job["requested_by"] == f"{ADMIN_EMAIL} [admin-bot]"

    historico = [j["id"] for j in admin.get("/api/v1/sync/jobs").json()]
    assert job_id in historico
    assert client(server, "leitor").get("/api/v1/sync/jobs").status_code == 403  # só quem indexa vê os jobs


async def _insert_running_job(settings: Settings) -> str:
    import asyncpg

    async with open_service(settings):  # garante schema/migrações
        pass
    conn = await asyncpg.connect(settings.database_url)
    try:
        return str(await conn.fetchval(
            f'INSERT INTO "{settings.pg_schema}".sync_jobs (requested_by, status) '
            "VALUES ('teste', 'running') RETURNING id::text"
        ))
    finally:
        await conn.close()


def test_job_interrompido_por_reinicio_vira_failed(int_settings: Settings, fake_llm: Any) -> None:
    """Na inicialização do servidor, um job que ficou 'running' (servidor caiu) vira failed."""

    async def scenario() -> None:
        job_id = await _insert_running_job(int_settings)
        async with open_service(int_settings) as svc:
            assert await svc.recover_interrupted_jobs() == 1
            job = await svc.get_job(job_id)
        assert job is not None
        assert job.status == "failed" and "reiniciou" in (job.error or "")

    asyncio.run(scenario())


def test_comando_da_cli_nao_derruba_job_do_servidor(int_settings: Settings, fake_llm: Any) -> None:
    """Regressão: abrir o serviço (como faz todo comando da CLI) não pode mexer no job de outro processo."""

    async def scenario() -> None:
        job_id = await _insert_running_job(int_settings)
        async with open_service(int_settings) as svc:  # ex.: `code-index repo list`
            await svc.list_repos()
            job = await svc.get_job(job_id)
        assert job is not None and job.status == "running"

    asyncio.run(scenario())


def test_servidor_recupera_jobs_ao_subir(int_settings: Settings, fake_llm: Any) -> None:
    """O caminho real: o lifespan do app HTTP marca os jobs órfãos ao iniciar."""
    from starlette.testclient import TestClient

    job_id = asyncio.run(_insert_running_job(int_settings))
    settings = int_settings.model_copy(update={"auth_secret": SecretStr("s" * 48)})
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765"):
        pass  # sobe e desce o lifespan

    async def check() -> None:
        async with open_service(int_settings) as svc:
            job = await svc.get_job(job_id)
        assert job is not None and job.status == "failed"

    asyncio.run(check())


# ------------------------------------------------------------------ páginas de erro do login


def test_callback_de_erro_mostra_pagina_html(server: Server) -> None:
    r = client(server).get("/auth/callback", params={"error": "access_denied"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/html")
    assert "Login cancelado" in r.text and "access_denied" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_callback_sem_cookie_mostra_html(server: Server) -> None:
    r = client(server).get("/auth/callback", params={"code": "x", "state": "y"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/html")
    assert "Login expirou" in r.text


def test_callback_escapa_texto_vindo_de_fora(server: Server) -> None:
    payload = "<script>alert(1)</script>"
    r = client(server).get("/auth/callback", params={"error": payload})
    assert r.status_code == 400
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# ------------------------------------------------------------------ OAuth do MCP (login no navegador)

REDIRECT = "http://127.0.0.1:5555/callback"


def _pkce() -> tuple[str, str]:
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(server: Server, **extra: Any) -> dict[str, Any]:
    body = {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none", "client_name": "Claude Code",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **extra}
    r = client(server).post("/register", json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


def _authorize(server: Server, client_id: str, challenge: str, session: str = "sessao-dev",
               decision: str = "allow") -> httpx.Response:
    """/authorize (SDK) → /auth/authorize (consentimento) → POST da decisão. Devolve o redirect final."""
    c = client(server)
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "st-1", "resource": f"{server.url}/mcp",
    })
    assert r.status_code == 302, r.text
    consent = r.headers["location"]
    assert consent.startswith(f"{server.url}/auth/authorize?req=")
    cookies = {"cix_session": server.tokens[session]}
    page = httpx.get(consent, cookies=cookies, timeout=30)
    assert page.status_code == 200 and "Permitir" in page.text
    req = consent.split("req=", 1)[1]
    return httpx.post(f"{server.url}/auth/authorize", data={"req": req, "decision": decision}, cookies=cookies,
                      headers={"Origin": server.url}, timeout=30)


def _code_from(resp: httpx.Response) -> str:
    assert resp.status_code == 303, resp.text
    loc = httpx.URL(resp.headers["location"])
    assert str(loc).startswith(REDIRECT) and loc.params["state"] == "st-1"
    return loc.params["code"]


def _token(server: Server, **form: str) -> httpx.Response:
    return client(server).post("/token", data=form)


def test_oauth_metadados_para_descoberta(server: Server) -> None:
    hdrs = {"Accept": "application/json, text/event-stream"}
    r = client(server, **hdrs).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and "oauth-protected-resource" in r.headers["www-authenticate"]
    prm = client(server).get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["authorization_servers"] == [server.url] or prm["authorization_servers"] == [server.url + "/"]
    meta = client(server).get("/.well-known/oauth-authorization-server").json()
    assert meta["registration_endpoint"].endswith("/register") and meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["scopes_supported"] == ["search:read"]


def test_oauth_registro_so_com_redirect_local(server: Server) -> None:
    r = client(server).post("/register", json={"redirect_uris": ["https://evil.example/cb"],
                                               "token_endpoint_auth_method": "none"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
    r = client(server).post("/register", json={"redirect_uris": [REDIRECT], "scope": "admin"})
    assert r.status_code == 400  # só search:read por OAuth


def test_oauth_fluxo_completo_com_refresh_e_revogacao(server: Server) -> None:
    reg = _register(server, client_name="Claude <script>")
    verifier, challenge = _pkce()

    # sem sessão: o consentimento manda para o login, preservando o pedido
    r = client(server).get("/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st-1"})
    r2 = client(server).get(r.headers["location"])
    assert r2.status_code == 303 and r2.headers["location"].startswith("/auth/login?next=%2Fauth%2Fauthorize")
    # pedido adulterado não vale
    assert client(server).get("/auth/authorize", params={"req": "x.y.z"}).status_code == 400

    # consentimento escapa o nome do cliente e exige a nossa origem (CSRF)
    consent = client(server).get("/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st-1"}).headers["location"]
    page = httpx.get(consent, cookies={"cix_session": server.tokens["sessao-dev"]}, timeout=30)
    assert "Claude &lt;script&gt;" in page.text and "<script>" not in page.text and "dev@example.com" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    no_origin = httpx.post(f"{server.url}/auth/authorize", data={"req": consent.split("req=")[1], "decision": "allow"},
                           cookies={"cix_session": server.tokens["sessao-dev"]}, timeout=30)
    assert no_origin.status_code == 403
    # um token de agent (Bearer) não aprova nada: só o cookie de sessão
    as_agent = httpx.post(f"{server.url}/auth/authorize", data={"req": consent.split("req=")[1], "decision": "allow"},
                          headers={"Origin": server.url, "Authorization": f"Bearer {server.tokens['admin-bot']}"},
                          timeout=30)
    assert as_agent.status_code == 303 and "/auth/login" in as_agent.headers["location"]

    code = _code_from(_authorize(server, reg["client_id"], challenge))
    base = {"client_id": reg["client_id"], "redirect_uri": REDIRECT}
    assert _token(server, grant_type="authorization_code", code=code, code_verifier="errado" * 8,
                  **base).status_code == 400  # PKCE
    r = _token(server, grant_type="authorization_code", code=code, code_verifier=verifier, **base)
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["access_token"].startswith("cix_") and tok["refresh_token"].startswith("cixr_")
    assert tok["scope"] == "search:read" and tok["token_type"].lower() == "bearer"
    # código é de uso único
    assert _token(server, grant_type="authorization_code", code=code, code_verifier=verifier,
                  **base).json()["error"] == "invalid_grant"

    me = client(server, tok["access_token"]).get("/api/v1/me").json()
    assert (me["kind"], me["scopes"], me["subject"]) == ("oauth", ["search:read"], USER_EMAIL)
    server.tokens["oauth-1"] = tok["access_token"]
    tools, *_ = asyncio.run(_mcp(server, "oauth-1"))
    assert "search_code" in tools
    # o refresh token não serve como Bearer
    assert client(server, tok["refresh_token"]).get("/api/v1/me").status_code == 401
    # token OAuth não emite token
    assert client(server, tok["access_token"]).post("/api/v1/tokens", json={"name": "x"}).status_code == 403

    # refresh rotaciona: o access token antigo cai
    r = _token(server, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=reg["client_id"])
    assert r.status_code == 200, r.text
    tok2 = r.json()
    assert tok2["refresh_token"] != tok["refresh_token"]
    assert client(server, tok["access_token"]).get("/api/v1/me").status_code == 401
    assert client(server, tok2["access_token"]).get("/api/v1/me").status_code == 200
    # reuso do refresh antigo (vazado) revoga a família inteira
    r = _token(server, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=reg["client_id"])
    assert r.status_code == 400
    assert client(server, tok2["access_token"]).get("/api/v1/me").status_code == 401
    assert _token(server, grant_type="refresh_token", refresh_token=tok2["refresh_token"],
                  client_id=reg["client_id"]).status_code == 400


def test_oauth_pessoa_desconecta_o_cliente(server: Server) -> None:
    reg = _register(server)
    verifier, challenge = _pkce()
    code = _code_from(_authorize(server, reg["client_id"], challenge))
    tok = _token(server, grant_type="authorization_code", code=code, code_verifier=verifier,
                 client_id=reg["client_id"], redirect_uri=REDIRECT).json()
    cookie = {"cix_session": server.tokens["sessao-dev"]}
    c = httpx.Client(base_url=server.url, cookies=cookie, timeout=30)
    listed = [t for t in c.get("/api/v1/tokens").json() if t["kind"] == "oauth" and t["active"]]
    assert [t["name"] for t in listed] == ["Claude Code"]
    assert c.delete(f"/api/v1/tokens/{listed[0]['id']}", headers={"Origin": server.url}).status_code == 200
    assert client(server, tok["access_token"]).get("/api/v1/me").status_code == 401
    assert _token(server, grant_type="refresh_token", refresh_token=tok["refresh_token"],
                  client_id=reg["client_id"]).status_code == 400  # o refresh caiu junto


def test_oauth_negar_e_revogacao_pelo_cliente(server: Server) -> None:
    reg = _register(server)
    _, challenge = _pkce()
    denied = _authorize(server, reg["client_id"], challenge, decision="deny")
    assert denied.status_code == 303 and "error=access_denied" in denied.headers["location"]

    verifier, challenge = _pkce()
    code = _code_from(_authorize(server, reg["client_id"], challenge, session="sessao-admin"))
    tok = _token(server, grant_type="authorization_code", code=code, code_verifier=verifier,
                 client_id=reg["client_id"], redirect_uri=REDIRECT).json()
    assert tok["scope"] == "search:read"  # admin também só lê por OAuth
    # o SDK exige o campo client_secret mesmo de cliente público (vazio)
    revoke = {"client_id": reg["client_id"], "client_secret": ""}
    assert client(server).post("/revoke", data={"token": tok["refresh_token"], **revoke}).status_code == 200
    assert client(server, tok["access_token"]).get("/api/v1/me").status_code == 401
    # revogar pelo access token também derruba o refresh
    verifier, challenge = _pkce()
    code = _code_from(_authorize(server, reg["client_id"], challenge))
    tok = _token(server, grant_type="authorization_code", code=code, code_verifier=verifier,
                 client_id=reg["client_id"], redirect_uri=REDIRECT).json()
    assert client(server).post("/revoke", data={"token": tok["access_token"], **revoke}).status_code == 200
    assert client(server, tok["access_token"]).get("/api/v1/me").status_code == 401
    assert _token(server, grant_type="refresh_token", refresh_token=tok["refresh_token"],
                  client_id=reg["client_id"]).status_code == 400
    # outro cliente não revoga token alheio
    other = _register(server)
    verifier, challenge = _pkce()
    code = _code_from(_authorize(server, reg["client_id"], challenge))
    tok = _token(server, grant_type="authorization_code", code=code, code_verifier=verifier,
                 client_id=reg["client_id"], redirect_uri=REDIRECT).json()
    client(server).post("/revoke", data={"token": tok["access_token"], "client_id": other["client_id"],
                                         "client_secret": ""})
    assert client(server, tok["access_token"]).get("/api/v1/me").status_code == 200


def test_oauth_cliente_confidencial_precisa_do_segredo(server: Server) -> None:
    reg = _register(server, token_endpoint_auth_method="client_secret_post")
    assert reg["client_secret"]
    verifier, challenge = _pkce()
    code = _code_from(_authorize(server, reg["client_id"], challenge))
    base = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": reg["client_id"], "redirect_uri": REDIRECT}
    assert _token(server, **base, client_secret="errado").status_code == 401
    assert _token(server, **base, client_secret=reg["client_secret"]).status_code == 200


# ---------------------------------------------------------------- uso da aplicação e pessoas

async def _mcp_como(server: Server, token: str, user_agent: str) -> None:
    headers = {"Authorization": f"Bearer {server.tokens[token]}", "User-Agent": user_agent}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(f"{server.url}/mcp", http_client=http) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            assert not (await s.call_tool("search_code", {"query": "ola", "mode": "keyword"})).is_error
            assert not (await s.call_tool("grep_code", {"pattern": "nada-casa-isso"})).is_error
            assert (await s.call_tool("repo_card", {"repo": "nao-existe"})).is_error


def test_uso_registrado_por_pessoa_tool_e_cliente(server: Server) -> None:
    asyncio.run(_mcp_como(server, "leitor", "claude-code/9.9.9 (cli)"))
    r = client(server, "leitor", **{"User-Agent": "meu-script/1.0"}).post(
        "/api/v1/search", json={"query": "ola mundo", "mode": "keyword"})
    assert r.status_code == 200
    client(server, "leitor").get("/api/v1/sync/jobs")  # polling da interface: não conta

    rep = client(server, "admin-bot").get("/api/v1/usage", params={"days": 7}).json()
    acoes = {(a["channel"], a["action"]): a for a in rep["by_action"]}
    busca = acoes[("mcp", "search_code")]
    assert busca["calls"] == 1 and busca["errors"] == 0 and busca["users"] == 1
    assert busca["response_bytes"] > 0 and busca["avg_results"] is not None
    assert acoes[("mcp", "repo_card")]["errors"] == 1
    assert acoes[("mcp", "grep_code")]["empty_results"] == 1
    assert acoes[("api", "POST /api/v1/search")]["calls"] == 1
    # contexto de agent só no MCP; resposta da API não ocupa a janela de ninguém
    assert acoes[("mcp", "grep_code")]["context_tokens"] > 0 and acoes[("api", "POST /api/v1/search")]["context_tokens"] is None
    assert rep["totals"]["context_bytes"] < rep["totals"]["response_bytes"]
    assert not any(a.startswith("GET /api/v1/sync/jobs") for _, a in acoes)
    # olhar o dashboard não é uso
    client(server, "admin-bot").get("/api/v1/users")
    de_novo = client(server, "admin-bot").get("/api/v1/usage", params={"days": 7}).json()
    assert not any(a["action"].startswith(("GET /api/v1/usage", "GET /api/v1/users")) for a in de_novo["by_action"])
    clientes = {c["client"]: c for c in rep["by_client"]}
    assert clientes["claude-code"]["last_version"] == "9.9.9" and "meu-script" in clientes
    assert {"query": "nada-casa-isso", "action": "grep_code"}.items() <= next(
        q for q in rep["empty_queries"] if q["action"] == "grep_code").items()
    assert rep["totals"]["calls"] >= 4 and rep["totals"]["errors"] >= 1
    assert len(rep["daily"]) == 7 and rep["daily"][-1]["mcp"] >= 3
    assert {e["email"] for e in rep["recent"] if e["channel"] == "mcp"} == {USER_EMAIL}
    assert "cix_" not in json.dumps(rep)  # nunca guarda tokens

    # só admin vê o uso de todos; cada pessoa vê o próprio
    assert client(server, "leitor").get("/api/v1/usage").status_code == 403
    meu = client(server, "leitor").get("/api/v1/me/usage").json()
    assert {e["email"] for e in meu["recent"]} == {USER_EMAIL}
    assert client(server, "admin-bot").get("/api/v1/me/usage").json()["totals"]["mcp_calls"] == 0
    filtrado = client(server, "admin-bot").get("/api/v1/usage", params={"email": USER_EMAIL}).json()
    assert filtrado["totals"]["mcp_calls"] == 3
    assert client(server, "admin-bot").get("/api/v1/usage", params={"email": "x@nada.com"}).status_code == 400

    pessoas = {u["email"]: u for u in client(server, "admin-bot").get("/api/v1/users").json()}
    assert pessoas[USER_EMAIL]["mcp_calls"] == 3 and pessoas[USER_EMAIL]["role"] == "leitura"
    assert pessoas[USER_EMAIL]["active"] and pessoas[USER_EMAIL]["active_tokens"] == 2  # leitor + indexador
    assert "claude-code" in pessoas[USER_EMAIL]["clients"]
    assert pessoas[ADMIN_EMAIL]["role"] == "admin"
    assert client(server, "leitor").get("/api/v1/users").status_code == 403


def test_admin_desativa_e_reativa_pessoa(server: Server) -> None:
    origin = {"Origin": server.url}
    admin = httpx.Client(base_url=server.url, cookies={"cix_session": server.tokens["sessao-admin"]}, timeout=30)
    # agent (mesmo admin) não bloqueia ninguém; leitor não é admin; CSRF vale
    assert client(server, "admin-bot").post(f"/api/v1/users/{USER_EMAIL}/disable").status_code == 403
    assert client(server, "leitor").post(f"/api/v1/users/{USER_EMAIL}/disable").status_code == 403
    assert admin.post(f"/api/v1/users/{USER_EMAIL}/disable").status_code == 403
    assert admin.post(f"/api/v1/users/{ADMIN_EMAIL}/disable", headers=origin).status_code == 403  # a si mesmo
    assert admin.post("/api/v1/users/ninguem@example.com/disable", headers=origin).status_code == 400

    r = admin.post(f"/api/v1/users/{USER_EMAIL}/disable", headers=origin)
    assert r.status_code == 200 and r.json()["active"] is False
    # nenhuma credencial da pessoa vale: token de agent, sessão e MCP
    assert client(server, "leitor").get("/api/v1/me").status_code == 401
    assert httpx.Client(base_url=server.url, cookies={"cix_session": server.tokens["sessao-dev"]}).get(
        "/api/v1/me").status_code == 401
    hdrs = {"Accept": "application/json, text/event-stream"}
    assert client(server, "leitor", **hdrs).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    pessoas = {u["email"]: u for u in client(server, "admin-bot").get("/api/v1/users").json()}
    assert pessoas[USER_EMAIL]["active"] is False

    async def novo_login() -> None:
        async with open_service(server.settings) as svc:
            with pytest.raises(PermissionError):
                await svc.start_session(USER_EMAIL)

    asyncio.run(novo_login())

    assert admin.post(f"/api/v1/users/{USER_EMAIL}/enable", headers=origin).json()["active"] is True
    assert client(server, "leitor").get("/api/v1/me").status_code == 200
    acoes = {(e["action"], e["status"]) for e in client(server, "admin-bot").get("/api/v1/audit").json()}
    assert ("user:disable", 200) in acoes and ("user:enable", 200) in acoes


def test_token_da_cli_cria_a_pessoa(int_settings: Settings, fake_llm: Any) -> None:
    settings = int_settings.model_copy(update={"auth_secret": SecretStr("s" * 48)})

    async def scenario() -> None:
        async with open_service(settings) as svc:
            tok, _ = await svc.create_agent_token(None, name="bot", scopes=["search:read"], owner_email="Bot@Ex.com")
            principal = await svc.authenticate(tok)
            assert principal is not None and principal.user_id is not None
            pessoas = {u["email"]: u for u in await svc.users_overview(30)}
            assert pessoas["bot@ex.com"]["active_tokens"] == 1 and pessoas["bot@ex.com"]["calls"] == 0
            assert (await svc.housekeeping())["usage_events"] == 0

    asyncio.run(scenario())
