"""Tokens, sessões de pessoas (login Google/Microsoft) e auditoria."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any


from ..domain.auth import ADMIN, Principal, check_can_issue, scopes_for_user, validate_scopes
from ..infra.auth_store import TokenInfo

from .oauth import OAuthMixin

log = logging.getLogger(__name__)


class AuthMixin(OAuthMixin):
    async def authenticate(self, token: str) -> Principal | None:
        """Principal de um token (agent ou sessão); None se inválido, expirado ou revogado."""
        return await self._tokens.authenticate(token)

    async def start_session(
        self, email: str, provider: str = "google", *, display_name: str | None = None,
    ) -> tuple[str, TokenInfo]:
        """Sessão de uma pessoa já autenticada no navegador (Google ou Microsoft). Scopes vêm da lista de admins.
        Registra a pessoa em `users` (primeiro login) ou atualiza nome e data do último login."""
        await self._users.record_login(email, display_name)
        scopes = scopes_for_user(email, self.settings.admin_emails)
        return await self._tokens.create(
            kind="session", name=f"{provider}-session", owner_email=email, scopes=scopes,
            ttl=timedelta(hours=self.settings.session_ttl_hours),
        )

    async def end_session(self, principal: Principal) -> None:
        if principal.kind == "session" and principal.token_id:
            await self._tokens.revoke(principal.token_id)

    async def create_agent_token(
        self,
        issuer: Principal | None,
        *,
        name: str,
        scopes: Sequence[str],
        ttl_days: int | None = None,
        owner_email: str | None = None,
    ) -> tuple[str, TokenInfo]:
        """Emite um token de agent. Retorna (valor — mostrar uma única vez, metadados).

        issuer=None: operador local (CLI no servidor), que informa `owner_email`.
        Caso contrário, só sessões de pessoas (login no navegador) emitem, e só com scopes que já possuem.
        """
        if not name.strip():
            raise ValueError("informe um nome para o token")
        if issuer is None:
            if not owner_email:
                raise ValueError("informe o e-mail do responsável pelo token")
            normalized = validate_scopes(scopes)
            owner = owner_email
        else:
            try:
                normalized = check_can_issue(issuer, scopes)
            except PermissionError as e:
                raise PermissionError(str(e)) from None
            owner = issuer.subject
        days = self.settings.token_ttl_days if ttl_days is None else ttl_days
        if days <= 0 or days > 365:
            raise ValueError("validade do token deve ser entre 1 e 365 dias")
        return await self._tokens.create(
            kind="agent", name=name.strip(), owner_email=owner, scopes=normalized, ttl=timedelta(days=days),
        )

    async def list_tokens(self, principal: Principal | None, *, include_inactive: bool = False) -> list[TokenInfo]:
        """Admin (ou operador local) vê todos; os demais, só os próprios."""
        owner = None if principal is None or principal.has(ADMIN) else principal.subject
        return await self._tokens.list(owner_email=owner, include_inactive=include_inactive)

    async def revoke_token(self, principal: Principal | None, token_id: str) -> None:
        info = await self._tokens.get(token_id)
        if info is None or info.kind not in ("agent", "oauth"):
            raise ValueError(f"token não encontrado: {token_id}")
        if principal is not None and not principal.has(ADMIN) and info.owner_email != principal.subject:
            raise PermissionError("só o dono do token ou um admin pode revogá-lo")
        if info.kind == "oauth":  # desconecta o cliente MCP: o refresh token cai junto
            await self.revoke_oauth_access(token_id)
        else:
            await self._tokens.revoke(token_id)

    async def recent_audit(self, *, limit: int = 100, actor: str | None = None) -> list[dict[str, Any]]:
        return await self._audit.recent(limit=limit, actor=actor)

