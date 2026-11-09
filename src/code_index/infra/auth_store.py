"""Persistência de tokens (api_tokens) e da trilha de auditoria (audit_log).

Formato do token: `cix_<43 chars base64url>` (256 bits). Só o HMAC-SHA256 com o segredo do
servidor vai para o banco: um vazamento da tabela não permite usar os tokens, e a busca é
por igualdade de hash (o token tem entropia alta; não precisa de KDF lento).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from ..domain.auth import Principal, TokenKind, effective_scopes
from ..settings import Settings

TOKEN_PREFIX = "cix_"
_LAST_USED_RESOLUTION = timedelta(minutes=1)  # evita um UPDATE por requisição


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class TokenInfo:
    """Metadados de um token (nunca o valor)."""

    id: str
    kind: TokenKind
    name: str
    owner_email: str
    token_prefix: str
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        def iso(d: datetime | None) -> str | None:
            return d.isoformat(timespec="seconds") if d else None

        return {
            "id": self.id, "kind": self.kind, "name": self.name, "owner_email": self.owner_email,
            "prefix": self.token_prefix, "scopes": list(self.scopes), "active": self.active,
            "created_at": iso(self.created_at), "expires_at": iso(self.expires_at),
            "last_used_at": iso(self.last_used_at), "revoked_at": iso(self.revoked_at),
        }


_COLS = "id::text, kind, name, owner_email, token_prefix, scopes, created_at, expires_at, last_used_at, revoked_at"


def _info(r: asyncpg.Record) -> TokenInfo:
    return TokenInfo(
        id=r["id"], kind=r["kind"], name=r["name"], owner_email=r["owner_email"],
        token_prefix=r["token_prefix"], scopes=tuple(r["scopes"]), created_at=r["created_at"],
        expires_at=r["expires_at"], last_used_at=r["last_used_at"], revoked_at=r["revoked_at"],
    )


class TokenStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".api_tokens'
        self._settings = settings

    def _secret(self) -> str:
        return self._settings.require_auth_secret()

    async def create(
        self,
        *,
        kind: TokenKind,
        name: str,
        owner_email: str,
        scopes: Iterable[str],
        ttl: timedelta | None,
        oauth_client_id: str | None = None,
    ) -> tuple[str, TokenInfo]:
        """Cria um token. Retorna (valor em claro — mostrar UMA vez, metadados)."""
        token = generate_token()
        expires = datetime.now(timezone.utc) + ttl if ttl else None
        row = await self._pool.fetchrow(
            f"""INSERT INTO {self._t}
                    (kind, name, owner_email, token_hash, token_prefix, scopes, expires_at, oauth_client_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING {_COLS}""",
            kind, name, owner_email.lower(), hash_token(token, self._secret()), token[:10], list(scopes), expires,
            oauth_client_id,
        )
        return token, _info(row)

    async def authenticate(self, token: str) -> Principal | None:
        """Principal do token, ou None se inválido/expirado/revogado."""
        if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
            return None
        row = await self._pool.fetchrow(
            f"""SELECT {_COLS}, oauth_client_id FROM {self._t}
                WHERE token_hash = $1 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())""",
            hash_token(token, self._secret()),
        )
        if row is None:
            return None
        info = _info(row)
        if info.last_used_at is None or datetime.now(timezone.utc) - info.last_used_at > _LAST_USED_RESOLUTION:
            await self._pool.execute(f"UPDATE {self._t} SET last_used_at = now() WHERE id = $1::uuid", info.id)
        return Principal(
            subject=info.owner_email, kind=info.kind, scopes=effective_scopes(info.scopes),
            token_id=info.id, token_name=info.name, oauth_client_id=row["oauth_client_id"],
        )

    async def list(self, *, owner_email: str | None = None, include_inactive: bool = False) -> list[TokenInfo]:
        where, args = ["kind IN ('agent', 'oauth')"], []
        if owner_email:
            args.append(owner_email.lower())
            where.append(f"owner_email = ${len(args)}")
        if not include_inactive:
            where.append("revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())")
        rows = await self._pool.fetch(
            f"SELECT {_COLS} FROM {self._t} WHERE {' AND '.join(where)} ORDER BY created_at DESC", *args
        )
        return [_info(r) for r in rows]

    async def get(self, token_id: str) -> TokenInfo | None:
        try:
            row = await self._pool.fetchrow(f"SELECT {_COLS} FROM {self._t} WHERE id = $1::uuid", token_id)
        except asyncpg.DataError:  # id não é uuid
            return None
        return _info(row) if row else None

    async def revoke(self, token_id: str) -> bool:
        try:
            res = await self._pool.execute(
                f"UPDATE {self._t} SET revoked_at = now() WHERE id = $1::uuid AND revoked_at IS NULL", token_id
            )
        except asyncpg.DataError:
            return False
        return res.endswith(" 1")

    async def purge_expired_sessions(self) -> int:
        """Remove sessões e tokens OAuth vencidos há mais de 1 dia (os de agent ficam para histórico).

        Tokens OAuth são de vida curta e renovados pelo refresh: sem isto, a tabela cresceria a cada hora."""
        res = await self._pool.execute(
            f"DELETE FROM {self._t} WHERE kind IN ('session', 'oauth') AND expires_at < now() - interval '1 day'"
        )
        return int(res.split()[-1])


class AuditStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._t = f'"{settings.pg_schema}".audit_log'

    async def record(
        self,
        *,
        actor: str,
        action: str,
        token_id: str | None = None,
        params: dict[str, Any] | None = None,
        status: int | None = None,
        client_ip: str | None = None,
    ) -> None:
        await self._pool.execute(
            f"""INSERT INTO {self._t} (actor, token_id, action, params, status, client_ip)
                VALUES ($1, $2::uuid, $3, $4::jsonb, $5, $6)""",
            actor, token_id, action, json.dumps(params, default=str, ensure_ascii=False) if params else None,
            status, client_ip,
        )

    async def recent(self, *, limit: int = 100, actor: str | None = None) -> list[dict[str, Any]]:
        args: list[Any] = [min(max(limit, 1), 1000)]
        where = ""
        if actor:
            args.append(f"%{actor}%")
            where = "WHERE actor ILIKE $2"
        rows = await self._pool.fetch(
            f"""SELECT id, at, actor, token_id::text, action, params, status, client_ip
                FROM {self._t} {where} ORDER BY at DESC LIMIT $1""",
            *args,
        )
        return [
            {**dict(r), "at": r["at"].isoformat(timespec="seconds"),
             "params": json.loads(r["params"]) if r["params"] else None}
            for r in rows
        ]
