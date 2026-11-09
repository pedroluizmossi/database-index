"""Login de pessoas por OpenID Connect (authorization code + PKCE), comum a todos os provedores.

O fluxo e as validações que não dependem do provedor ficam aqui:
  - assinatura RS256 com as chaves públicas do provedor (JWKS, com cache);
  - `aud` = nosso client_id, `iss` na lista do provedor, `exp`/`iat` válidos;
  - `nonce` igual ao gerado no início do login (anti-replay).
Quem pode entrar (e-mail verificado, domínio, tenant) é regra de cada provedor, em `_identity`.

Provedores: `google_oidc.GoogleOIDC` e `entra_oidc.EntraOIDC`. Registro em `login.py`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx
import jwt

from ..settings import Settings

log = logging.getLogger(__name__)


class LoginError(Exception):
    """Falha de login (mensagem segura para mostrar ao usuário)."""


@dataclass(frozen=True)
class LoginStart:
    url: str
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True)
class Identity:
    """Pessoa autenticada por um provedor. `email` já em minúsculas e autorizado."""

    email: str
    name: str | None
    domain: str | None
    provider: str = ""


def _provider_error(r: httpx.Response) -> str:
    """`error: descrição` da resposta de erro OAuth (RFC 6749 §5.2), curto e sem quebras de linha."""
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if not isinstance(body, dict):
        return f"HTTP {r.status_code}"
    error = str(body.get("error") or f"HTTP {r.status_code}")
    # A Microsoft põe "Trace ID/Correlation ID/Timestamp" em linhas seguintes: só a primeira interessa.
    desc = str(body.get("error_description") or "").strip().splitlines()
    return f"{error}: {desc[0][:300]}" if desc else error


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class OIDCProvider:
    """Base dos provedores. Subclasses definem endpoints, emissores e a regra de autorização."""

    name: ClassVar[str]           # identificador na URL (?provider=)
    label: ClassVar[str]          # nome no botão ("Google")
    who: ClassVar[str]            # sujeito das mensagens ("o Google")

    def __init__(
        self,
        settings: Settings,
        *,
        client_id: str,
        client_secret: str,
        authorize_url: str,
        token_url: str,
        jwks_url: str,
        issuers: tuple[str, ...],
        http: httpx.AsyncClient | None = None,
        jwks: jwt.PyJWKClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._issuers = issuers
        self.redirect_uri = settings.public_url.rstrip("/") + "/auth/callback"
        self._http = http
        self._jwks = jwks or jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)

    # ------------------------------------------------------------ pontos de extensão

    def _extra_params(self) -> dict[str, str]:
        """Parâmetros extras da URL de login (dicas de domínio etc.)."""
        return {}

    def _identity(self, claims: dict[str, Any]) -> Identity:
        """Autoriza a pessoa a partir dos claims já validados (assinatura, aud, iss, nonce)."""
        raise NotImplementedError

    # ------------------------------------------------------------ fluxo

    def start(self) -> LoginStart:
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
            **self._extra_params(),
        }
        return LoginStart(
            url=f"{self._authorize_url}?{urlencode(params)}", state=state, nonce=nonce, code_verifier=verifier,
        )

    async def finish(self, *, code: str, code_verifier: str, nonce: str) -> Identity:
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
            r = await client.post(self._token_url, data=data)
        finally:
            if self._http is None:
                await client.aclose()
        if r.status_code != 200:
            # O provedor explica o motivo (ex.: AADSTS7000215 = segredo errado). Não é segredo: vai para o log
            # e para a página, escapado. O corpo da requisição (com o client_secret) nunca é logado.
            reason = _provider_error(r)
            log.warning("%s recusou o código de autorização (HTTP %s): %s", self.label, r.status_code, reason)
            raise LoginError(f"{self.who} recusou o código de autorização: {reason}")
        token = r.json().get("id_token")
        if not token:
            raise LoginError(f"resposta d{self.who} sem id_token")  # "do Google", "da Microsoft"
        return str(token)

    async def verify_id_token(self, id_token: str, *, nonce: str) -> Identity:
        try:
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token, key.key, algorithms=["RS256"], audience=self._client_id,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=30,
            )
        except jwt.PyJWTError as e:
            raise LoginError(f"id_token inválido: {e}") from None
        if claims.get("iss") not in self._issuers:
            raise LoginError("id_token com emissor inesperado")
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise LoginError("nonce inválido (possível replay)")
        return self._identity(claims)
