"""CLI remota (interfaces/cli/remote.py): URL, credenciais, modo, erros HTTP e os comandos com httpx.MockTransport.

Sem rede e sem banco: o servidor é um MockTransport que responde como a API.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from code_index.interfaces import cli
from code_index.interfaces.cli import remote as remote_mod
from code_index.interfaces.cli.remote import (
    Credentials,
    JobProgress,
    RemoteError,
    RemoteService,
    RemoteTarget,
    insecure,
    load_credentials,
    normalize_url,
    resolve_target,
    save_credentials,
)
from code_index.settings import Settings

TOKEN = "cix_segredo_de_teste_123"
URL = "https://ci.exemplo.com"


# ---------------------------------------------------------------- URL e credenciais

def test_normalize_url() -> None:
    assert normalize_url("https://ci.exemplo.com/") == URL
    assert normalize_url(" https://ci.exemplo.com/api/v1/ ") == URL
    assert normalize_url("http://localhost:8765/prefixo/") == "http://localhost:8765/prefixo"
    for bad in ("ci.exemplo.com", "ftp://x", "", "https://"):
        with pytest.raises(ValueError):
            normalize_url(bad)


@pytest.mark.parametrize("url", [
    "https://user:senha@ci.exemplo.com", f"https://ci.exemplo.com/?token={TOKEN}",
    "https://ci.exemplo.com/#frag", f"https://ci.exemplo.com/{TOKEN}",
])
def test_url_com_credencial_recusada(url: str) -> None:
    with pytest.raises(ValueError, match="não pode levar token"):
        normalize_url(url)


def test_http_fora_de_loopback_e_inseguro() -> None:
    assert insecure("http://ci.exemplo.com")
    assert not insecure("http://127.0.0.1:8765") and not insecure("http://localhost") and not insecure("http://[::1]:80")
    assert not insecure(URL)


def test_credenciais_0600_e_ida_e_volta(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "code-index" / "credentials.toml"
    save_credentials(path, Credentials(url=URL, token='cix_"aspas"\\'))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    creds = load_credentials(path)
    assert creds == Credentials(url=URL, token='cix_"aspas"\\')
    assert "cix_" not in repr(creds)  # o token não aparece em repr/log
    save_credentials(path, Credentials(url=URL, token=TOKEN))  # regrava por cima, mantendo 0600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and load_credentials(path) == Credentials(URL, TOKEN)
    assert not list(path.parent.glob(".*tmp"))


def test_credenciais_abertas_avisam(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = save_credentials(tmp_path / "c.toml", Credentials(url=URL, token=TOKEN))
    path.chmod(0o644)
    load_credentials(path)
    assert "chmod 600" in capsys.readouterr().err


def test_credenciais_invalidas(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("url = 'https://x'\n")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="incompleto"):
        load_credentials(path)
    path.write_text("isto não é toml = = =")
    with pytest.raises(ValueError, match="ilegível"):
        load_credentials(path)


def test_resolve_target(make_settings: Callable[..., Settings], tmp_path: Path) -> None:
    creds = tmp_path / "creds.toml"
    assert resolve_target(make_settings(credentials_file=creds)) is None  # sem nada: local
    save_credentials(creds, Credentials(url=URL + "/", token=TOKEN))
    t = resolve_target(make_settings(credentials_file=creds))
    assert t == RemoteTarget(url=URL, token=TOKEN, origin="arquivo") and TOKEN not in repr(t)
    assert resolve_target(make_settings(credentials_file=creds, cli_mode="local")) is None
    # o ambiente vence o arquivo; o token do arquivo não vale para outra URL
    t = resolve_target(make_settings(credentials_file=creds, remote_url="https://outro.exemplo.com",
                                     remote_token=SecretStr("cix_env")))
    assert t is not None and (t.url, t.token, t.origin) == ("https://outro.exemplo.com", "cix_env", "ambiente")
    with pytest.raises(ValueError, match="sem token para https://outro"):
        resolve_target(make_settings(credentials_file=creds, remote_url="https://outro.exemplo.com"))
    with pytest.raises(ValueError, match="CODE_INDEX_CLI_MODE=remote"):
        resolve_target(make_settings(credentials_file=tmp_path / "nada.toml", cli_mode="remote"))


def test_settings_do_ambiente(monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: Path) -> None:
    monkeypatch.setenv("CODE_INDEX_URL", URL)
    monkeypatch.setenv("CODE_INDEX_TOKEN", TOKEN)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.remote_url == URL and s.remote_token is not None and s.remote_token.get_secret_value() == TOKEN
    assert s.credentials_file == tmp_path / "code-index" / "credentials.toml"
    assert TOKEN not in repr(s)


# ---------------------------------------------------------------- servidor falso

Handler = Callable[[httpx.Request], httpx.Response]


class FakeApi:
    """Rotas falsas: (método, caminho) -> função(request) -> Response. Guarda as requisições."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, path: str, handler: Handler | Any, status: int = 200) -> None:
        if callable(handler):
            self.routes[(method, path)] = handler
        else:
            self.routes[(method, path)] = lambda _r, body=handler: httpx.Response(status, json=body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)
        fn = self.routes.get((request.method, request.url.path))
        if fn is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        return fn(request)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(RemoteService, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(RemoteService, "poll_seconds", 0.0)
    return fake


def _svc(**kw: Any) -> RemoteService:
    return RemoteService(RemoteTarget(url=URL, token=TOKEN), **kw)


async def _call(coro_fn: Callable[[RemoteService], Any], **kw: Any) -> Any:
    async with _svc(**kw) as svc:
        return await coro_fn(svc)


# ---------------------------------------------------------------- erros HTTP

@pytest.mark.parametrize("status,body,headers,expected", [
    (401, {"detail": "token inválido, expirado ou revogado"}, {}, "não autenticado em https://ci.exemplo.com"),
    (403, {"detail": "requer o scope admin"}, {}, "sem permissão: requer o scope admin"),
    (429, {"detail": "limite de requisições (sync) atingido"}, {"Retry-After": "42"}, "Tente de novo em 42 s"),
    (500, {"detail": "Internal Server Error"}, {}, "erro no servidor (HTTP 500"),
    (503, None, {}, "servidor indisponível (HTTP 503)"),
    (400, {"detail": "repositório desconhecido: x"}, {}, "repositório desconhecido: x"),
    (422, {"detail": [{"loc": ["body", "top_k"], "msg": "deve ser <= 50"}]}, {}, "pedido inválido: top_k: deve ser <= 50"),
    (307, None, {"Location": "https://login.exemplo.com/"}, "redirecionou para https://login.exemplo.com/"),
])
def test_erros_http_viram_mensagens(api: FakeApi, status: int, body: Any, headers: dict[str, str],
                                    expected: str) -> None:
    api.on("GET", "/api/v1/orgs", lambda _r: httpx.Response(status, json=body, headers=headers)
           if body is not None else httpx.Response(status, text="<html>oops</html>", headers=headers))
    with pytest.raises(RemoteError) as e:
        asyncio.run(_call(lambda s: s.org_summary()))
    assert expected in str(e.value) and e.value.status == status and "<html>" not in str(e.value)
    if status == 429:
        assert e.value.retry_after == 42


def test_falha_de_rede(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    monkeypatch.setattr(RemoteService, "transport", httpx.MockTransport(boom))
    with pytest.raises(RemoteError, match="não foi possível conectar a https://ci.exemplo.com") as e:
        asyncio.run(_call(lambda s: s.org_summary()))
    assert e.value.status is None and TOKEN not in str(e.value)


def test_resposta_que_nao_e_json(api: FakeApi) -> None:
    api.on("GET", "/api/v1/orgs", lambda _r: httpx.Response(200, text="<html>proxy</html>"))
    with pytest.raises(RemoteError, match="não é JSON"):
        asyncio.run(_call(lambda s: s.org_summary()))


# ---------------------------------------------------------------- métodos

def test_busca_monta_o_corpo_e_devolve_hits(api: FakeApi) -> None:
    hit = {"repo": "r", "path": "a.py", "language": "python", "start_line": 1, "end_line": 2, "score": 0.5,
           "code": "x", "campo_novo_do_servidor": 1}
    api.on("POST", "/api/v1/search", [hit])
    hits = asyncio.run(_call(lambda s: s.search("ola", top_k=3, repos=["r"], candidates=60, rerank=False)))
    assert hits[0].repo == "r" and hits[0].to_dict()["code"] == "x"
    body = json.loads(api.requests[0].content)
    assert body == {"query": "ola", "top_k": 3, "mode": "hybrid", "rerank": False, "include_linked_docs": True,
                    "repos": ["r"], "candidates": 60}


def test_parametros_repetidos_e_sem_none(api: FakeApi) -> None:
    api.on("GET", "/api/v1/symbols", [])
    asyncio.run(_call(lambda s: s.find_symbol("Foo", repos=["a", "b"], limit=5)))
    q = api.requests[0].url.params
    assert q.get_list("repo") == ["a", "b"] and q["limit"] == "5" and "kind" not in q and "language" not in q


def test_leitura_de_arquivo_e_caminho(api: FakeApi) -> None:
    api.on("GET", "/api/v1/files/meu-repo/src/a b.py", lambda _r: httpx.Response(200, text="linha\n"))
    text = asyncio.run(_call(lambda s: s.read_file("meu-repo", "src/a b.py", 2, 3)))
    assert text == "linha\n"
    assert api.requests[0].url.raw_path.startswith(b"/api/v1/files/meu-repo/src/a%20b.py?")
    assert api.requests[0].url.params["start_line"] == "2"


def test_404_vira_falso_onde_a_cli_espera(api: FakeApi) -> None:
    api.on("DELETE", "/api/v1/docs/links", {"detail": "vínculo não encontrado"}, status=404)
    api.on("DELETE", "/api/v1/docs/ignored", {"ignored": False})
    api.on("GET", "/api/v1/repos/x/card", {"detail": "sem cartão nem fatos para este repo"}, status=404)
    api.on("GET", "/api/v1/repos/y/card", {"detail": "repositório desconhecido: y"}, status=404)

    async def go(s: RemoteService) -> None:
        assert await s.unlink_doc("wiki", "P", "r") is False
        assert await s.unignore_doc("wiki", "P") is True
        assert await s.repo_card("x") is None
        with pytest.raises(RemoteError, match="desconhecido"):
            await s.repo_card("y")

    asyncio.run(_call(go))


def _job(status: str, **extra: Any) -> dict[str, Any]:
    return {"id": "j1", "status": status, "result": None, "error": None, "progress": None, **extra}


def test_acompanha_o_job_com_rate_limit_e_progresso(api: FakeApi) -> None:
    replies = iter([
        httpx.Response(200, json=_job("queued")),
        httpx.Response(429, json={"detail": "limite"}, headers={"Retry-After": "0"}),
        httpx.Response(200, json=_job("running", progress={"phase_label": "indexando", "repos_done": 0,
                                                              "repos_total": 1, "files_done": 3, "files_total": 9})),
        httpx.Response(503, text="reiniciando"),
        httpx.Response(200, json=_job("done", result={"selected": ["r"], "indexed": {"r": "abc1234def"},
                                                       "errors": {}, "discovered": {}, "remaining": 2})),
    ])
    api.on("GET", "/api/v1/sync/jobs/j1", lambda _r: next(replies))
    out = io.StringIO()
    res = asyncio.run(_call(lambda s: s.job_result("j1"), progress=JobProgress(out)))
    assert res.indexed == {"r": "abc1234def"} and res.remaining == 2
    lines = out.getvalue().splitlines()
    assert lines == ["job j1: na fila do worker...", "job j1: indexando (0/1 repos, 3/9 arquivos)"]


def test_job_que_falhou_sem_resultado(api: FakeApi) -> None:
    api.on("GET", "/api/v1/sync/jobs/j1", _job("failed", error="falhou após 3 tentativa(s): x"))
    with pytest.raises(RemoteError, match="o job j1 falhou: falhou após 3"):
        asyncio.run(_call(lambda s: s.job_result("j1")))


def test_sync_remoto_envia_lote_padrao_ou_tudo(api: FakeApi) -> None:
    api.on("POST", "/api/v1/sync", {"selected": ["a"], "indexed": {}, "errors": {}, "discovered": {}, "remaining": 0})

    async def go(s: RemoteService) -> None:
        await s.sync(["a"], batch="default", dry_run=True)
        await s.sync(None, batch=None, dry_run=True)
        await s.sync(None, batch=3, dry_run=True, do_discover=False)

    asyncio.run(_call(go))
    bodies = [json.loads(r.content) for r in api.requests]
    assert "batch" not in bodies[0] and bodies[0]["all"] is False and bodies[0]["repos"] == ["a"]
    assert bodies[1]["all"] is True and bodies[1]["repos"] is None
    assert bodies[2]["batch"] == 3 and bodies[2]["discover"] is False


def test_vetores_acompanha_a_rodada(api: FakeApi) -> None:
    run = {"started_at": "2026-01-01T00:00:00", "status": "running", "result": None, "error": None}
    api.on("POST", "/api/v1/vectors/embed", {"pending": 3, "last_run": run})
    done = {**run, "status": "done", "result": {"embedded": 3, "deleted": 0, "seconds": 1.0, "embed_tokens": 9,
                                                "cost_usd": 0.0, "pending": 3}}
    api.on("GET", "/api/v1/vectors/status", {"pending": 0, "last_run": done})
    res = asyncio.run(_call(lambda s: s.embed_vectors(limit=3)))
    assert res["embedded"] == 3 and res["dry_run"] is False
    assert json.loads(api.requests[0].content) == {"limit": 3}


def test_vetores_em_outra_replica(api: FakeApi) -> None:
    api.on("POST", "/api/v1/vectors/embed", {"pending": 3, "last_run": {"started_at": "t1", "status": "running"}})
    api.on("GET", "/api/v1/vectors/status", {"pending": 3, "last_run": None})
    assert asyncio.run(_call(lambda s: s.embed_vectors())) == {"detached": True, "pending": 3}


# ---------------------------------------------------------------- comandos (main), com a mesma saída

@pytest.fixture
def remote_settings(make_settings: Callable[..., Settings], tmp_path: Path) -> Settings:
    return make_settings(remote_url=URL, remote_token=SecretStr(TOKEN), credentials_file=tmp_path / "creds.toml")


def run_cli(settings: Settings, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(cli, "bootstrap", lambda: settings)
    monkeypatch.setattr("sys.argv", ["code-index", *argv])
    try:
        cli.main()
    except SystemExit as e:  # sys.exit("erro: ...") como o interpretador faria: texto na saída de erro, código 1
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        return e.code or 0
    return 0


def test_repo_list_remoto(api: FakeApi, remote_settings: Settings, monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture[str]) -> None:
    row = {"name": "org__api", "org": "org", "status": "indexed", "language": "java", "source": None,
           "commit": "abc1234", "indexed_at": "2026-01-02T03:04:05", "pushed_at": None, "outdated": False,
           "files": 10, "chunks": 20, "error": None}
    api.on("GET", "/api/v1/repos", [row])
    assert run_cli(remote_settings, monkeypatch, "repo", "list", "-s", "indexed") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"{'org__api':<50} {'indexed':<8} {'abc1234':<8} {'2026-01-02 03:04':<16}    10 arq     20 chunks"
    assert "1 repos: indexed 1" in out
    assert api.requests[0].url.params["status"] == "indexed"


def test_sync_remoto_acompanha_e_sai_com_erro(api: FakeApi, remote_settings: Settings,
                                              monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(RemoteService, "transport", httpx.MockTransport(api))
    api.on("POST", "/api/v1/sync", {"job_id": "j1", "status": "queued", "url": "/api/v1/sync/jobs/j1"}, status=202)
    api.on("GET", "/api/v1/sync/jobs/j1", _job("done", result={
        "selected": ["a", "b"], "indexed": {"a": "abcdef0123"}, "errors": {"b": "clone falhou\ndetalhe"},
        "discovered": {}, "remaining": 0}))
    assert run_cli(remote_settings, monkeypatch, "sync", "a", "b", "--no-discover") == 1  # erro com nomes pedidos
    cap = capsys.readouterr()
    assert "ok    a @ abcdef0" in cap.out and "ERRO  b: clone falhou" in cap.err
    body = json.loads(api.requests[0].content)
    assert body["repos"] == ["a", "b"] and body["discover"] is False and "batch" not in body


def test_sync_no_wait(api: FakeApi, remote_settings: Settings, monkeypatch: pytest.MonkeyPatch,
                      capsys: pytest.CaptureFixture[str]) -> None:
    api.on("POST", "/api/v1/sync", {"job_id": "j9", "status": "queued", "url": "/api/v1/sync/jobs/j9"}, status=202)
    assert run_cli(remote_settings, monkeypatch, "sync", "--no-wait", "-b", "5") == 0
    assert "job j9 na fila (queued)" in capsys.readouterr().out
    assert json.loads(api.requests[0].content)["batch"] == 5 and len(api.requests) == 1


def test_comando_local_only_recusa_no_modo_remoto(api: FakeApi, remote_settings: Settings,
                                                  monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["token", "create", "x", "--owner", "a@b.com"], ["user", "disable", "a@b.com"], ["watch"],
                 ["user", "set", "a@b.com", "ui.theme=dark"], ["eval"]):
        assert run_cli(remote_settings, monkeypatch, *argv) == 1
        err = capsys.readouterr().err
        assert "não roda no modo remoto" in err and "kubectl exec" in err, argv
    assert api.requests == []


def test_erro_http_no_comando(api: FakeApi, remote_settings: Settings, monkeypatch: pytest.MonkeyPatch,
                              capsys: pytest.CaptureFixture[str]) -> None:
    api.on("POST", "/api/v1/repos", {"detail": "requer o scope admin"}, status=403)
    assert run_cli(remote_settings, monkeypatch, "repo", "add", "org/x") == 1
    assert capsys.readouterr().err.strip() == "erro: sem permissão: requer o scope admin"


def test_login_grava_sem_mostrar_o_token(api: FakeApi, make_settings: Callable[..., Settings], tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    me = {"subject": "dev@example.com", "kind": "agent", "scopes": ["search:read"], "token_name": "cli",
          "provider": None}
    api.on("GET", "/api/v1/me", me)
    creds = tmp_path / "cfg" / "credentials.toml"
    settings = make_settings(credentials_file=creds)
    monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN + "\n"))
    assert run_cli(settings, monkeypatch, "login", "--url", URL + "/api/v1") == 0
    cap = capsys.readouterr()
    assert TOKEN not in cap.out + cap.err and "dev@example.com" in cap.out
    assert load_credentials(creds) == Credentials(url=URL, token=TOKEN)
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    # o modo remoto passa a valer: whoami usa o arquivo
    assert run_cli(settings, monkeypatch, "whoami") == 0
    out = capsys.readouterr().out
    assert f"servidor: {URL} (configurado pelo arquivo)" in out and TOKEN not in out
    # --local ignora o servidor
    assert run_cli(settings, monkeypatch, "--local", "whoami") == 0
    assert "modo local" in capsys.readouterr().out
    assert run_cli(settings, monkeypatch, "logout") == 0
    assert not creds.exists() and "credenciais apagadas" in capsys.readouterr().out


def test_login_recusa_token_invalido_e_url_com_token(api: FakeApi, make_settings: Callable[..., Settings],
                                                     tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    api.on("GET", "/api/v1/me", {"detail": "token inválido, expirado ou revogado"}, status=401)
    creds = tmp_path / "credentials.toml"
    settings = make_settings(credentials_file=creds)
    assert run_cli(settings, monkeypatch, "login", "--url", URL, "--token", TOKEN) == 1
    assert "não autenticado" in capsys.readouterr().err and not creds.exists()
    assert run_cli(settings, monkeypatch, "login", "--url", f"{URL}/?token={TOKEN}", "--token", TOKEN) == 1
    err = capsys.readouterr().err
    assert "não pode levar token" in err and not creds.exists()


def test_login_avisa_http_fora_de_loopback(make_settings: Callable[..., Settings], tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"subject": "a@b.com", "kind": "agent", "scopes": [], "token_name": None})

    monkeypatch.setattr(RemoteService, "transport", httpx.MockTransport(handler))
    settings = make_settings(credentials_file=tmp_path / "c.toml")
    assert run_cli(settings, monkeypatch, "login", "--url", "http://ci.interno:8765", "--token", TOKEN) == 0
    assert "sem TLS" in capsys.readouterr().err


# ---------------------------------------------------------------- paridade: todo método usado pela CLI existe

_LOCAL_ONLY_CALLS = {
    "create_agent_token", "set_user_disabled", "user_settings", "update_user_settings",  # operador / navegador
    "run_sync_worker", "load_eval_cases", "evaluate", "eval_meta", "eval_summarized",      # worker / eval
    "register_openapi",                                                                    # remoto usa openapi_index
}


def test_cliente_remoto_tem_os_metodos_que_os_comandos_usam() -> None:
    cli_dir = Path(cli.__file__).parent
    used: set[str] = set()
    for path in cli_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "svc"):
                used.add(node.func.attr)
    missing = sorted(m for m in used - _LOCAL_ONLY_CALLS if not hasattr(RemoteService, m))
    assert used and not missing, f"faltam no RemoteService: {missing}"
    assert remote_mod.API == "/api/v1"
