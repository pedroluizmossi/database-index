"""Banco: aplicar as migrations sem abrir o serviço inteiro (o Job de migrations do Helm, `code-index db migrate`)."""

from __future__ import annotations

from ..infra.db import create_pool, migrate
from ..settings import Settings


async def migrate_database(settings: Settings) -> list[str]:
    """Garante as extensões e aplica as migrations pendentes no schema configurado. Devolve as versões aplicadas
    agora (vazio = já estava em dia). Idempotente e seguro com vários processos (trava no banco)."""
    pool = await create_pool(settings)
    try:
        return await migrate(pool, settings)
    finally:
        await pool.close()
