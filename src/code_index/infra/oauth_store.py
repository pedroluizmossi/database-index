"""Persistência do OAuth do MCP: clientes registrados, códigos de autorização e refresh tokens.

Segredos nunca em claro: código e refresh token só como HMAC-SHA256 (mesmo esquema dos tokens `cix_`);
o client_secret dos clientes confidenciais é cifrado (Fernet) com chave derivada do CODE_INDEX_AUTH_SECRET,
porque o SDK do MCP compara o segredo recebido com o guardado.
Os access tokens são tokens `cix_` comuns (kind "oauth") e ficam no TokenStore.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from cryptography.fernet import Fernet, InvalidToken

from ..settings import Settings
from .auth_store import hash_token

CODE_PREFIX = "cixc_"
REFRESH_PREFIX = "cixr_"


def generate_code() -> str:
    return CODE_PREFIX + secrets.token_urlsafe(32)


def generate_refresh() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class OAuthCode:
    client_id: str
    owner_email: str
    scopes: tuple[str, ...]
    code_challenge: str
    redirect_uri: str
    redirect_uri_explicit: bool
    resource: str | None
    expires_at: datetime


@dataclass(frozen=True)
class OAuthRefresh:
    id: str
    family_id: str
    client_id: str
    owner_email: str
    scopes: tuple[str, ...]
    resource: str | None
    access_token_id: str | None
    expires_at: datetime
    revoked_at: datetime | None


def _refresh(r: asyncpg.Record) -> OAuthRefresh:
    return OAuthRefresh(
        id=r["id"], family_id=r["family_id"], client_id=r["client_id"], owner_email=r["owner_email"],
        scopes=tuple(r["scopes"]), resource=r["resource"], access_token_id=r["access_token_id"],
        expires_at=r["expires_at"], revoked_at=r["revoked_at"],
    )


_CODE_COLS = ("client_id, owner_email, scopes, code_challenge, redirect_uri, redirect_uri_explicit, resource, "
              "expires_at")


def _code(r: asyncpg.Record) -> OAuthCode:
    return OAuthCode(
        client_id=r["client_id"], owner_email=r["owner_email"], scopes=tuple(r["scopes"]),
        code_challenge=r["code_challenge"], redirect_uri=r["redirect_uri"],
        redirect_uri_explicit=r["redirect_uri_explicit"], resource=r["resource"], expires_at=r["expires_at"],
    )


_REFRESH_COLS = ("id::text, family_id::text, client_id, owner_email, scopes, resource, access_token_id::text, "
                 "expires_at, revoked_at")


class OAuthStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings
        q = settings.qualified
        self._clients, self._codes, self._refresh = q("oauth_clients"), q("oauth_codes"), q("oauth_refresh_tokens")

    def _secret(self) -> str:
        return self._settings.require_auth_secret()

    def _fernet(self) -> Fernet:
        key = hashlib.sha256(b"code-index-oauth-client:" + self._secret().encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    def _hash(self, value: str) -> bytes:
        return hash_token(value, self._secret())

    # ------------------------------------------------------------ clientes

    async def save_client(self, client_id: str, *, name: str | None, metadata: dict[str, Any],
                          secret: str | None) -> None:
        enc = self._fernet().encrypt(secret.encode()).decode() if secret else None
        await self._pool.execute(
            f"INSERT INTO {self._clients} (client_id, client_name, metadata, secret_enc) VALUES ($1, $2, $3::jsonb, $4)",
            client_id, name, json.dumps(metadata, default=str), enc,
        )

    async def get_client(self, client_id: str) -> tuple[dict[str, Any], str | None] | None:
        """(metadados, client_secret em claro ou None). None se o cliente não existe."""
        if len(client_id) > 200:
            return None
        row = await self._pool.fetchrow(
            f"SELECT metadata, secret_enc FROM {self._clients} WHERE client_id = $1", client_id
        )
        if row is None:
            return None
        secret = None
        if row["secret_enc"]:
            try:
                secret = self._fernet().decrypt(row["secret_enc"].encode()).decode()
            except InvalidToken:  # CODE_INDEX_AUTH_SECRET mudou: o cliente precisa se registrar de novo
                return None
        return json.loads(row["metadata"]), secret

    # ------------------------------------------------------------ códigos de autorização

    async def create_code(self, *, client_id: str, owner_email: str, scopes: tuple[str, ...], code_challenge: str,
                          redirect_uri: str, redirect_uri_explicit: bool, resource: str | None,
                          ttl: timedelta) -> str:
        code = generate_code()
        await self._pool.execute(
            f"""INSERT INTO {self._codes} (code_hash, client_id, owner_email, scopes, code_challenge, redirect_uri,
                                           redirect_uri_explicit, resource, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            self._hash(code), client_id, owner_email.lower(), list(scopes), code_challenge, redirect_uri,
            redirect_uri_explicit, resource, datetime.now(timezone.utc) + ttl,
        )
        await self._pool.execute(f"DELETE FROM {self._codes} WHERE expires_at < now() - interval '1 hour'")
        return code

    async def get_code(self, code: str) -> OAuthCode | None:
        if not code.startswith(CODE_PREFIX) or len(code) > 200:
            return None
        row = await self._pool.fetchrow(f"SELECT {_CODE_COLS} FROM {self._codes} WHERE code_hash = $1", self._hash(code))
        return _code(row) if row else None

    async def consume_code(self, code: str) -> OAuthCode | None:
        """Apaga e devolve o código. None se já foi usado ou venceu (uso único, sem corrida entre duas trocas)."""
        row = await self._pool.fetchrow(
            f"DELETE FROM {self._codes} WHERE code_hash = $1 AND expires_at > now() RETURNING {_CODE_COLS}",
            self._hash(code),
        )
        return _code(row) if row else None

    # ------------------------------------------------------------ refresh tokens

    async def create_refresh(self, *, family_id: str | None, client_id: str, owner_email: str,
                             scopes: tuple[str, ...], resource: str | None, access_token_id: str,
                             ttl: timedelta) -> tuple[str, str]:
        """Cria um refresh token. Retorna (valor em claro, family_id)."""
        token = generate_refresh()
        row = await self._pool.fetchrow(
            f"""INSERT INTO {self._refresh} (token_hash, family_id, client_id, owner_email, scopes, resource,
                                             access_token_id, expires_at)
                VALUES ($1, COALESCE($2::uuid, gen_random_uuid()), $3, $4, $5, $6, $7::uuid, $8)
                RETURNING family_id::text""",
            self._hash(token), family_id, client_id, owner_email.lower(), list(scopes), resource, access_token_id,
            datetime.now(timezone.utc) + ttl,
        )
        return token, row["family_id"]

    async def get_refresh(self, token: str) -> OAuthRefresh | None:
        """Refresh token pelo valor, inclusive revogado (para detectar reuso). None se não existe."""
        if not token.startswith(REFRESH_PREFIX) or len(token) > 200:
            return None
        row = await self._pool.fetchrow(
            f"SELECT {_REFRESH_COLS} FROM {self._refresh} WHERE token_hash = $1", self._hash(token)
        )
        return _refresh(row) if row else None

    async def use_refresh(self, refresh_id: str) -> bool:
        """Marca como usado (revogado). False se já estava: outra troca chegou antes (reuso)."""
        res = await self._pool.execute(
            f"UPDATE {self._refresh} SET revoked_at = now() WHERE id = $1::uuid AND revoked_at IS NULL", refresh_id
        )
        return res.endswith(" 1")

    async def revoke_family(self, family_id: str) -> list[str]:
        """Revoga todos os refresh tokens da família. Retorna os access tokens emitidos nela."""
        rows = await self._pool.fetch(
            f"""UPDATE {self._refresh} SET revoked_at = COALESCE(revoked_at, now())
                WHERE family_id = $1::uuid RETURNING access_token_id::text""",
            family_id,
        )
        return [r["access_token_id"] for r in rows if r["access_token_id"]]

    async def family_of_access_token(self, access_token_id: str) -> str | None:
        return await self._pool.fetchval(
            f"SELECT family_id::text FROM {self._refresh} WHERE access_token_id = $1::uuid LIMIT 1", access_token_id
        )

    async def purge_expired(self) -> None:
        await self._pool.execute(f"DELETE FROM {self._refresh} WHERE expires_at < now() - interval '1 day'")
