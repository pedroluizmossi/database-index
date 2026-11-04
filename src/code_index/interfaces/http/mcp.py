"""Servidor MCP (Streamable HTTP) montado em /mcp, protegido pelos mesmos tokens da API.

- Toda requisição exige `Authorization: Bearer cix_...` com scope `search:read`
  (o SDK do MCP responde 401/403 antes de chegar às tools).
- O MCP só lê: indexar (que gera custo na Voyage) fica na API e na interface, com confirmação.
- Proteção contra DNS rebinding: só aceita os hosts/origens configurados.
- Cada chamada de tool passa pelo rate limit e vai para a auditoria (sem o conteúdo retornado).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from ...domain.auth import SEARCH_READ, Principal
from ...services import CodeIndexService
from ...settings import Settings
from .security import RateLimiter, enforce_rate_limit, origin_of

ServiceGetter = Callable[[], CodeIndexService]


class ServiceTokenVerifier:
    """Valida os tokens `cix_` no banco (mesma regra da API REST)."""

    def __init__(self, get_service: ServiceGetter) -> None:
        self._get_service = get_service

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = await self._get_service().authenticate(token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            client_id=principal.token_name or principal.kind,
            scopes=sorted(principal.scopes),
            subject=principal.subject,
            claims={"kind": principal.kind, "token_id": principal.token_id, "token_name": principal.token_name},
        )


def _current_principal() -> Principal:
    at = get_access_token()
    if at is None:  # o middleware de auth do SDK já barra isso; defesa em profundidade
        raise ToolError("não autenticado")
    claims = at.claims or {}
    return Principal(
        subject=at.subject or "?",
        kind=claims.get("kind", "agent"),
        scopes=frozenset(at.scopes),
        token_id=claims.get("token_id"),
        token_name=claims.get("token_name"),
    )


def transport_security(settings: Settings) -> TransportSecuritySettings:
    public = urlsplit(settings.public_url)
    hosts = {public.netloc, *settings.allowed_hosts}
    origins = {origin_of(settings.public_url)}
    if settings.http_host in ("127.0.0.1", "localhost", "::1"):
        hosts |= {"127.0.0.1:*", "localhost:*", "[::1]:*"}
        origins |= {"http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"}
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=sorted(hosts), allowed_origins=sorted(origins)
    )


def build_mcp(settings: Settings, get_service: ServiceGetter, limiter: RateLimiter) -> MCPServer[Any]:
    base = settings.public_url.rstrip("/")
    server: MCPServer[Any] = MCPServer(
        name="code-index",
        instructions=(
            "Índice semântico + full-text de repositórios de código. Use search_code para "
            "encontrar implementações por descrição em linguagem natural ou por identificadores; "
            "depois read_file_lines para ver o contexto completo."
        ),
        token_verifier=ServiceTokenVerifier(get_service),
        auth=AuthSettings(
            issuer_url=base,  # type: ignore[arg-type]
            resource_server_url=f"{base}/mcp",  # type: ignore[arg-type]
            required_scopes=[SEARCH_READ],
            validate_token_resource=False,  # tokens opacos nossos; validação feita no verifier
        ),
    )

    async def run_tool(name: str, params: dict[str, Any], bucket: str, scope: str | None, fn: Callable[[CodeIndexService], Any]) -> Any:
        principal = _current_principal()
        svc = get_service()
        if scope and not principal.has(scope):
            await svc.audit(principal, f"mcp:{name}", params=params, status=403)
            raise ToolError(f"scope necessário: {scope}")
        try:
            enforce_rate_limit(limiter, settings, principal, bucket)
        except Exception:
            await svc.audit(principal, f"mcp:{name}", params=params, status=429)
            raise ToolError("limite de requisições atingido; tente mais tarde") from None
        started = time.monotonic()
        try:
            result = await fn(svc)
        except (ValueError, RuntimeError, PermissionError) as e:
            await svc.audit(principal, f"mcp:{name}", params=params, status=400)
            raise ToolError(str(e)) from None
        params = {**params, "ms": int((time.monotonic() - started) * 1000)}
        await svc.audit(principal, f"mcp:{name}", params=params, status=200)
        return result

    @server.tool()
    async def search_code(
        query: str,
        top_k: int = 8,
        repos: list[str] | None = None,
        language: str | None = None,
        path_glob: str | None = None,
        mode: str = "hybrid",
        rerank: bool = True,
    ) -> list[dict]:
        """Busca trechos de código.

        Args:
            query: descrição em linguagem natural ("onde validamos o token JWT") ou identificadores.
            top_k: número de resultados (máx. 50).
            repos: limita a estes repositórios (nomes de list_repos).
            language: filtra por linguagem (ex.: "python", "java", "typescript").
            path_glob: filtra por caminho, ex.: "src/**/*.ts".
            mode: "hybrid" (padrão), "semantic" ou "keyword".
            rerank: reordena os candidatos com o reranker da Voyage (padrão: sim).
        """
        top_k = max(1, min(top_k, 50))

        async def fn(svc: CodeIndexService) -> list[dict]:
            hits = await svc.search(query, top_k=top_k, repos=repos, language=language,
                                    path_glob=path_glob, mode=mode, rerank=rerank)
            return [h.to_dict() for h in hits]

        return await run_tool("search_code", {"query": query, "top_k": top_k, "repos": repos, "language": language,
                                              "path_glob": path_glob, "mode": mode}, "search", None, fn)

    @server.tool()
    async def read_file_lines(repo: str, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Lê um arquivo (ou intervalo de linhas) de um repositório indexado, com números de linha.

        Segredos (senhas, tokens, chaves) aparecem mascarados como ***.
        """
        return await run_tool(
            "read_file_lines", {"repo": repo, "path": path, "start_line": start_line, "end_line": end_line},
            "read", None, lambda svc: svc.read_file(repo, path, start_line, end_line),
        )

    @server.tool()
    async def list_repos(status: str | None = None, org: str | None = None, name: str | None = None) -> list[dict]:
        """Lista os repositórios conhecidos e seu estado de indexação.

        Args:
            status: "indexed", "pending", "error" ou "ignored".
            org: filtra por org do GitHub.
            name: trecho do nome do repo.
        """

        async def fn(svc: CodeIndexService) -> list[dict]:
            return [r.to_dict() for r in await svc.list_repos(status=status, org=org, name=name)]

        return await run_tool("list_repos", {"status": status, "org": org, "name": name}, "read", None, fn)

    return server
