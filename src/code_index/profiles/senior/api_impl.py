"""Operação do SDL → classe que a implementa, pela convenção do SDK da plataforma (perfil `senior`), e as rotas
HTTP das operações (`/<serviço>/{queries|actions|signals}/<operação>`, a fonte OpenAPI liga página → operação).

A operação `listBlacklistedEmails` é tratada pela classe `ListBlacklistedEmailsHandler` (às vezes
`...HandlerImpl`). Operações versionadas (`login__9`) usam `LoginV9Handler`.
"""

from __future__ import annotations

import re

from code_index.profiles.api import ContractRoutes, ImplConvention

_VERSIONED = re.compile(r"^(.+?)__(\d+)$")


def handler_names(operation: str) -> list[str]:
    """Nomes de classe candidatos (minúsculos), do mais provável para o menos."""
    m = _VERSIONED.match(operation)
    if m:
        base, version = m.group(1).lower(), m.group(2)
        return [f"{base}v{version}handler", f"{base}{version}handler", f"{base}v{version}handlerimpl"]
    op = operation.lower()
    return [f"{op}handler", f"{op}handlerimpl"]


# Rotas publicadas pela plataforma: POST /<serviço>/queries/<operação> (e actions, signals).
CONTRACT_ROUTES = ContractRoutes((("queries", "query"), ("actions", "action"), ("signals", "signal")))

IMPL_CONVENTION = ImplConvention(
    names=handler_names,
    rule="convenção do SDK (`<operacao>Handler`)",
    symbol="classe handler",
    scope="da plataforma",
    example_service="email",
    example_operation="listBlacklistedEmails",
)
