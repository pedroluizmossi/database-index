"""OAuth do MCP: o code-index como servidor de autorização dos clientes MCP (regras em domain/oauth.py).

A pessoa entra no navegador (Google ou Microsoft), aprova o cliente e ele recebe:
  - access token: um `cix_` do tipo "oauth" (só search:read), validado como qualquer outro token;
  - refresh token: rotacionado a cada uso; reuso de um já usado revoga a autorização inteira (família).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.auth import Principal, scopes_for_user
from ..domain.oauth import CODE_TTL_SECONDS, check_redirect_uris, grant_scopes
from ..infra.login import email_allowed
from ..infra.oauth_store import OAuthCode, OAuthRefresh
from .base import ServiceBase

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: tuple[str, ...]


class OAuthMixin(ServiceBase):
    # ------------------------------------------------------------ clientes

    async def register_oauth_client(self, client_id: str, metadata: dict[str, Any], secret: str | None) -> None:
        """Registro dinâmico (RFC 7591). ValueError se algum redirect_uri não é permitido."""
        uris = [str(u) for u in metadata.get("redirect_uris") or []]
        check_redirect_uris(uris, self.settings.oauth_redirect_uris)
        name = str(metadata.get("client_name") or "").strip()[:100] or None
        clean = {k: v for k, v in metadata.items() if k != "client_secret"}
        await self._oauth.save_client(client_id, name=name, metadata=clean, secret=secret)

    async def get_oauth_client(self, client_id: str) -> tuple[dict[str, Any], str | None] | None:
        return await self._oauth.get_client(client_id)

    # ------------------------------------------------------------ autorização

    async def issue_oauth_code(
        self,
        principal: Principal,
        *,
        client_id: str,
        scopes: Sequence[str] | None,
        code_challenge: str,
        redirect_uri: str,
        redirect_uri_explicit: bool,
        resource: str | None,
    ) -> str:
        """Código de autorização após o consentimento. Só uma pessoa logada no navegador autoriza."""
        if principal.kind != "session":
            raise PermissionError("a autorização precisa de uma pessoa logada no navegador")
        if await self._oauth.get_client(client_id) is None:
            raise ValueError("cliente OAuth desconhecido")
        granted = grant_scopes(scopes, principal.scopes)
        return await self._oauth.create_code(
            client_id=client_id, owner_email=principal.subject, scopes=granted, code_challenge=code_challenge,
            redirect_uri=redirect_uri, redirect_uri_explicit=redirect_uri_explicit, resource=resource,
            ttl=timedelta(seconds=CODE_TTL_SECONDS),
        )

    async def load_oauth_code(self, client_id: str, code: str) -> OAuthCode | None:
        found = await self._oauth.get_code(code)
        if found is None or found.client_id != client_id:
            return None
        return found

    async def exchange_oauth_code(self, client_id: str, code: str) -> OAuthTokens:
        """Troca o código (já validado pelo SDK: PKCE, redirect_uri, validade) pelos tokens. Uso único."""
        found = await self._oauth.consume_code(code)
        if found is None or found.client_id != client_id:
            raise ValueError("código de autorização já usado ou vencido")
        return await self._issue_oauth_tokens(client_id, found.owner_email, found.scopes, found.resource, None)

    # ------------------------------------------------------------ refresh

    async def load_oauth_refresh(self, client_id: str, token: str) -> OAuthRefresh | None:
        """Refresh token válido do cliente. Reuso de um já usado revoga a família (token vazado)."""
        found = await self._oauth.get_refresh(token)
        if found is None or found.client_id != client_id:
            return None
        if found.revoked_at is not None:
            await self._revoke_oauth_family(found.family_id, reason="reuso de refresh token")
            return None
        if found.expires_at <= datetime.now(timezone.utc):
            return None
        return found

    async def exchange_oauth_refresh(self, client_id: str, token: str, scopes: Sequence[str] | None) -> OAuthTokens:
        found = await self.load_oauth_refresh(client_id, token)
        if found is None:
            raise ValueError("refresh token inválido, vencido ou já usado")
        user = await self._users.get(found.owner_email)
        if not email_allowed(self.settings, found.owner_email) or (user is not None and user.get("disabled_at")):
            # Saiu da allowlist (ou foi desativada) depois do login: não renova, e a autorização inteira cai.
            await self._revoke_oauth_family(found.family_id, reason="pessoa sem acesso")
            raise ValueError("acesso removido: entre de novo pelo navegador")
        if not await self._oauth.use_refresh(found.id):  # outra troca concorrente usou este mesmo token
            await self._revoke_oauth_family(found.family_id, reason="reuso de refresh token")
            raise ValueError("refresh token já usado")
        if found.access_token_id:
            await self._tokens.revoke(found.access_token_id)
        wanted = [s for s in (scopes or found.scopes) if s in found.scopes]
        return await self._issue_oauth_tokens(
            found.client_id, found.owner_email, tuple(wanted) or found.scopes, found.resource, found.family_id,
        )

    async def _issue_oauth_tokens(self, client_id: str, owner_email: str, scopes: Sequence[str],
                                  resource: str | None, family_id: str | None) -> OAuthTokens:
        # Recalcula com os scopes atuais da pessoa: quem deixou de ser admin/autorizado não renova o que tinha.
        granted = grant_scopes(scopes, scopes_for_user(owner_email, self.settings.admin_emails))
        client = await self._oauth.get_client(client_id)
        name = str((client[0] if client else {}).get("client_name") or "cliente MCP")[:100]
        ttl = timedelta(hours=self.settings.oauth_access_ttl_hours)
        access, info = await self._tokens.create(
            kind="oauth", name=name, owner_email=owner_email, scopes=granted, ttl=ttl, oauth_client_id=client_id,
        )
        refresh, _ = await self._oauth.create_refresh(
            family_id=family_id, client_id=client_id, owner_email=owner_email, scopes=granted, resource=resource,
            access_token_id=info.id, ttl=timedelta(days=self.settings.oauth_refresh_ttl_days),
        )
        return OAuthTokens(access_token=access, refresh_token=refresh, expires_in=int(ttl.total_seconds()),
                           scopes=granted)

    # ------------------------------------------------------------ revogação

    async def revoke_oauth_token(self, token: str) -> None:
        """Revogação (RFC 7009) pelo valor: access ou refresh token derruba a autorização inteira."""
        refresh = await self._oauth.get_refresh(token)
        if refresh is not None:
            await self._revoke_oauth_family(refresh.family_id, reason="revogado pelo cliente")
            return
        principal = await self._tokens.authenticate(token)
        if principal is not None and principal.kind == "oauth" and principal.token_id:
            await self.revoke_oauth_access(principal.token_id)

    async def revoke_oauth_access(self, access_token_id: str) -> None:
        """Revoga um access token OAuth e a família de refresh tokens dele (a pessoa desconecta o cliente)."""
        await self._tokens.revoke(access_token_id)
        family = await self._oauth.family_of_access_token(access_token_id)
        if family:
            await self._revoke_oauth_family(family, reason="revogado pela pessoa")

    async def _revoke_oauth_family(self, family_id: str, *, reason: str) -> None:
        for access_id in await self._oauth.revoke_family(family_id):
            await self._tokens.revoke(access_id)
        log.info("autorização OAuth %s revogada (%s)", family_id, reason)
