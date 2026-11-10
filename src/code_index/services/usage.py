"""Uso da aplicação: registrar cada chamada (MCP, API, interface) e montar os relatórios do dashboard.

As interfaces medem (tempo, status, resposta) e chamam `record_usage`; o consumo da Voyage vem de
`metered()`, que acumula o que as chamadas ao LiteLLM gastaram dentro do bloco. Registrar uso nunca
derruba a requisição: falha vira log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal

from ..domain import usage as U
from ..domain.auth import ADMIN, Principal, scopes_for_user
from ..infra import llm
from .base import ServiceBase

log = logging.getLogger(__name__)

_MISSING: Any = object()


class UsageMixin(ServiceBase):
    @staticmethod
    @contextmanager
    def metered() -> Iterator[Any]:
        """Mede o consumo de LLM (tokens/custo) das chamadas feitas dentro do bloco: `.snapshot()` no fim."""
        with llm.metered() as meter:
            yield meter

    async def record_usage(
        self,
        principal: Principal | None,
        *,
        transport: Literal["mcp", "http"],
        action: str,
        status: int,
        duration_ms: int,
        llm_usage: U.LLMUsage | None = None,
        result: Any = _MISSING,
        result_count: int | None = None,
        response_bytes: int | None = None,
        client: str | None = None,
        client_version: str | None = None,
        params: dict[str, Any] | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Grava um evento de uso. `result` (a resposta) só serve para contar itens e medir o tamanho:
        o conteúdo em si nunca é guardado."""
        if not self.settings.usage_tracking:
            return
        if result is not _MISSING:
            result_count = U.result_count(result) if result_count is None else result_count
            response_bytes = U.response_bytes(result) if response_bytes is None else response_bytes
        kind = principal.kind if principal else None
        event = U.UsageEvent(
            channel=U.channel_for(transport, kind), action=action, status=status, duration_ms=max(0, duration_ms),
            user_email=principal.subject if principal else None, token_id=principal.token_id if principal else None,
            token_kind=kind, client=U.normalize_client(client), client_version=(client_version or None),
            result_count=result_count, response_bytes=response_bytes, llm=llm_usage or U.LLMUsage(),
            params=U.clean_params(params), attrs=attrs or None,
        )
        try:
            await self._usage.record(event, user_id=principal.user_id if principal else None)
        except Exception:  # noqa: BLE001 - métrica nunca derruba a chamada
            log.exception("falha ao registrar uso de %s", action)

    # ------------------------------------------------------------ relatórios

    async def usage_report(self, days: int = 30, *, email: str | None = None) -> dict[str, Any]:
        """Dashboard de uso: totais, série diária, por tool/rota, por cliente, consultas e eventos recentes.
        Com `email`, só o uso daquele usuário."""
        days = U.validate_days(days)
        user_id = None
        if email:
            user = await self._users.get(email)
            if user is None:
                raise ValueError(f"usuário não encontrado: {email}")
            user_id = user["id"]
        return await self._report(days, user_id)

    async def my_usage(self, principal: Principal, days: int = 30) -> dict[str, Any]:
        """O uso do próprio usuário (todas as credenciais dele)."""
        days = U.validate_days(days)
        user_id = principal.user_id
        if user_id is None:
            user = await self._users.get(principal.subject)
            user_id = user["id"] if user else None
        if user_id is None:
            raise ValueError(f"usuário não encontrado: {principal.subject}")
        return await self._report(days, user_id)

    async def _report(self, days: int, user_id: str | None) -> dict[str, Any]:
        now = datetime.now().astimezone()
        delta = now.utcoffset()
        offset = int(delta.total_seconds()) if delta else 0
        totals = await self._usage.totals(days, user_id)
        # Contexto = o que entra na janela de um agent: só respostas de tools do MCP. A resposta da API/interface
        # tem tamanho (response_bytes), mas não ocupa contexto de ninguém.
        totals["context_tokens"] = U.approx_tokens(totals.get("context_bytes"))
        actions = await self._usage.by_action(days, user_id)
        for a in actions:
            mcp = a["channel"] == "mcp"
            a["context_tokens"] = U.approx_tokens(a["response_bytes"]) if mcp else None
            a["avg_context_tokens"] = U.approx_tokens(a["avg_response_bytes"]) if mcp else None
        # A janela é "agora − N dias": toca N+1 datas (a mais antiga, parcial). Sem ela, a soma do gráfico
        # ficaria menor que os totais.
        daily = U.fill_days(await self._usage.daily(days, offset, user_id), days + 1, now.date())
        return {
            "days": days,
            "generated_at": now.isoformat(timespec="seconds"),
            "bytes_per_token": U.BYTES_PER_TOKEN,
            "totals": totals,
            "daily": daily,
            "by_action": actions,
            "by_client": await self._usage.by_client(days, user_id),
            "top_queries": await self._usage.top_queries(days, user_id),
            "empty_queries": await self._usage.top_queries(days, user_id, empty_only=True),
            "recent": await self._usage.recent(days, user_id, limit=50),
        }

    # ------------------------------------------------------------ usuários (admin)

    async def users_overview(self, days: int = 30) -> list[dict[str, Any]]:
        """Todos os usuários, com papel, estado e o uso no período (admin)."""
        days = U.validate_days(days)
        rows = await self._usage.by_user(days)
        for r in rows:
            scopes = scopes_for_user(r["email"], self.settings.admin_emails)
            r["role"] = "admin" if ADMIN in scopes else "leitura"
            r["active"] = r["disabled_at"] is None
            r["context_tokens"] = U.approx_tokens(r["context_bytes"])  # só MCP
        return rows

    async def set_user_disabled(self, principal: Principal | None, email: str, disabled: bool) -> dict[str, Any]:
        """Desativa ou reativa um usuário: desativado, nenhuma credencial dele vale (sessão, agent, OAuth)
        e ela não consegue entrar. principal=None: operador local (CLI)."""
        email = (email or "").strip().lower()
        if "@" not in email:
            raise ValueError("informe o e-mail do usuário")
        if principal is not None:
            if not principal.has(ADMIN):
                raise PermissionError("só admin altera o acesso de usuários")
            if principal.kind != "session":  # um agent (que lê conteúdo não confiável) não bloqueia ninguém
                raise PermissionError("só um admin logado no navegador altera o acesso de usuários")
            if disabled and principal.subject == email:
                raise PermissionError("você não pode desativar o próprio acesso")
        user = await self._users.set_disabled(email, disabled)
        if user is None:
            raise ValueError(f"usuário não encontrado: {email}")
        return {"email": user["email"], "active": user["disabled_at"] is None,
                "disabled_at": user["disabled_at"].isoformat(timespec="seconds") if user["disabled_at"] else None}

    async def housekeeping(self) -> dict[str, int]:
        """Limpeza periódica: eventos de uso além da retenção, sessões/tokens OAuth e refresh tokens vencidos."""
        return {
            "usage_events": await self._usage.purge(max(1, self.settings.usage_retention_days)),
            "sessions": await self._tokens.purge_expired_sessions(),
            "oauth_refresh": await self._oauth.purge_expired(),
        }
