"""Pessoas e preferências. O registro das configs fica em `domain/preferences.py`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain import preferences
from ..domain.auth import Principal
from .base import ServiceBase, _iso_local


class UsersMixin(ServiceBase):
    async def user_settings(self, principal: Principal | None, *, email: str | None = None) -> dict[str, Any]:
        """Perfil e configs já resolvidas (padrão → valor da pessoa), mais o schema para a UI montar o formulário.
        Tokens da pessoa (agent/OAuth) leem as mesmas configs do dono. principal=None: operador local (CLI),
        que informa o `email`."""
        subject = self._settings_subject(principal, email)
        user = await self._users.get(subject)
        if principal is None and user is None:
            raise ValueError(f"usuário não encontrado: {subject} (ele aparece depois do primeiro login)")
        stored = await self._users.settings(subject)
        return {
            "email": subject,
            "display_name": user["display_name"] if user else None,
            "last_login_at": _iso_local(user["last_login_at"]) if user else None,
            "settings": preferences.resolve(stored),
            "changed": sorted(k for k in stored if k in preferences.PREFS),
            "schema": preferences.schema(),
        }

    async def update_user_settings(
        self, principal: Principal | None, changes: Mapping[str, Any], *, email: str | None = None,
    ) -> dict[str, Any]:
        """Altera configs da própria pessoa; valor `None` volta ao padrão. Só sessão de navegador grava:
        um agent (que lê conteúdo indexado, não confiável) não muda as configs de ninguém.
        principal=None: operador local (CLI), que informa o `email` de uma pessoa que já fez login."""
        if principal is not None and principal.kind != "session":
            raise PermissionError("só o próprio usuário, logado no navegador, altera as configurações")
        subject = self._settings_subject(principal, email)
        if principal is None and await self._users.get(subject) is None:
            raise ValueError(f"usuário não encontrado: {subject} (ele aparece depois do primeiro login)")
        to_set, to_unset = preferences.validate_changes(changes)
        await self._users.apply_settings(subject, to_set, to_unset)
        return await self.user_settings(principal, email=email)

    async def list_users(self) -> list[dict[str, Any]]:
        """Pessoas que já fizeram login, com quantas configs cada uma mudou (operador local)."""
        return await self._users.all()

    @staticmethod
    def _settings_subject(principal: Principal | None, email: str | None) -> str:
        if principal is not None:
            return principal.subject  # pela API, cada pessoa só alcança as próprias configs
        if not email or "@" not in email:
            raise ValueError("informe o e-mail do usuário")
        return email.strip().lower()
