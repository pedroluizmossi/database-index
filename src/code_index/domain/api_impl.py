"""Operação da API (SDL da plataforma Senior) → classe que a implementa (puro, sem I/O).

Convenção do SDK: a operação `listBlacklistedEmails` é tratada pela classe `ListBlacklistedEmailsHandler`
(às vezes `...HandlerImpl`). Operações versionadas (`login__9`) usam `LoginV9Handler`. Casa por nome,
sem LLM; o que não casar fica para um agent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .repo_card import is_test_path

_VERSIONED = re.compile(r"^(.+?)__(\d+)$")
_TITLE = re.compile(r"^[A-Z]+\s+/([^/\s]+)/(queries|actions|signals)/([A-Za-z_][\w]*)")
OP_KINDS = (("queries", "query"), ("actions", "action"), ("signals", "signal"))


def handler_names(operation: str) -> list[str]:
    """Nomes de classe candidatos (minúsculos), do mais provável para o menos."""
    m = _VERSIONED.match(operation)
    if m:
        base, version = m.group(1).lower(), m.group(2)
        return [f"{base}v{version}handler", f"{base}{version}handler", f"{base}v{version}handlerimpl"]
    op = operation.lower()
    return [f"{op}handler", f"{op}handlerimpl"]


def match_handlers(
    services: Sequence[Mapping[str, Any]], classes: Iterable[tuple[str, str, int]],
) -> list[dict[str, Any]]:
    """`services`: fatos `sdl` do repo; `classes`: (nome, path, linha) das classes do repo.
    Devolve uma linha por operação casada: domain, service, operation, kind, symbol, path, line."""
    by_name: dict[str, tuple[str, str, int]] = {}
    for name, path, line in classes:
        current = by_name.get(name.lower())
        # prefere arquivo fora de teste e, entre iguais, o caminho mais curto
        if current is None or (is_test_path(current[1]), len(current[1])) > (is_test_path(path), len(path)):
            by_name[name.lower()] = (name, path, line)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for svc in services:
        for bucket, kind in OP_KINDS:
            for op in svc.get(bucket, []):
                op_key = (svc["domain"], svc["service"], op)
                if op_key in seen:
                    continue
                for candidate in handler_names(op):
                    found = by_name.get(candidate)
                    if found:
                        seen.add(op_key)
                        out.append({"domain": svc["domain"], "service": svc["service"], "operation": op,
                                    "kind": kind, "symbol": found[0], "path": found[1], "line": found[2]})
                        break
    return out


def _title_ref(title: str) -> tuple[str, str, str] | None:
    """Referência digitada como título de página de API: 'POST /email/queries/x' → ('email', 'query', 'x')."""
    m = _TITLE.match(title.strip())
    if not m:
        return None
    kind = dict(OP_KINDS)[m.group(2)]
    return m.group(1), kind, m.group(3)


def parse_operation_ref(ref: str) -> tuple[str | None, str]:
    """Aceita 'listBlacklistedEmails', 'email.listBlacklistedEmails', 'email/listBlacklistedEmails' ou
    'POST /email/queries/listBlacklistedEmails'. Devolve (serviço ou None, operação)."""
    raw = ref.strip()
    from_title = _title_ref(raw)
    if from_title:
        return from_title[0], from_title[2]
    parts = [p for p in re.split(r"[./]", raw) if p and p not in ("queries", "actions", "signals")]
    if not parts:
        raise ValueError("informe a operação (ex.: listBlacklistedEmails ou email.listBlacklistedEmails)")
    return (parts[-2] if len(parts) >= 2 else None), parts[-1]
