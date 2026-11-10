"""Login com Google (OpenID Connect). Fluxo e validações comuns em `oidc.py`.

Regra de autorização do Google:
  - `email_verified` verdadeiro;
  - o e-mail está em GOOGLE_ALLOWED_EMAILS/CODE_INDEX_ADMIN_EMAILS, OU o claim `hd`
    (domínio Google Workspace) é igual a GOOGLE_ALLOWED_DOMAIN. Só o sufixo do e-mail não basta:
    contas pessoais (gmail) não têm `hd`, então para elas vale apenas a lista de e-mails.
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt

from ..settings import Settings
from .oidc import Identity, LoginError, LoginStart, OIDCProvider

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL, não segredo
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

GoogleIdentity = Identity  # nome antigo
__all__ = ["GoogleIdentity","GoogleOIDC", "Identity", "LoginError", "LoginStart"]


class GoogleOIDC(OIDCProvider):
    name = "google"
    label = "Google"
    who = "o Google"

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        jwks: jwt.PyJWKClient | None = None,
    ) -> None:
        if not settings.google_client_id or not settings.google_client_secret:
            raise RuntimeError("login Google não configurado: defina GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET")
        self._domain = (settings.google_allowed_domain or "").lower() or None
        self._emails = settings.login_emails
        if not self._domain and not self._emails:
            raise RuntimeError(
                "nenhum usuário autorizado: defina GOOGLE_ALLOWED_EMAILS (ou CODE_INDEX_ADMIN_EMAILS) "
                "e/ou GOOGLE_ALLOWED_DOMAIN"
            )
        super().__init__(
            settings, client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            authorize_url=AUTH_URL, token_url=TOKEN_URL, jwks_url=JWKS_URL, issuers=ISSUERS, http=http, jwks=jwks,
        )

    def _extra_params(self) -> dict[str, str]:
        # só uma dica para a tela do Google; a validação é no id_token
        return {"hd": self._domain} if self._domain else {}

    def _identity(self, claims: dict[str, Any]) -> Identity:
        email = str(claims.get("email", "")).lower()
        if not email or claims.get("email_verified") is not True:
            raise LoginError("e-mail não verificado pelo Google")
        hd = str(claims.get("hd", "")).lower() or None
        by_email = email in self._emails
        by_domain = self._domain is not None and hd == self._domain and email.endswith("@" + self._domain)
        if not (by_email or by_domain):
            raise LoginError(f"a conta {email} não tem acesso a este servidor")
        return Identity(email=email, name=claims.get("name"), domain=hd, provider=self.name)

    @classmethod
    def allows_email(cls, settings: Settings, email: str) -> bool:
        # Sem o claim `hd` (só existe no login): o domínio vale pelo sufixo do e-mail, que o login já exigiu casar.
        domain = (settings.google_allowed_domain or "").lower()
        email = email.lower()
        return email in settings.login_emails or bool(domain and email.endswith("@" + domain))
