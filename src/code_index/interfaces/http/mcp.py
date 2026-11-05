"""Servidor MCP (Streamable HTTP) montado em /mcp, protegido pelos mesmos tokens da API.

- Toda requisição exige `Authorization: Bearer cix_...` com scope `search:read`
  (o SDK do MCP responde 401/403 antes de chegar às tools).
- O MCP só lê: indexar (que gera custo na Voyage) fica na API e na interface, com confirmação.
- Proteção contra DNS rebinding: só aceita os hosts/origens configurados.
- Cada chamada de tool passa pelo rate limit e vai para a auditoria (sem o conteúdo retornado).
"""

from __future__ import annotations

import logging
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
from .mcp_guide import GUIDE, INSTRUCTIONS, investigation_prompt
from .security import RateLimiter, enforce_rate_limit, origin_of

ServiceGetter = Callable[[], CodeIndexService]
log = logging.getLogger(__name__)

MCP_CODE_MAX_CHARS = 4000  # trecho maior (ex.: JSON minificado) é cortado; o resto sai via read_file_lines


def _compact_hit(hit: dict) -> dict:
    """Resultado de busca enxuto para o contexto do agent: sem campos vazios e sem trechos gigantes."""
    out = {k: v for k, v in hit.items() if v not in (None, [], "")}
    code = out.get("code", "")
    if len(code) > MCP_CODE_MAX_CHARS:
        out["code"] = code[:MCP_CODE_MAX_CHARS] + f"\n… (trecho cortado: {len(code)} caracteres; leia com read_file_lines)"
    return out


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
        instructions=INSTRUCTIONS,
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
        except ToolError:
            raise
        except Exception:
            log.exception("tool MCP %s falhou", name)
            await svc.audit(principal, f"mcp:{name}", params=params, status=500)
            raise ToolError("erro interno ao executar a tool; tente de novo mais tarde") from None
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
        kind: str | None = None,
        include_linked_docs: bool = True,
        exclude_repos: list[str] | None = None,
        exclude_path_glob: list[str] | None = None,
        max_docs: int | None = None,
    ) -> list[dict]:
        """Busca trechos de código e de documentação (README, docs/ e páginas de wiki).

        Args:
            query: descrição em linguagem natural ("onde validamos o token JWT") ou identificadores.
            top_k: número de resultados (máx. 50).
            repos: limita a estes repositórios (nomes de list_repos).
            language: filtra por linguagem (ex.: "python", "java", "typescript").
            path_glob: filtra por caminho, ex.: "src/**/*.ts".
            mode: "hybrid" (padrão), "semantic" ou "keyword".
            rerank: reordena os candidatos com o reranker da Voyage (padrão: sim).
            kind: "code" (só código), "doc" (só documentação) ou vazio (ambos).
            include_linked_docs: com `repos`, inclui as páginas de wiki ligadas a esses repos (padrão: sim).
            exclude_repos: repos que NÃO podem aparecer no resultado (nomes de list_repos).
            exclude_path_glob: globs de caminho que NÃO podem aparecer, ex.: ["**/old/**", "**/*.generated.*"].
            max_docs: máximo de páginas de documentação quando kind é vazio (padrão: top_k/4, mín. 2).

        Resultados de documentação externa trazem `url` (página original), `title` e `linked_repos`.
        Trechos vizinhos do mesmo arquivo vêm fundidos (no máximo 2 por arquivo); trechos muito
        longos vêm cortados (leia o resto com read_file_lines). Trecho idêntico em vários repos
        (boilerplate) aparece uma vez, com os outros repos em `also_in`.
        """
        top_k = max(1, min(top_k, 50))

        async def fn(svc: CodeIndexService) -> list[dict]:
            hits = await svc.search(query, top_k=top_k, repos=repos, language=language,
                                    path_glob=path_glob, mode=mode, rerank=rerank, kind=kind,
                                    include_linked_docs=include_linked_docs, exclude_repos=exclude_repos,
                                    exclude_path_glob=exclude_path_glob, max_docs=max_docs)
            return [_compact_hit(h.to_dict()) for h in hits]

        return await run_tool("search_code", {"query": query, "top_k": top_k, "repos": repos, "language": language,
                                              "path_glob": path_glob, "mode": mode, "kind": kind,
                                              "include_linked_docs": include_linked_docs,
                                              "exclude_repos": exclude_repos,
                                              "exclude_path_glob": exclude_path_glob, "max_docs": max_docs},
                               "search", None, fn)

    @server.tool()
    async def read_file_lines(repo: str, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Lê um arquivo (ou intervalo de linhas) de um repositório indexado, com números de linha.

        `repo` aceita o nome como em list_repos ('org__repo') ou só 'repo'. Sem intervalo, devolve
        até 2000 linhas e diz como continuar. Arquivos ocultos (.env, .git/...) não são lidos.
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

    @server.tool()
    async def find_symbol(
        name: str,
        kind: str | None = None,
        repos: list[str] | None = None,
        language: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Onde uma classe, interface, função, método ou recurso é DECLARADO (arquivo e linha).

        Use quando já sabe o nome (ou parte dele). Mais preciso que search_code para "onde está X".

        Args:
            name: nome ou parte dele ("HmacService"), qualificado ("WebhookService.retry"; se a classe
                não casar, procura só o método) ou "Classe.*" para listar os membros da classe.
            kind: "container" (classe/interface/enum), "member" (método/função) ou "section"
                (recurso Terraform, seção de Markdown, objeto do Kubernetes...).
            repos: limita a estes repositórios.
            language: filtra por linguagem.
            limit: máximo de resultados (até 200).
        """

        async def fn(svc: CodeIndexService) -> list[dict]:
            hits = await svc.find_symbol(name, kind=kind, repos=repos, language=language, limit=limit)
            return [h.to_dict() for h in hits]

        return await run_tool("find_symbol", {"name": name, "kind": kind, "repos": repos, "language": language},
                              "read", None, fn)

    @server.tool()
    async def grep_code(
        pattern: str,
        repos: list[str] | None = None,
        path_glob: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> dict:
        """Busca TEXTO EXATO (ou regex) nos repositórios: mensagens de erro, chaves de configuração,
        strings, chamadas específicas. Complementa search_code, que busca por significado.

        Args:
            pattern: texto a procurar (ou expressão regular, se regex=true).
            repos: limita a estes repositórios.
            path_glob: filtra caminhos, ex.: "**/*.java".
            regex: trata `pattern` como expressão regular (sintaxe do ripgrep).
            case_sensitive: diferencia maiúsculas/minúsculas (padrão: não).
            max_results: máximo de linhas (até 500).

        Retorna {"matches": [{repo, path, line, text}], "truncated": bool}. Segredos aparecem como ***.
        """

        async def fn(svc: CodeIndexService) -> dict:
            res = await svc.grep(pattern, repos=repos, path_glob=path_glob, regex=regex,
                                 case_sensitive=case_sensitive, max_results=max_results)
            return {"matches": [m.to_dict() for m in res.matches], "truncated": res.truncated}

        return await run_tool("grep_code", {"pattern": pattern, "repos": repos, "path_glob": path_glob, "regex": regex},
                              "search", None, fn)

    # ------------------------------------------------------------------ guia de uso
    # Texto fixo (sem dados do índice): não passa por run_tool. A autenticação do transporte já vale.

    @server.prompt(name="guia_de_uso", title="Guia de uso do code-index",
                   description="Como escolher e combinar as tools do code-index sem desperdício.")
    def guia_de_uso() -> str:
        return GUIDE

    @server.prompt(name="investigar", title="Investigar com o code-index",
                   description="Responde uma pergunta sobre o código seguindo o guia de uso.")
    def investigar(pergunta: str) -> str:
        return investigation_prompt(pergunta)

    @server.resource("code-index://guia", name="guia", title="Guia de uso do code-index",
                     description="Como escolher e combinar as tools do code-index.", mime_type="text/markdown")
    def guia() -> str:
        return GUIDE

    return server
