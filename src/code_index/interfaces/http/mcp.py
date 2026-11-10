"""Servidor MCP (Streamable HTTP) montado em /mcp, protegido pelos mesmos tokens da API.

- Toda requisição exige `Authorization: Bearer cix_...` com scope `search:read`
  (o SDK do MCP responde 401/403 antes de chegar às tools).
- O token vem colado (agent, emitido na interface) ou do OAuth: o cliente MCP abre o navegador, a pessoa
  entra com Google ou Microsoft e aprova (servidor de autorização em mcp_oauth.py, metadados em /.well-known).
- O MCP só lê: indexar fica na API e na interface, com confirmação.
- Proteção contra DNS rebinding: só aceita os hosts/origens configurados.
- Cada chamada de tool passa pelo rate limit e vai para a auditoria e para o uso (`usage_events`: cliente MCP,
  tempo, itens e tamanho da resposta, consumo da Voyage), nunca com o conteúdo retornado.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from ...domain.auth import SEARCH_READ, Principal
from ...domain.oauth import OAUTH_SCOPES
from ...domain.usage import client_from_user_agent
from ...services import CodeIndexService
from ...settings import Settings
from .mcp_oauth import CodeIndexOAuthProvider
from .mcp_guide import GUIDE, INSTRUCTIONS, investigation_prompt
from .security import RateLimiter, enforce_rate_limit, origin_of

ServiceGetter = Callable[[], CodeIndexService]
log = logging.getLogger(__name__)

MCP_CODE_MAX_CHARS = 4000  # trecho maior (ex.: JSON minificado) é cortado; o resto sai via read_file_lines


_REPO_DROP = ("source", "pushed_at", "size_kb", "skipped", "card_stale", "owner_confidence")


def _compact_repo(repo: dict) -> dict:
    """list_repos no MCP: sem campos vazios ou de operação, resumo do cartão cortado na 1ª frase."""
    out = {k: v for k, v in repo.items() if k not in _REPO_DROP and v not in (None, [], "", 0, False)}
    summary = out.get("summary")
    if summary:
        first = summary.split(". ", 1)[0].rstrip(".")
        out["summary"] = (first[:200] + "…") if len(first) > 200 else first + "."
    return out


def _compact_hit(hit: dict) -> dict:
    """Resultado de busca enxuto para o contexto do agent: sem campos vazios e sem trechos gigantes."""
    out = {k: v for k, v in hit.items() if v not in (None, [], "")}
    code = out.get("code", "")
    if len(code) > MCP_CODE_MAX_CHARS:
        out["code"] = code[:MCP_CODE_MAX_CHARS] + f"\n… (trecho cortado: {len(code)} caracteres; leia com read_file_lines)"
    return out


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
        oauth_client_id=claims.get("oauth_client_id"),
        user_id=claims.get("user_id"),
    )


def _client_of(ctx: Context[Any, Any] | None) -> tuple[str | None, str | None]:
    """Cliente MCP da chamada: clientInfo do `initialize` (quando a sessão o tem) ou o User-Agent HTTP.
    O servidor roda sem estado, então em geral é o User-Agent ('claude-code/2.0.31' → claude-code)."""
    if ctx is None:
        return None, None
    try:
        rc = ctx.request_context
    except ValueError:
        return None, None
    try:
        params = rc.session.client_params
    except Exception:  # noqa: BLE001 - sessão sem initialize (modo sem estado)
        params = None
    info = getattr(params, "client_info", None)
    if info is not None and getattr(info, "name", None):
        return info.name, getattr(info, "version", None)
    request = rc.request
    headers = getattr(request, "headers", None)
    return client_from_user_agent(headers.get("user-agent") if headers is not None else None)


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
        # Servidor de autorização OAuth: valida também os tokens `cix_` colados (load_access_token).
        auth_server_provider=CodeIndexOAuthProvider(settings, get_service),
        auth=AuthSettings(
            issuer_url=base,  # type: ignore[arg-type]
            resource_server_url=f"{base}/mcp",  # type: ignore[arg-type]
            required_scopes=[SEARCH_READ],
            validate_token_resource=False,  # tokens opacos nossos; validação feita no provider
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=list(OAUTH_SCOPES), default_scopes=list(OAUTH_SCOPES),
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    async def run_tool(ctx: Context[Any, Any] | None, name: str, params: dict[str, Any], bucket: str,
                       scope: str | None, fn: Callable[[CodeIndexService], Any]) -> Any:
        principal = _current_principal()
        svc = get_service()
        client, client_version = _client_of(ctx)
        started = time.monotonic()

        async def done(status: int, result: Any = None, *, llm_usage: Any = None) -> None:
            ms = int((time.monotonic() - started) * 1000)
            await svc.audit(principal, f"mcp:{name}", params={**params, "ms": ms} if status == 200 else params,
                            status=status)
            extra = {"result": result} if status == 200 else {}
            await svc.record_usage(principal, transport="mcp", action=name, status=status, duration_ms=ms,
                                   llm_usage=llm_usage, client=client, client_version=client_version,
                                   params=params, **extra)

        if scope and not principal.has(scope):
            await done(403)
            raise ToolError(f"scope necessário: {scope}")
        try:
            enforce_rate_limit(limiter, settings, principal, bucket)
        except Exception:
            await done(429)
            raise ToolError("limite de requisições atingido; tente mais tarde") from None
        with svc.metered() as meter:
            try:
                result = await fn(svc)
            except (ValueError, RuntimeError, PermissionError) as e:
                await done(400, llm_usage=meter.snapshot())
                raise ToolError(str(e)) from None
            except ToolError:
                await done(400, llm_usage=meter.snapshot())
                raise
            except Exception:
                log.exception("tool MCP %s falhou", name)
                await done(500, llm_usage=meter.snapshot())
                raise ToolError("erro interno ao executar a tool; tente de novo mais tarde") from None
        await done(200, result, llm_usage=meter.snapshot())
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
        domain: str | None = None,
        service: str | None = None,
        ctx: Context | None = None,
    ) -> list[dict]:
        """Busca trechos de código e de documentação (README, docs/ e páginas de wiki).

        Args:
            query: descrição em linguagem natural ("onde validamos o token JWT") ou identificadores.
            top_k: número de resultados (máx. 50).
            repos: limita a estes repositórios (nomes de list_repos).
            language: filtra por linguagem (ex.: "python", "java", "typescript").
            path_glob: filtra por caminho, ex.: "src/**/*.ts".
            mode: "hybrid" (padrão) combina significado e termos; prefira para investigar comportamentos.
                "semantic" busca por significado; use para explorar conceitos com palavras diferentes.
                "keyword" busca termos e identificadores; use quando souber palavras específicas. Pode
                perder implementações que usam outros nomes. Para texto exato, prefira grep_code.
            rerank: reordena candidatos pela relevância para a consulta (padrão: sim). É independente
                do mode; mantenha ativado para investigar, salvo necessidade específica.
            kind: "code" (só código), "doc" (só documentação) ou vazio (ambos).
            include_linked_docs: com `repos`, inclui as páginas de wiki ligadas a esses repos (padrão: sim).
            exclude_repos: repos que NÃO podem aparecer no resultado (nomes de list_repos).
            exclude_path_glob: globs de caminho que NÃO podem aparecer, ex.: ["**/old/**", "**/*.generated.*"].
            max_docs: máximo de páginas de documentação quando kind é vazio (padrão: top_k/4, mín. 2).
            domain: só o código deste domínio do DDD (nome técnico de list_domains, ex.: "platform").
            service: com domain, só este serviço (ex.: "authentication"). Inclui pastas de monorepos.

        Cada resultado traz `owners` (domínio/serviço dono do arquivo), quando conhecido.
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
                                    exclude_path_glob=exclude_path_glob, max_docs=max_docs,
                                    domain=domain, service=service)
            return [_compact_hit(h.to_dict()) for h in hits]

        return await run_tool(ctx, "search_code", {"query": query, "top_k": top_k, "repos": repos, "language": language,
                                              "path_glob": path_glob, "mode": mode, "kind": kind,
                                              "include_linked_docs": include_linked_docs,
                                              "exclude_repos": exclude_repos,
                                              "exclude_path_glob": exclude_path_glob, "max_docs": max_docs,
                                              "domain": domain, "service": service},
                               "search", None, fn)

    @server.tool()
    async def read_file_lines(repo: str, path: str, start_line: int | None = None, end_line: int | None = None,
                              ctx: Context | None = None) -> str:
        """Lê um arquivo (ou intervalo de linhas) de um repositório indexado, com números de linha.

        `repo` aceita o nome como em list_repos ('org__repo') ou só 'repo'. Sem intervalo, devolve
        até 2000 linhas e diz como continuar. Arquivos ocultos (.env, .git/...) não são lidos.
        Segredos (senhas, tokens, chaves) aparecem mascarados como ***.
        """
        return await run_tool(
            ctx, "read_file_lines", {"repo": repo, "path": path, "start_line": start_line, "end_line": end_line},
            "read", None, lambda svc: svc.read_file(repo, path, start_line, end_line),
        )

    @server.tool()
    async def list_repos(status: str | None = None, org: str | None = None, name: str | None = None,
                         domain: str | None = None, ctx: Context | None = None) -> list[dict]:
        """Lista os repositórios conhecidos, seu estado de indexação e o dono no DDD (`owners`).

        Args:
            status: "indexed", "pending", "error" ou "ignored".
            org: filtra por org do GitHub.
            name: trecho do nome do repo.
            domain: só repos deste domínio ("platform") ou serviço ("platform/authentication").
        """

        async def fn(svc: CodeIndexService) -> list[dict]:
            rows = await svc.list_repos(status=status, org=org, name=name)
            if domain:
                want = domain.strip().lower().rstrip("/")
                rows = [r for r in rows if any(o == want or o.startswith(want + "/") for o in r.owners)]
            return [_compact_repo(r.to_dict()) for r in rows]

        return await run_tool(ctx, "list_repos", {"status": status, "org": org, "name": name, "domain": domain},
                              "read", None, fn)

    @server.tool()
    async def list_domains(ctx: Context | None = None) -> dict:
        """Domínios e serviços do DDD (do SDL e os definidos pela equipe), com rótulo e quantos repos cada
        um tem. Use os nomes técnicos em `search_code(domain=..., service=...)` e `list_repos(domain=...)`."""

        async def fn(svc: CodeIndexService) -> dict:
            catalog = await svc.ownership_catalog()
            return {
                "domains": [
                    {k: v for k, v in {
                        "name": d["name"], "label": d["label"], "repos": d["repos"],
                        "services": [{k2: v2 for k2, v2 in {"name": s["name"], "label": s["label"],
                                                              "repos": s["repos"]}.items() if v2 not in ("", None)}
                                     for s in d["services"]],
                    }.items() if v not in ("", None, [])}
                    for d in catalog["domains"]
                ],
                "coverage": {k: catalog["coverage"][k] for k in ("repos", "owned", "percent")},
            }

        return await run_tool(ctx, "list_domains", {}, "read", None, fn)

    @server.tool()
    async def find_repos(query: str, limit: int = 10, ctx: Context | None = None) -> list[dict]:
        """Descobre QUAL repo cuida de um assunto, pelo cartão do repo (resumo, capacidades, tags, operações SDL).

        Use antes de search_code quando não souber o repo: "quem envia e-mail", "serviço de tenants".
        Devolve repo, score, summary, kind e tags; filtre search_code por esses repos em seguida.
        Os cartões são escritos por agents a partir do código: confirme no código antes de afirmar.

        Args:
            query: assunto ou pergunta, em português ou inglês.
            limit: máximo de repos (até 50).
        """
        limit = max(1, min(limit, 50))
        return await run_tool(ctx, "find_repos", {"query": query, "limit": limit}, "read", None,
                              lambda svc: svc.find_repos(query, limit=limit))

    @server.tool()
    async def find_api_impl(operation: str, service: str | None = None, ctx: Context | None = None) -> list[dict]:
        """Onde uma operação da API da plataforma é implementada: repo, classe handler, arquivo e linha.

        Use com resultados de documentação de API (título 'POST /servico/queries/operacao') ou quando
        souber o nome da operação. Casamento pela convenção do SDK (`<operacao>Handler`), sem LLM.

        Args:
            operation: 'listBlacklistedEmails', 'email.listBlacklistedEmails' ou 'POST /email/queries/listBlacklistedEmails'.
            service: filtra pelo serviço (ex.: 'email').
        """
        return await run_tool(ctx, "find_api_impl", {"operation": operation, "service": service}, "read", None,
                              lambda svc: svc.find_api_impl(operation, service=service))

    @server.tool()
    async def related(entity: str, type: str | None = None, ctx: Context | None = None) -> dict:
        """Tudo ligado a uma entidade: páginas que documentam um repo, repos relacionados, operação ↔ handler,
        página de API → operação, dono no DDD (repo → serviço/domínio). Cada relação traz a origem (manual, sdl,
        handler, tag, menção, repo irmão...).

        Args:
            entity: nome do repo ('snr-email-backend') ou chave: 'repo:<nome>', 'doc:<fonte>/<caminho>',
                'op:<domínio>/<serviço>/<operação>', 'code:<repo>/<caminho>:<linha>', 'svc:<domínio>/<serviço>',
                'dom:<domínio>'.
            type: filtra o tipo: documents | describes | implemented_by | related_to | belongs_to.
        """
        return await run_tool(ctx, "related", {"entity": entity, "type": type}, "read", None,
                              lambda svc: svc.related(entity, type_=type, limit=200))

    @server.tool()
    async def repo_card(repo: str, ctx: Context | None = None) -> dict:
        """Cartão completo de um repo: resumo, capacidades, glossário, repos relacionados (com a base:
        "codigo" = referência vista no código, "nome" = só pelo nome), por onde começar a ler e os fatos
        extraídos sem LLM (catalog-info, operações e eventos do .sdl, propriedades do .pdl, pastas).

        Args:
            repo: nome como em list_repos ('org__repo') ou só 'repo'.
        """

        async def fn(svc: CodeIndexService) -> dict:
            info = await svc.repo_card(repo)
            if info is None:
                raise ValueError(f"sem cartão nem fatos para {repo}")
            return info

        return await run_tool(ctx, "repo_card", {"repo": repo}, "read", None, fn)

    @server.tool()
    async def find_symbol(
        name: str,
        kind: str | None = None,
        repos: list[str] | None = None,
        language: str | None = None,
        limit: int = 20,
        ctx: Context | None = None,
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

        return await run_tool(ctx, "find_symbol", {"name": name, "kind": kind, "repos": repos, "language": language},
                              "read", None, fn)

    @server.tool()
    async def grep_code(
        pattern: str,
        repos: list[str] | None = None,
        path_glob: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 50,
        ctx: Context | None = None,
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

        return await run_tool(ctx, "grep_code", {"pattern": pattern, "repos": repos, "path_glob": path_glob, "regex": regex},
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
