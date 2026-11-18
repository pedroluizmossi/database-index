"""CLI: `code-index login`, `logout` e `whoami` (CLI remota: servidor e token da pessoa).

O token é lido de --token, da entrada padrão ou do prompt (sem eco), validado em GET /api/v1/me e gravado em
~/.config/code-index/credentials.toml (0600). Nunca é impresso.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from ...settings import Settings
from .common import Subparsers
from .remote import (
    Credentials,
    RemoteService,
    RemoteTarget,
    delete_credentials,
    normalize_url,
    resolve_target,
    save_credentials,
    warn,
    warn_insecure,
)


def _read_token() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("token (cix_..., não aparece ao digitar): ")
    return sys.stdin.readline()


def _describe(me: dict) -> str:
    name = f", token {me['token_name']!r}" if me.get("token_name") else ""
    return f"{me['subject']} ({me['kind']}{name}; scopes: {', '.join(me['scopes']) or '-'})"


async def _cmd_login(a: argparse.Namespace, settings: Settings) -> None:
    url = normalize_url(a.url or settings.remote_url or "")
    if a.token is not None:
        warn("--token fica no histórico do shell e visível em `ps`; prefira a entrada padrão "
             "(echo $T | code-index login --url ...) ou o prompt sem eco")
    token = (a.token if a.token is not None else _read_token()).strip()
    if not token:
        raise ValueError("token vazio: crie um na interface web (Tokens) e passe por --token ou pela entrada padrão")
    if any(c.isspace() for c in token):
        raise ValueError("token inválido (tem espaços): copie de novo da interface web")
    warn_insecure(url)
    async with RemoteService(RemoteTarget(url=url, token=token), timeout=settings.remote_timeout_seconds) as remote:
        me = await remote.me()
    path = save_credentials(settings.credentials_file, Credentials(url=url, token=token))
    print(f"conectado a {url} como {_describe(me)}.")
    print(f"credenciais gravadas em {path} (permissão 0600). A CLI agora usa este servidor; `code-index logout` desfaz.")
    if settings.remote_url or settings.remote_token is not None:
        warn("CODE_INDEX_URL/CODE_INDEX_TOKEN estão definidas no ambiente e valem antes do arquivo")


async def _cmd_logout(a: argparse.Namespace, settings: Settings) -> None:
    path = settings.credentials_file.expanduser()
    if delete_credentials(path):
        print(f"credenciais apagadas ({path}). O token continua válido no servidor: revogue na interface web "
              "(Tokens) se não for mais usar.")
    else:
        print("nenhuma credencial gravada.")
    if settings.remote_url or settings.remote_token is not None:
        warn("CODE_INDEX_URL/CODE_INDEX_TOKEN continuam definidas no ambiente: a CLI segue no modo remoto")


async def _cmd_whoami(a: argparse.Namespace, settings: Settings) -> None:
    target = resolve_target(settings)
    if target is None:
        print(f"modo local: a CLI abre o banco direto (schema {settings.pg_schema}). "
              "Para usar um servidor: `code-index login --url URL`.")
        return
    warn_insecure(target.url)
    async with RemoteService(target, timeout=settings.remote_timeout_seconds) as remote:
        me = await remote.me()
    print(f"servidor: {target.url} (configurado pelo {target.origin})")
    print(f"usuário: {_describe(me)}")


def register(sub: Subparsers, settings: Settings) -> None:
    lg = sub.add_parser("login", help="usa um servidor code-index (CLI remota): valida e grava URL e token")
    lg.add_argument("--url", help="endereço do servidor, ex.: https://code-index.exemplo.com (padrão CODE_INDEX_URL)")
    lg.add_argument("--token", help="token cix_... (evite: fica no histórico e em `ps`; sem isto, lê da entrada "
                                    "padrão ou pergunta sem eco)")
    lg.set_defaults(fn=_cmd_login)
    sub.add_parser("logout", help="apaga as credenciais gravadas pelo login (volta ao modo local)") \
        .set_defaults(fn=_cmd_logout)
    sub.add_parser("whoami", help="modo da CLI (local ou servidor) e quem é o dono do token") \
        .set_defaults(fn=_cmd_whoami)
