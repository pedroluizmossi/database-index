"""Tipos e utilitários comuns aos comandos da CLI (formatação e a escolha local/remoto do serviço)."""

from __future__ import annotations

import argparse
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeAlias, cast

from ...services import CodeIndexService, open_service
from ...settings import Settings
from .remote import JobProgress, RemoteService, resolve_target, warn_insecure

CommandFn = Callable[[argparse.Namespace, Settings], Coroutine[Any, Any, None]]
# Retorno de `ArgumentParser.add_subparsers()`: cada área registra nele os seus comandos.
Subparsers: TypeAlias = "argparse._SubParsersAction[argparse.ArgumentParser]"

LOCAL_ONLY_ATTR = "_code_index_local_only"


def print_json(data: Any, *, default: Callable[[Any], Any] | None = None) -> None:
    """Imprime `data` como JSON indentado, sem escapar acentos."""
    print(json.dumps(data, indent=2, ensure_ascii=False, default=default))


def local_only(hint: str) -> Callable[[CommandFn], CommandFn]:
    """Marca um comando que só roda com acesso direto ao banco (operador). No modo remoto, `main` recusa com
    `hint` (onde fazer isso: interface web ou `kubectl exec` no pod)."""

    def mark(fn: CommandFn) -> CommandFn:
        setattr(fn, LOCAL_ONLY_ATTR, hint)
        return fn

    return mark


@asynccontextmanager
async def open_backend(settings: Settings) -> AsyncIterator[CodeIndexService]:
    """O serviço dos comandos: local (abre o banco) ou, com servidor configurado (CODE_INDEX_URL ou
    `code-index login`), o cliente de /api/v1 com os mesmos métodos. Os comandos não sabem a diferença."""
    target = resolve_target(settings)
    if target is None:
        async with open_service(settings) as svc:
            yield svc
        return
    warn_insecure(target.url)
    async with RemoteService(target, timeout=settings.remote_timeout_seconds, progress=JobProgress()) as remote:
        yield cast(CodeIndexService, remote)


def as_remote(svc: object) -> RemoteService | None:
    """O cliente remoto, se `svc` for um (para os poucos comandos que se comportam diferente, ex.: --no-wait)."""
    return svc if isinstance(svc, RemoteService) else None
