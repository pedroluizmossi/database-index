"""CLI: `code-index db` (manutenção do banco: migrations)."""

from __future__ import annotations

import argparse

from ...services.db import migrate_database
from ...settings import Settings
from .common import Subparsers


async def _cmd_migrate(a: argparse.Namespace, settings: Settings) -> None:
    applied = await migrate_database(settings)
    if not applied:
        print(f"schema {settings.pg_schema}: migrations em dia (nada a aplicar).")
        return
    print(f"schema {settings.pg_schema}: {len(applied)} migration(s) aplicada(s):")
    for version in applied:
        print(f"  {version}")


def register(sub: Subparsers, settings: Settings) -> None:
    d = sub.add_parser("db", help="manutenção do banco (migrations)").add_subparsers(required=True)
    m = d.add_parser("migrate", help="aplica as migrations pendentes e sai (Job pre-install/pre-upgrade do Helm)")
    m.set_defaults(fn=_cmd_migrate)
