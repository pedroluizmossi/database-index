"""Login com Google (OpenID Connect, authorization code + PKCE).

Validação do id_token (nada de confiar no que o navegador manda):
  - assinatura RS256 com as chaves públicas do Google (JWKS, com cache);
  - `aud` = nosso client_id, `iss` = accounts.google.com, `exp`/`iat` válidos;
  - `nonce` igual ao gerado no início do login (anti-replay);
  - `email_verified` verdadeiro;
  - autorização: o e-mail está em GOOGLE_ALLOWED_EMAILS/CODE_INDEX_ADMIN_EMAILS, OU o claim `hd`
    (domínio Google Workspace) é igual a GOOGLE_ALLOWED_DOMAIN. Só o sufixo do e-mail não basta:
    contas pessoais (gmail) não têm `hd`, então para elas vale apenas a lista de e-mails.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from ..settings import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL, não segredo
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class LoginError(Exception):
    """Falha de login (mensagem segura para mostrar ao usuário)."""


@dataclass(frozen=True)
class LoginStart:
    url: str
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    name: str | None
    domain: str | None


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class GoogleOIDC:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        jwks: jwt.PyJWKClient | None = None,
    ) -> None:
        if not settings.google_client_id or not settings.google_client_secret:
            raise RuntimeError("login Google não configurado: defina GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET")
        self._client_id = settings.google_client_id
        self._client_secret = settings.google_client_secret.get_secret_value()
        self._domain = (settings.google_allowed_domain or "").lower() or None
        self._emails = settings.login_emails
        if not self._domain and not self._emails:
            raise RuntimeError(
                "nenhum usuário autorizado: defina GOOGLE_ALLOWED_EMAILS (ou CODE_INDEX_ADMIN_EMAILS) "
                "e/ou GOOGLE_ALLOWED_DOMAIN"
            )
        self.redirect_uri = settings.public_url.rstrip("/") + "/auth/callback"
        self._http = http
        self._jwks = jwks or jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)

    def start(self) -> LoginStart:
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if self._domain:
            params["hd"] = self._domain  # só uma dica para a tela do Google; a validação é no id_token
        return LoginStart(url=f"{AUTH_URL}?{urlencode(params)}", state=state, nonce=nonce, code_verifier=verifier)

    async def finish(self, *, code: str, code_verifier: str, nonce: str) -> GoogleIdentity:
        id_token = await self._exchange(code, code_verifier)
        return await self.verify_id_token(id_token, nonce=nonce)

    async def _exchange(self, code: str, code_verifier: str) -> str:
        data = {
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        client = self._http or httpx.AsyncClient(timeout=15)
        try:
            r = await client.post(TOKEN_URL, data=data)
        finally:
            if self._http is None:
                await client.aclose()
        if r.status_code != 200:
            raise LoginError("o Google recusou o código de autorização")
        token = r.json().get("id_token")
        if not token:
            raise LoginError("resposta do Google sem id_token")
        return str(token)

    async def verify_id_token(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        try:
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token, key.key, algorithms=["RS256"], audience=self._client_id,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=30,
            )
        except jwt.PyJWTError as e:
            raise LoginError(f"id_token inválido: {e}") from None
        if claims.get("iss") not in ISSUERS:
            raise LoginError("id_token com emissor inesperado")
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise LoginError("nonce inválido (possível replay)")
        email = str(claims.get("email", "")).lower()
        if not email or claims.get("email_verified") is not True:
            raise LoginError("e-mail não verificado pelo Google")
        hd = str(claims.get("hd", "")).lower() or None
        by_email = email in self._emails
        by_domain = self._domain is not None and hd == self._domain and email.endswith("@" + self._domain)
        if not (by_email or by_domain):
            raise LoginError(f"a conta {email} não tem acesso a este servidor")
        return GoogleIdentity(email=email, name=claims.get("name"), domain=hd)
