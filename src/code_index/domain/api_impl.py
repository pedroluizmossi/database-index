"""Operação de contrato → símbolo que a implementa (puro, sem I/O).

O núcleo só conhece o formato genérico dos contratos (`domain/contracts`: domínio/serviço/grupo/nome). Quem diz
como se chama a classe que implementa uma operação é a convenção de um perfil de organização
(`ImplConvention`, gancho `impl_convention` do perfil): `names(operação)` dá os nomes candidatos, do mais
provável para o menos. Casa por nome, sem LLM; o que não casar fica para um agent. Sem perfil que ofereça uma
convenção não há implementações (a tool MCP `find_api_impl` nem é registrada).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import CALLABLE_GROUPS, GROUP_FIELDS
from .repo_card import is_test_path

# operação → nomes de classe candidatos (sem diferenciar maiúsculas), do mais provável para o menos
ImplNames = Callable[[str], Sequence[str]]

# grupos de operações chamáveis: (campo do serviço nos fatos, grupo) na ordem de exibição
OP_KINDS = tuple((GROUP_FIELDS[g], g) for g in CALLABLE_GROUPS)
_TITLE = re.compile(r"^[A-Z]+\s+/([^/\s]+)/(" + "|".join(f for f, _ in OP_KINDS) + r")/([A-Za-z_][\w]*)")

NO_IMPL_CONVENTION = ("nenhum perfil de organização ativo oferece implementações de operações "
                      "(veja CODE_INDEX_PROFILES)")


@dataclass(frozen=True)
class ImplConvention:
    """Convenção de nomes operação → implementação de um perfil, com os textos que as interfaces mostram.

    - `names`: nomes candidatos da classe que implementa a operação (`ImplNames`).
    - `rule`: a convenção em poucas palavras, completa "Casamento pela ..." (ex.: "convenção do SDK (`<op>Handler`)").
    - `symbol`: o que implementa ("classe", "classe handler").
    - `scope`: complemento de "operação da API" (ex.: "da plataforma"); vazio = nenhum.
    - `example_service`/`example_operation`: exemplos das descrições (tool MCP, CLI).
    """

    names: ImplNames
    rule: str
    symbol: str = "classe"
    scope: str = ""
    example_service: str = "pedidos"
    example_operation: str = "listarPedidos"

    @staticmethod
    def combine(conventions: Iterable[ImplConvention]) -> ImplConvention | None:
        """Convenções de vários perfis: nomes de todas (na ordem, sem repetir); textos da primeira."""
        items = tuple(conventions)
        if len(items) <= 1:
            return items[0] if items else None

        def names(operation: str) -> list[str]:
            return list(dict.fromkeys(n.lower() for c in items for n in c.names(operation)))

        first = items[0]
        return ImplConvention(names=names, rule=first.rule, symbol=first.symbol, scope=first.scope,
                              example_service=first.example_service, example_operation=first.example_operation)


def match_handlers(
    services: Sequence[Mapping[str, Any]], classes: Iterable[tuple[str, str, int]], impl_names: ImplNames,
) -> list[dict[str, Any]]:
    """`services`: serviços de contrato do repo (fatos `sdl`, `contracts.ContractService.to_dict`); `classes`:
    (nome, path, linha) das classes do repo; `impl_names`: a convenção do perfil.
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
                for candidate in impl_names(op):
                    found = by_name.get(candidate.lower())
                    if found:
                        seen.add(op_key)
                        out.append({"domain": svc["domain"], "service": svc["service"], "operation": op,
                                    "kind": kind, "symbol": found[0], "path": found[1], "line": found[2]})
                        break
    return out


def _title_ref(title: str) -> tuple[str, str, str] | None:
    """Referência digitada como título de página de API: 'POST /pedidos/queries/x' → ('pedidos', 'query', 'x')."""
    m = _TITLE.match(title.strip())
    if not m:
        return None
    kind = dict(OP_KINDS)[m.group(2)]
    return m.group(1), kind, m.group(3)


def parse_operation_ref(ref: str) -> tuple[str | None, str]:
    """Aceita 'listarPedidos', 'pedidos.listarPedidos', 'pedidos/listarPedidos' ou
    'POST /pedidos/queries/listarPedidos'. Devolve (serviço ou None, operação)."""
    raw = ref.strip()
    from_title = _title_ref(raw)
    if from_title:
        return from_title[0], from_title[2]
    parts = [p for p in re.split(r"[./]", raw) if p and p not in dict(OP_KINDS)]
    if not parts:
        raise ValueError("informe a operação (ex.: listarPedidos ou pedidos.listarPedidos)")
    return (parts[-2] if len(parts) >= 2 else None), parts[-1]
