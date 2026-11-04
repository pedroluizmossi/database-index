"""Regras de autorização (puras): scopes, principal autenticado e quem pode conceder o quê.

Scopes:
  search:read  — buscar, ler arquivos, listar repos/orgs           (agents, por padrão)
  index:write  — sync/reindex (gera custo na Voyage)              (automações específicas)
  admin        — gerenciar orgs/repos/tokens; implica os demais   (pessoas listadas como admin)

Regras de emissão de token:
  - só pessoas logadas via Google (kind="session") emitem tokens; um token de agent nunca
    emite outro token (evita escalada e cadeia de credenciais);
  - ninguém concede um scope que não tem.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

SEARCH_READ = "search:read"
INDEX_WRITE = "index:write"
ADMIN = "admin"
ALL_SCOPES = (SEARCH_READ, INDEX_WRITE, ADMIN)

TokenKind = Literal["agent", "session"]


def validate_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    out = tuple(dict.fromkeys(scopes))
    unknown = [s for s in out if s not in ALL_SCOPES]
    if unknown:
        raise ValueError(f"scope desconhecido: {', '.join(unknown)} (válidos: {', '.join(ALL_SCOPES)})")
    if not out:
        raise ValueError("informe ao menos um scope")
    return out


def effective_scopes(scopes: Iterable[str]) -> frozenset[str]:
    """`admin` implica todos os scopes."""
    s = frozenset(scopes)
    return frozenset(ALL_SCOPES) if ADMIN in s else s


def scopes_for_user(email: str, admin_emails: Iterable[str]) -> tuple[str, ...]:
    """Scopes de uma pessoa logada via Google: admins listados têm tudo; demais, só leitura."""
    admins = {e.lower() for e in admin_emails}
    return (ADMIN, INDEX_WRITE, SEARCH_READ) if email.lower() in admins else (SEARCH_READ,)


@dataclass(frozen=True)
class Principal:
    """Quem está fazendo a requisição."""

    subject: str                 # e-mail do dono (pessoa) do token/sessão
    kind: TokenKind
    scopes: frozenset[str]       # já efetivos (admin expandido)
    token_id: str | None = None
    token_name: str | None = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def actor(self) -> str:
        """Identificação para auditoria: e-mail, mais o nome do token quando é um agent."""
        return self.subject if self.kind == "session" else f"{self.subject} [{self.token_name}]"


def check_can_issue(issuer: Principal, requested: Iterable[str]) -> tuple[str, ...]:
    """Valida a emissão de um token de agent por `issuer`. Retorna os scopes normalizados."""
    scopes = validate_scopes(requested)
    if issuer.kind != "session":
        raise PermissionError("tokens de agent não podem emitir outros tokens; faça login com o Google")
    missing = [s for s in scopes if s not in issuer.scopes]
    if missing:
        raise PermissionError(f"você não tem os scopes: {', '.join(missing)}")
    return scopes
