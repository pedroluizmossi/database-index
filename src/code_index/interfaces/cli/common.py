"""Tipos e utilitários comuns aos comandos da CLI (só formatação)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Coroutine
from typing import Any, TypeAlias

from ...settings import Settings

CommandFn = Callable[[argparse.Namespace, Settings], Coroutine[Any, Any, None]]
# Retorno de `ArgumentParser.add_subparsers()`: cada área registra nele os seus comandos.
Subparsers: TypeAlias = "argparse._SubParsersAction[argparse.ArgumentParser]"


def print_json(data: Any, *, default: Callable[[Any], Any] | None = None) -> None:
    """Imprime `data` como JSON indentado, sem escapar acentos."""
    print(json.dumps(data, indent=2, ensure_ascii=False, default=default))
