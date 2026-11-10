"""Adaptador do servidor de autorização OAuth do SDK do MCP para os casos de uso (services/oauth.py).

O SDK cuida do protocolo (/.well-known/oauth-authorization-server, /authorize, /token, /register, /revoke,
PKCE, checagem de redirect_uri e de validade). Aqui só se traduz para o serviço. A parte com a pessoa
(login Google/Microsoft e consentimento) fica em /auth/authorize, no app.py: `authorize` devolve a URL dela
com o pedido assinado, sem estado no servidor até a aprovação.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from ...services import CodeIndexService
from ...settings import Settings

ServiceGetter = Callable[[], CodeIndexService]
PENDING_MAX_AGE = 900  # segundos para a pessoa entrar e aprovar


def pending_signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.require_auth_secret(), salt="code-index-oauth-authorize")


def load_pending(signer: URLSafeTimedSerializer, blob: str) -> dict[str, Any] | None:
    """Pedido de autorização assinado por `authorize`; None se adulterado ou vencido."""
    try:
        data = signer.loads(blob, max_age=PENDING_MAX_AGE)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


class CodeIndexOAuthProvider:
    """Implementa o `OAuthAuthorizationServerProvider` do SDK."""

    def __init__(self, settings: Settings, get_service: ServiceGetter) -> None:
        self._base = settings.public_url.rstrip("/")
        self._get_service = get_service
        self._signer = pending_signer(settings)

    # ------------------------------------------------------------ clientes

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        found = await self._get_service().get_oauth_client(client_id)
        if found is None:
            return None
        metadata, secret = found
        return OAuthClientInformationFull.model_validate({**metadata, "client_id": client_id, "client_secret": secret})

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        metadata = client_info.model_dump(mode="json", exclude_none=True, exclude={"client_secret"})
        try:
            await self._get_service().register_oauth_client(
                client_info.client_id, metadata, client_info.client_secret,
            )
        except ValueError as e:
            raise RegistrationError("invalid_redirect_uri", str(e)) from None

    # ------------------------------------------------------------ autorização

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (self._base, f"{self._base}/mcp"):
            raise AuthorizeError("invalid_target", "este servidor só emite tokens para o próprio MCP")
        blob = self._signer.dumps({
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_explicit": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "state": params.state,
            "scopes": params.scopes,
            "resource": params.resource,
        })
        return f"{self._base}/auth/authorize?req={blob}"

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        found = await self._get_service().load_oauth_code(client.client_id, authorization_code)
        if found is None:
            return None
        return AuthorizationCode(
            code=authorization_code, scopes=list(found.scopes), expires_at=found.expires_at.timestamp(),
            client_id=found.client_id, code_challenge=found.code_challenge, redirect_uri=AnyUrl(found.redirect_uri),
            redirect_uri_provided_explicitly=found.redirect_uri_explicit, resource=found.resource,
            subject=found.owner_email,
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        try:
            tokens = await self._get_service().exchange_oauth_code(client.client_id, authorization_code.code)
        except (ValueError, PermissionError) as e:
            raise TokenError("invalid_grant", str(e)) from None
        return _oauth_token(tokens)

    # ------------------------------------------------------------ refresh

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        found = await self._get_service().load_oauth_refresh(client.client_id, refresh_token)
        if found is None:
            return None
        return RefreshToken(
            token=refresh_token, client_id=found.client_id, scopes=list(found.scopes),
            expires_at=int(found.expires_at.timestamp()), resource=found.resource, subject=found.owner_email,
        )

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        try:
            tokens = await self._get_service().exchange_oauth_refresh(client.client_id, refresh_token.token, scopes)
        except (ValueError, PermissionError) as e:
            raise TokenError("invalid_grant", str(e)) from None
        return _oauth_token(tokens)

    # ------------------------------------------------------------ validação e revogação

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Qualquer token `cix_` (agent, OAuth ou sessão): mesma regra da API REST."""
        principal = await self._get_service().authenticate(token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            # o SDK compara com o cliente na revogação; tokens colados não pertencem a cliente OAuth
            client_id=principal.oauth_client_id or principal.token_name or principal.kind,
            scopes=sorted(principal.scopes),
            subject=principal.subject,
            claims={"kind": principal.kind, "token_id": principal.token_id, "token_name": principal.token_name,
                    "user_id": principal.user_id, "oauth_client_id": principal.oauth_client_id},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._get_service().revoke_oauth_token(token.token)

    async def exchange_identity_assertion(self, client: Any, params: Any) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "este servidor não aceita o grant jwt-bearer")


def _oauth_token(tokens: Any) -> OAuthToken:
    return OAuthToken(
        access_token=tokens.access_token, token_type="Bearer", expires_in=tokens.expires_in,
        scope=" ".join(tokens.scopes), refresh_token=tokens.refresh_token,
    )
