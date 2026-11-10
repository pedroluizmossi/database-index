"""Regras de autorização (puras): scopes, principal autenticado e quem pode conceder o quê.

Scopes:
  search:read  — buscar, ler arquivos, listar repos/orgs           (agents, por padrão)
  index:write  — sync/reindex (gera custo na Voyage)              (automações específicas)
  admin        — gerenciar orgs/repos/tokens; implica os demais   (pessoas listadas como admin)

Tipos de token:
  session — pessoa logada no navegador (Google ou Microsoft), cookie HttpOnly
  agent   — emitido por uma pessoa na interface/CLI para um agent (Claude, Codex, Kiro...)
  oauth   — emitido a um cliente MCP pelo fluxo OAuth, após login e consentimento no navegador

Regras de emissão de token:
  - só pessoas logadas no navegador (kind="session") emitem tokens; um token de agent ou OAuth
    nunca emite outro token (evita escalada e cadeia de credenciais);
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

TokenKind = Literal["agent", "session", "oauth"]


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
    """Scopes de uma pessoa logada no navegador: admins listados têm tudo; demais, só leitura."""
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
    oauth_client_id: str | None = None   # só tokens OAuth: o cliente MCP que recebeu o token
    user_id: str | None = None           # pessoa dona da credencial (users.id)

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def actor(self) -> str:
        """Identificação para auditoria: e-mail, mais o nome do token quando é um agent."""
        return self.subject if self.kind == "session" else f"{self.subject} [{self.token_name}]"


def session_token_name(provider: str) -> str:
    """Nome do token de sessão: guarda o provedor do login ('google-session')."""
    return f"{provider}-session"


def session_provider(principal: Principal) -> str | None:
    """Provedor do login ('google', 'microsoft') de uma sessão de navegador; None para tokens de agent/OAuth."""
    if principal.kind != "session" or not principal.token_name:
        return None
    return principal.token_name.removesuffix("-session") or None


def check_can_issue(issuer: Principal, requested: Iterable[str]) -> tuple[str, ...]:
    """Valida a emissão de um token de agent por `issuer`. Retorna os scopes normalizados."""
    scopes = validate_scopes(requested)
    if issuer.kind != "session":
        raise PermissionError("tokens de agent não podem emitir outros tokens; faça login no navegador")
    missing = [s for s in scopes if s not in issuer.scopes]
    if missing:
        raise PermissionError(f"você não tem os scopes: {', '.join(missing)}")
    return scopes
