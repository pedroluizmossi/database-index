"""CLI: `code-index user` (usuários: uso, acesso e configurações; operador local)."""

from __future__ import annotations

import argparse
import json
from typing import Any

from ...services import open_service
from ...settings import Settings
from .common import Subparsers


async def _cmd_user_list(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        users = await svc.users_overview(a.days)
    if not users:
        print("nenhum usuário ainda (aparecem no primeiro login ou ao receber um token).")
    print(f"{'usuário':<38} {'papel':<8} {'estado':<10} {'chamadas':>8} {'mcp':>6} {'US$':>8}  último acesso")
    for u in users:
        seen = u["last_seen_at"] or u["last_login_at"]
        last = str(seen)[:16].replace("T", " ") if seen else "nunca"
        state = "ativo" if u["active"] else "desativado"
        print(f"{u['email']:<38} {u['role']:<8} {state:<10} {u['calls']:>8} {u['mcp_calls']:>6} "
              f"{u['cost_usd']:>8.4f}  {last}")
    print(f"(uso nos últimos {a.days} dias)")


async def _cmd_user_access(a: argparse.Namespace, settings: Settings) -> None:
    disable = a.action == "disable"
    async with open_service(settings) as svc:
        res = await svc.set_user_disabled(None, a.email, disable)
        await svc.audit(None, f"user:{a.action}:local", params={"email": a.email})
    print(f"{res['email']}: {'desativado (nenhuma credencial vale)' if disable else 'reativado'}.")


def _print_user_settings(info: dict[str, Any]) -> None:
    name = f" ({info['display_name']})" if info["display_name"] else ""
    print(f"{info['email']}{name}")
    changed = set(info["changed"])
    for item in info["schema"]:
        key = item["key"]
        value = json.dumps(info["settings"][key], ensure_ascii=False)
        origin = "alterada" if key in changed else "padrão"
        print(f"  {key:<28} {value:<14} {origin:<9} {item['label']}")


async def _cmd_user_settings(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        _print_user_settings(await svc.user_settings(None, email=a.email))


def _parse_setting(raw: str) -> tuple[str, Any]:
    """'chave=valor'. O valor é lido como JSON (10, true, "dark"); se não for JSON, vale como texto."""
    key, sep, value = raw.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"use chave=valor: {raw}")
    try:
        return key.strip(), json.loads(value)
    except ValueError:
        return key.strip(), value


async def _cmd_user_set(a: argparse.Namespace, settings: Settings) -> None:
    changes = dict(_parse_setting(raw) for raw in a.settings)
    if any(v is None for v in changes.values()):
        raise ValueError("para voltar ao padrão, use 'user unset'")
    async with open_service(settings) as svc:
        info = await svc.update_user_settings(None, changes, email=a.email)
        await svc.audit(None, "settings:update:local", params={"email": a.email, "keys": sorted(changes)})
    _print_user_settings(info)


async def _cmd_user_unset(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        info = await svc.update_user_settings(None, dict.fromkeys(a.keys), email=a.email)
        await svc.audit(None, "settings:update:local", params={"email": a.email, "keys": sorted(a.keys)})
    _print_user_settings(info)


def register(sub: Subparsers, settings: Settings) -> None:
    us = sub.add_parser("user", help="usuários: uso, acesso e configurações").add_subparsers(required=True)
    u = us.add_parser("list", help="lista os usuários, com papel, estado e uso no período")
    u.add_argument("--days", type=int, default=30)
    u.set_defaults(fn=_cmd_user_list)
    for action, text in (("disable", "desativa o usuário (nenhuma credencial dele vale)"), ("enable", "reativa o usuário")):
        u = us.add_parser(action, help=text)
        u.add_argument("email")
        u.set_defaults(fn=_cmd_user_access, action=action)
    u = us.add_parser("settings", help="configurações de um usuário (valor atual e se foi alterada)")
    u.add_argument("email")
    u.set_defaults(fn=_cmd_user_settings)
    u = us.add_parser("set", help="altera configurações de um usuário")
    u.add_argument("email")
    u.add_argument("settings", nargs="+", metavar="chave=valor", help="ex.: ui.theme=dark ui.results_per_page=16")
    u.set_defaults(fn=_cmd_user_set)
    u = us.add_parser("unset", help="volta configurações de um usuário ao padrão")
    u.add_argument("email")
    u.add_argument("keys", nargs="+", metavar="chave")
    u.set_defaults(fn=_cmd_user_unset)
