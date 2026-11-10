"""Uso da aplicação (puro, sem I/O): o que um evento de uso guarda e como derivar as métricas.

Um evento por chamada: tool do MCP, rota da API ou ação da interface. Serve para ver quem usa o quê,
quanto custa (tokens/US$ da Voyage) e quanto contexto cada tool consome do agent (tamanho da resposta),
e para melhorar a busca depois (consultas frequentes, consultas sem resultado, tools lentas).

Nunca guardamos tokens nem o conteúdo devolvido: só parâmetros, contagens e tempos.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from .auth import TokenKind

Channel = Literal["mcp", "api", "ui"]
CHANNELS: tuple[Channel, ...] = ("mcp", "api", "ui")

BYTES_PER_TOKEN = 4          # aproximação usual de tokens de LLM para texto/código
MAX_PARAM_CHARS = 500        # consulta longa é cortada (não é lugar de guardar documentos)
MAX_PARAM_ITEMS = 20         # listas (repos, globs) longas também
MAX_REPORT_DAYS = 365

# Parâmetros que identificam "o que a pessoa procurou": usados nos relatórios de consultas.
QUERY_PARAMS = ("query", "pattern", "name", "operation", "entity", "q")
# Rotas de escrita que são consultas. As demais escritas (criar token, remover repo...) também têm `name`,
# mas não são buscas; das rotas da API só contam as de leitura (GET) e estas.
QUERY_WRITE_ACTIONS = ("POST /api/v1/search", "POST /api/v1/grep")

_SECRET_KEYS = re.compile(r"token|secret|password|senha|authorization|cookie", re.IGNORECASE)
_UA_PRODUCT = re.compile(r"^\s*([A-Za-z][\w.\-]*)(?:/([\w.\-+]+))?")


@dataclass(frozen=True)
class LLMUsage:
    """Consumo da Voyage (ou outro provedor via LiteLLM) numa chamada."""

    embed_tokens: int = 0
    rerank_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class UsageEvent:
    channel: Channel
    action: str
    status: int
    duration_ms: int
    user_email: str | None = None
    token_id: str | None = None
    token_kind: TokenKind | None = None
    client: str | None = None
    client_version: str | None = None
    result_count: int | None = None
    response_bytes: int | None = None
    llm: LLMUsage = field(default_factory=LLMUsage)
    params: dict[str, Any] | None = None
    attrs: dict[str, Any] | None = None


def channel_for(transport: Literal["mcp", "http"], token_kind: TokenKind | None) -> Channel:
    """MCP é sempre `mcp`; na API HTTP, sessão de navegador é a interface (`ui`), o resto é `api`."""
    if transport == "mcp":
        return "mcp"
    return "ui" if token_kind == "session" else "api"


def status_for(exc: BaseException) -> int:
    """Status de um erro de caso de uso (mesma regra da API: valor inválido 400, permissão 403, conflito 409, resto 500)."""
    if isinstance(exc, PermissionError):
        return 403
    if isinstance(exc, ValueError):
        return 400
    if isinstance(exc, RuntimeError):
        return 409
    return 500


def clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Parâmetros para guardar: sem vazios, sem nada com cara de segredo, textos e listas cortados."""
    if not params:
        return None
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == "" or value == [] or _SECRET_KEYS.search(key):
            continue
        if isinstance(value, str):
            value = value[:MAX_PARAM_CHARS]
        elif isinstance(value, (list, tuple)):
            value = [v[:MAX_PARAM_CHARS] if isinstance(v, str) else v for v in list(value)[:MAX_PARAM_ITEMS]]
        elif not isinstance(value, (int, float, bool)):
            value = str(value)[:MAX_PARAM_CHARS]
        out[key] = value
    return out or None


_COUNT_KEYS = ("matches", "results", "relations", "items", "hits", "domains")


def result_count(result: Any) -> int | None:
    """Quantos itens uma resposta trouxe: lista → tamanho; dict ou objeto (ex.: GrepResult) → a primeira lista
    conhecida dentro dele. Tupla é um par de valores (ex.: token, metadados), não uma lista de itens."""
    if isinstance(result, list):
        return len(result)
    for key in _COUNT_KEYS:
        value = result.get(key) if isinstance(result, Mapping) else getattr(result, key, None)
        if isinstance(value, (list, tuple)):
            return len(value)
    return None


def response_bytes(result: Any) -> int:
    """Tamanho da resposta em bytes UTF-8 (o que entra no contexto do agent)."""
    if result is None:
        return 0
    if isinstance(result, bytes):
        return len(result)
    text = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
    return len(text.encode("utf-8"))


def approx_tokens(n_bytes: int | float | None) -> int:
    return int(round((n_bytes or 0) / BYTES_PER_TOKEN))


def client_from_user_agent(user_agent: str | None) -> tuple[str | None, str | None]:
    """'claude-code/2.0.31 (cli)' → ('claude-code', '2.0.31'). Navegadores viram 'navegador'."""
    if not user_agent or not user_agent.strip():
        return None, None
    if user_agent.startswith("Mozilla/"):
        return "navegador", None
    m = _UA_PRODUCT.match(user_agent)
    if not m:
        return user_agent.strip()[:60], None
    return m.group(1).lower()[:60], (m.group(2) or None)


def normalize_client(name: str | None) -> str | None:
    """Nome do cliente MCP em minúsculas, sem espaços nas pontas (ex.: 'Claude Code' → 'claude code')."""
    if not name or not name.strip():
        return None
    return name.strip().lower()[:60]


def validate_days(days: int) -> int:
    if days < 1 or days > MAX_REPORT_DAYS:
        raise ValueError(f"período deve ser entre 1 e {MAX_REPORT_DAYS} dias")
    return days


def fill_days(rows: list[dict[str, Any]], days: int, today: date) -> list[dict[str, Any]]:
    """Série diária completa (dias sem uso entram zerados), do mais antigo para hoje."""
    by_day = {r["day"]: r for r in rows}
    zero = {k: 0 for r in rows for k in r if k != "day"} or {"mcp": 0, "api": 0, "ui": 0, "errors": 0, "users": 0}
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        out.append(by_day.get(day) or {"day": day, **zero})
    return out
