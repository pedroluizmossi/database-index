"""Servidor MCP (stdio) expondo a busca no índice de código."""

from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .search import read_file, repo_status, search

server = MCPServer(
    name="code-index",
    instructions=(
        "Índice semântico + full-text de repositórios de código. Use search_code para "
        "encontrar implementações por descrição em linguagem natural ou por identificadores; "
        "depois read_file para ver o contexto completo."
    ),
)


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
        top_k: número de resultados.
        repos: limita a estes repositórios (nomes de list_repos).
        language: filtra por linguagem (ex.: "python", "java", "typescript").
        path_glob: filtra por caminho, ex.: "src/**/*.ts".
        mode: "hybrid" (padrão), "semantic" ou "keyword".
        rerank: reordena os candidatos com o reranker da Voyage (padrão: sim).
    """
    try:
        hits = await search(query, top_k=top_k, repos=repos, language=language,
                            path_glob=path_glob, mode=mode, rerank=rerank)
    except ValueError as e:
        raise ToolError(str(e)) from None
    return [h.to_dict() for h in hits]


@server.tool()
def read_file_lines(repo: str, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Lê um arquivo (ou intervalo de linhas) de um repositório indexado, com números de linha."""
    try:
        return read_file(repo, path, start_line, end_line)
    except ValueError as e:
        raise ToolError(str(e)) from None


@server.tool()
async def list_repos(status: str | None = None, org: str | None = None, name: str | None = None) -> list[dict]:
    """Lista os repositórios conhecidos e seu estado de indexação.

    Args:
        status: "indexed", "pending", "error" ou "ignored".
        org: filtra por org do GitHub.
        name: trecho do nome do repo.
    """
    return await repo_status(status=status, org=org, name=name)


@server.tool()
async def reindex(repos: list[str] | None = None, batch: int = 5) -> dict:
    """Indexa repositórios.

    Args:
        repos: nomes a indexar agora ("org__repo", "org/repo" ou só "repo"; também aceita
            URL git). Se vazio, processa um lote dos repos pendentes ou alterados.
        batch: tamanho do lote quando `repos` é vazio.
    """
    from .indexer import sync

    try:
        res = await asyncio.to_thread(sync, repos or None, batch=batch, progress=False)
    except (ValueError, RuntimeError) as e:
        raise ToolError(str(e)) from None
    return res.to_dict()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server.run("stdio")


if __name__ == "__main__":
    main()
