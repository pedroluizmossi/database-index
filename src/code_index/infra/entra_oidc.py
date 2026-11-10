"""Login com Microsoft (Entra ID / Azure AD, OpenID Connect v2). Fluxo e validações comuns em `oidc.py`.

Regra de autorização da Microsoft (mais estrita que a do Google, porque no Entra o e-mail não tem
`email_verified` e pode ser editado pelo administrador de qualquer tenant — o ataque "nOAuth"):
  - um único tenant (AZURE_TENANT_ID, o GUID): endpoints do tenant, `iss` exato e `tid` igual. Nada de
    `common`/`organizations`, que aceitariam contas de qualquer empresa;
  - contas convidadas (guest, claim `idp` de outro emissor) são recusadas: o e-mail delas vem de fora;
  - o e-mail (`email`, ou `preferred_username`) está em AZURE_ALLOWED_EMAILS/CODE_INDEX_ADMIN_EMAILS, OU
    termina com @AZURE_ALLOWED_DOMAIN.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import jwt

from ..settings import Settings
from .oidc import Identity, LoginError, OIDCProvider

AUTHORITY = "https://login.microsoftonline.com"
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class EntraOIDC(OIDCProvider):
    name = "microsoft"
    label = "Microsoft"
    who = "a Microsoft"

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        jwks: jwt.PyJWKClient | None = None,
    ) -> None:
        if not settings.azure_tenant_id or not settings.azure_client_id or not settings.azure_client_secret:
            raise RuntimeError(
                "login Microsoft não configurado: defina AZURE_TENANT_ID, AZURE_CLIENT_ID e AZURE_CLIENT_SECRET"
            )
        tenant = settings.azure_tenant_id.strip().lower()
        if not _GUID.match(tenant):
            raise RuntimeError(
                "AZURE_TENANT_ID precisa ser o ID (GUID) do tenant; 'common', 'organizations' e nomes de "
                "domínio não são aceitos"
            )
        self._tenant = tenant
        self._domain = (settings.azure_allowed_domain or "").lower().lstrip("@") or None
        self._emails = settings.azure_login_emails
        if not self._domain and not self._emails:
            raise RuntimeError(
                "nenhum usuário autorizado: defina AZURE_ALLOWED_EMAILS (ou CODE_INDEX_ADMIN_EMAILS) "
                "e/ou AZURE_ALLOWED_DOMAIN"
            )
        self._issuer = f"{AUTHORITY}/{tenant}/v2.0"
        super().__init__(
            settings, client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret.get_secret_value(),
            authorize_url=f"{AUTHORITY}/{tenant}/oauth2/v2.0/authorize",
            token_url=f"{AUTHORITY}/{tenant}/oauth2/v2.0/token",
            jwks_url=f"{AUTHORITY}/{tenant}/discovery/v2.0/keys",
            issuers=(self._issuer,), http=http, jwks=jwks,
        )

    def _extra_params(self) -> dict[str, str]:
        # só uma dica para a tela da Microsoft; a validação é no id_token
        return {"domain_hint": self._domain} if self._domain else {}

    def _identity(self, claims: dict[str, Any]) -> Identity:
        if str(claims.get("tid", "")).lower() != self._tenant:
            raise LoginError("conta de outro tenant da Microsoft")
        idp = str(claims.get("idp", ""))
        if idp and idp != self._issuer and self._tenant not in idp.lower():
            raise LoginError("contas convidadas (guest) não são aceitas; entre com uma conta da organização")
        email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
        if "@" not in email:
            raise LoginError("a Microsoft não informou o e-mail da conta")
        by_email = email in self._emails
        by_domain = self._domain is not None and email.endswith("@" + self._domain)
        if not (by_email or by_domain):
            raise LoginError(f"a conta {email} não tem acesso a este servidor")
        return Identity(email=email, name=claims.get("name"), domain=email.rsplit("@", 1)[1], provider=self.name)

    @classmethod
    def allows_email(cls, settings: Settings, email: str) -> bool:
        domain = (settings.azure_allowed_domain or "").lower().lstrip("@")
        email = email.lower()
        return email in settings.azure_login_emails or bool(domain and email.endswith("@" + domain))
