"""CLI: `code-index token` (tokens de agents; operador local, com acesso direto ao banco)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers


async def _cmd_token_create(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        token, info = await svc.create_agent_token(
            None, name=a.name, scopes=a.scopes, ttl_days=a.days, owner_email=a.owner,
        )
        await svc.audit(None, "token:create:local", params={"name": a.name, "owner": a.owner, "scopes": a.scopes})
    print(f"token criado: {info.name} (id {info.id}, scopes {', '.join(info.scopes)}, expira {info.expires_at:%Y-%m-%d})")
    print(f"\n    {token}\n")
    print("guarde agora: ele não será mostrado de novo.")


async def _cmd_token_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        tokens = await svc.list_tokens(None, include_inactive=a.all)
    if not tokens:
        print("nenhum token.")
    for t in tokens:
        state = "ativo" if t.active else ("revogado" if t.revoked_at else "expirado")
        used = f"{t.last_used_at:%Y-%m-%d %H:%M}" if t.last_used_at else "nunca"
        exp = f"{t.expires_at:%Y-%m-%d}" if t.expires_at else "-"
        print(f"{t.id}  {t.token_prefix}…  {state:<8} {t.name:<30} {t.owner_email:<30} "
              f"{','.join(t.scopes):<30} expira {exp}  último uso {used}")


async def _cmd_token_revoke(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        await svc.revoke_token(None, a.id)
        await svc.audit(None, "token:revoke:local", params={"id": a.id})
    print(f"token {a.id} revogado.")


def register(sub: Subparsers, settings: Settings) -> None:
    tk = sub.add_parser("token", help="tokens de agents (operador local)").add_subparsers(required=True)
    t = tk.add_parser("create", help="emite um token (valor exibido uma única vez)")
    t.add_argument("name", help="rótulo, ex.: 'claude-code pedro'")
    t.add_argument("--owner", required=True, help="e-mail do responsável")
    t.add_argument("--scopes", nargs="+", default=["search:read"],
                   help="search:read (padrão), index:write, enrich:write, admin")
    t.add_argument("--days", type=int, help=f"validade em dias (padrão {settings.token_ttl_days})")
    t.set_defaults(fn=_cmd_token_create)
    t = tk.add_parser("list", help="lista tokens de agents")
    t.add_argument("--all", action="store_true", help="inclui revogados/expirados")
    t.set_defaults(fn=_cmd_token_list)
    t = tk.add_parser("revoke", help="revoga um token pelo id")
    t.add_argument("id")
    t.set_defaults(fn=_cmd_token_revoke)
