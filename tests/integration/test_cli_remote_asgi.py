"""CLI remota contra o app real (FastAPI em ASGI, sem porta): os comandos falam com /api/v1 e imprimem o mesmo
que no modo local.

Isolado como os outros testes de integração: schema code_index_test, pastas em tmp_path, LLM falso (sem Voyage).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from test_http_api import ADMIN_EMAIL, USER_EMAIL, _setup_tokens

from code_index.interfaces.cli import _refuse_local_only, build_parser
from code_index.interfaces.cli.remote import RemoteService
from code_index.interfaces.http.app import create_app
from code_index.settings import Settings, use_settings

pytestmark = pytest.mark.integration


async def run(settings: Settings, *argv: str) -> tuple[int, str, str]:
    """Roda um comando como o `main` (sem asyncio.run próprio): (código de saída, stdout, stderr)."""
    a: argparse.Namespace = build_parser(settings).parse_args(list(argv))
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            _refuse_local_only(a.fn, settings)
            await a.fn(a, settings)
        except SystemExit as e:
            if isinstance(e.code, str):
                print(e.code, file=sys.stderr)
            code = e.code if isinstance(e.code, int) else 1
        except (ValueError, RuntimeError, PermissionError) as e:
            print(f"erro: {e}", file=sys.stderr)
            code = 1
    return code, out.getvalue(), err.getvalue()


def test_cli_remota_no_app_real(int_settings: Settings, fake_llm: Any, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    server = int_settings.model_copy(update={
        "auth_secret": SecretStr("s" * 48), "admin_emails": [ADMIN_EMAIL], "google_allowed_emails": [USER_EMAIL],
    })
    use_settings(server)
    tokens, _ = asyncio.run(_setup_tokens(server))
    repo_dir = tmp_path / "meu-repo"
    repo_dir.mkdir()
    (repo_dir / "app.py").write_text("def ola():\n    return 'mundo'\n")
    app = create_app(server)
    monkeypatch.setattr(RemoteService, "transport", httpx.ASGITransport(app=app))
    monkeypatch.setattr(RemoteService, "poll_seconds", 0.2)

    def cli(token: str) -> Settings:  # a máquina da pessoa: só URL e token, sem banco
        return server.model_copy(update={
            "remote_url": "http://testserver", "remote_token": SecretStr(tokens[token]),
            "credentials_file": tmp_path / "nenhum.toml", "database_url": "postgres://ninguem@127.0.0.1:1/nada"})

    admin, leitor = cli("admin-bot"), cli("leitor")
    local = server.model_copy(update={"cli_mode": "local"})

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            code, out, _ = await run(admin, "whoami")
            assert code == 0 and "servidor: http://testserver" in out and ADMIN_EMAIL in out
            assert tokens["admin-bot"] not in out

            code, out, err = await run(admin, "repo", "add", str(repo_dir), "--name", "meu-repo")
            assert code == 0, err
            assert out.startswith(f"adicionado: meu-repo -> {repo_dir}")

            code, out, err = await run(admin, "sync", "meu-repo", "--no-discover")
            assert code == 0, err
            assert "ok    meu-repo" in out and "job " in err  # progresso na saída de erro

            # a mesma saída que a CLI local, lendo o mesmo banco
            for argv in (["repo", "list"], ["symbol", "ola"], ["grep", "mundo"], ["docs", "list"],
                         ["relations", "stats"], ["owner", "list"], ["search", "ola", "-m", "keyword", "--json"]):
                remote_res, local_res = await run(leitor, *argv), await run(local, *argv)
                assert remote_res[0] == local_res[0] == 0, (argv, remote_res, local_res)
                assert remote_res[1] == local_res[1], argv
            code, out, _ = await run(leitor, "show", "meu-repo/app.py:1-1")
            assert code == 0 and "def ola():" in out and out == (await run(local, "show", "meu-repo/app.py:1-1"))[1]

            # sem permissão, operação de operador e token inválido: mensagens claras, sem tocar no banco
            code, _, err = await run(leitor, "repo", "add", "org/outro")
            assert code == 1 and "erro: sem permissão: scope necessário: admin" in err and "sem TLS" in err
            code, _, err = await run(leitor, "token", "create", "x", "--owner", USER_EMAIL)
            assert code == 1 and "não roda no modo remoto" in err
            code, _, err = await run(cli("revogado"), "repo", "list")
            assert code == 1 and "não autenticado em http://testserver" in err and "code-index login" in err

            code, out, err = await run(admin, "sync", "--no-wait", "--no-discover", "--dry-run")
            assert code == 0, err
            code, out, err = await run(admin, "sync", "meu-repo", "--no-discover", "--no-wait")
            assert code == 0 and out.startswith("job "), err
            job_id = out.split()[1]
            code, out, err = await run(admin, "sync", "--job", job_id)
            assert code == 0 and "ok    meu-repo" in out, err

    asyncio.run(scenario())
