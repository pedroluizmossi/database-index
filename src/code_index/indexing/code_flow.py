"""Pipeline CocoIndex: repos -> arquivos -> chunks (tree-sitter) -> embeddings Voyage -> pgvector.

Incremental: o CocoIndex memoiza por arquivo e por chunk, então após um
`git pull` só os arquivos alterados são re-chunkados e só chunks novos
geram chamadas de embedding. Repos removidos do repos.toml têm suas linhas
apagadas automaticamente.

Custo de mudanças: o embedding é memoizado pelo próprio LiteLLMEmbedder.embed (chave =
modelo + parâmetros + texto), independente destas funções. Alterar/mover este módulo
faz o CocoIndex revarrer e re-chunkar, mas só textos novos vão para a Voyage.
Mantenha estáveis: app name, ContextKeys, subpath ("repo", nome) e o schema da tabela.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

import asyncpg
import cocoindex as coco
from cocoindex.connectors import localfs, postgres
from cocoindex.ops.litellm import LiteLLMEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.file import FileLike, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator
from numpy.typing import NDArray

from ..domain.models import Repo
from ..infra import llm
from ..settings import get_settings
from .content import CONTENT_LOGIC, redact, skip_reason
from .context import Decl, chunk_bounds, chunk_header, declarations, full_lines, symbol_names
from ..domain.search_text import code_terms
from ..infra.chunk_store import FTS_DOCUMENT

PG_DB = coco.ContextKey[asyncpg.Pool]("code_index_db")
EMBEDDER = coco.ContextKey[LiteLLMEmbedder]("code_index_embedder", detect_change=True)

_splitter = RecursiveSplitter()


@dataclass
class CodeChunk:
    repo: str
    path: str
    id: int
    language: str
    code: str
    start_line: int
    end_line: int
    search_symbols: str   # caminho + nomes declarados/envolventes, tokenizados para busca (peso maior)
    search_terms: str     # identificadores e palavras do código, tokenizados para busca
    embedding: Annotated[NDArray, EMBEDDER]


@dataclass
class CodeSymbol:
    """Declaração encontrada na indexação (classe, método, função, recurso, seção). Sem embedding."""

    repo: str
    path: str
    line: int
    name: str
    kind: str             # container | member | section
    container: str        # classe/seção que envolve a declaração ("" se nenhuma)
    signature: str        # a linha da declaração (com segredos mascarados)
    language: str


@coco.lifespan
async def _lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    settings = get_settings()
    async with asyncpg.create_pool(settings.database_url) as pool:
        builder.provide(PG_DB, pool)
        builder.provide(EMBEDDER, llm.make_embedder(settings))
        try:
            yield
        finally:
            await llm.close_clients()


@coco.fn
async def process_chunk(
    item: ChunkInput,
    repo: str,
    path: str,
    language: str,
    id_gen: IdGenerator,
    table: postgres.TableTarget[CodeChunk],
) -> None:
    # Cabeçalho (caminho, linguagem, classe envolvente, declarações) dá contexto ao embedding.
    embedding = await coco.use_context(EMBEDDER).embed(f"{item.header}\n{item.code}", input_type="document")
    table.declare_row(
        row=CodeChunk(
            repo=repo,
            path=path,
            id=await id_gen.next_id(item.code),
            language=language,
            code=item.code,
            start_line=item.start_line,
            end_line=item.end_line,
            search_symbols=code_terms(item.symbols),
            search_terms=code_terms(item.code),
            embedding=embedding,
        )
    )


@dataclass(frozen=True)
class ChunkInput:
    """Chunk pronto para embedar: linhas inteiras (o que é guardado/exibido) + cabeçalho de contexto."""

    code: str
    header: str
    symbols: str          # caminho + nomes de declarações relevantes para o trecho
    start_line: int
    end_line: int


@coco.fn(memo=True, version=4)
async def process_file(
    file: FileLike,
    repo: str,
    root: str,
    table: postgres.TableTarget[CodeChunk],
    symbols_table: postgres.TableTarget[CodeSymbol],
) -> None:
    coco.use_logic_dep(CONTENT_LOGIC)
    path = file.file_path.path.relative_to(root).as_posix()
    text = await file.read_text()  # decodifica com errors="replace": binários são barrados em skip_reason
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # CRLF/CR: linhas iguais às do read_file
    if not text.strip() or len(text.encode()) > get_settings().max_file_bytes:
        return
    language = detect_code_language(filename=file.file_path.path.name) or ""
    if skip_reason(path, language, text):
        return
    text, _ = redact(text, language)  # segredos/certificados nunca vão para a Voyage nem para o banco
    chunks = _splitter.split(
        text,
        chunk_size=1500,
        min_chunk_size=300,
        chunk_overlap=200,
        language=language or None,
    )
    lines = text.split("\n")
    decls = declarations(text, language, file.file_path.path.name)
    _declare_symbols(symbols_table, repo, path, language, decls, lines)
    items = []
    for c in chunks:
        start, end = chunk_bounds(c.start.line, c.end.line, c.end.column)
        items.append(ChunkInput(
            code=full_lines(lines, start, end),
            header=chunk_header(repo, path, language, decls, start, end),
            symbols=" ".join([path, *symbol_names(decls, start, end)]),
            start_line=start,
            end_line=end,
        ))
    id_gen = IdGenerator()
    await coco.map(process_chunk, items, repo, path, language, id_gen, table)


def _declare_symbols(
    symbols_table: postgres.TableTarget[CodeSymbol],
    repo: str,
    path: str,
    language: str,
    decls: list[Decl],
    lines: list[str],
) -> None:
    container = ""
    seen: set[tuple[int, str]] = set()
    for d in decls:
        if (d.line, d.name) in seen:  # duas regras podem casar a mesma declaração
            continue
        seen.add((d.line, d.name))
        symbols_table.declare_row(row=CodeSymbol(
            repo=repo, path=path, line=d.line, name=d.name, kind=d.kind,
            container=container if d.kind == "member" else "",
            signature=lines[d.line - 1].strip()[:240] if 0 < d.line <= len(lines) else "",
            language=language,
        ))
        if d.kind == "container":
            container = d.name


@coco.fn(memo=True)
async def index_repo(
    repo: Repo,
    revision: str,
    table: postgres.TableTarget[CodeChunk],
    symbols_table: postgres.TableTarget[CodeSymbol],
) -> None:
    """Memoizado por (repo, revision): se o commit não mudou, o repo nem é percorrido
    e as linhas já gravadas são mantidas. Por isso o custo de um sync é proporcional
    aos repos que mudaram, não ao tamanho da org."""
    root = repo.local_path(get_settings().repos_dir)
    files = localfs.walk_dir(
        root,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=list(repo.include),
            excluded_patterns=repo.all_excludes,
        ),
    )
    await coco.mount_each(process_file, files.items(), repo.name, str(root), table, symbols_table)


@coco.fn
async def app_main(repos: list[tuple[Repo, str]]) -> None:
    """`repos` = todos os repos que devem existir no índice, com sua revisão.
    Repo ausente da lista tem suas linhas apagadas."""
    settings = get_settings()
    table = await postgres.mount_table_target(
        PG_DB,
        table_name=settings.chunks_table,
        table_schema=await postgres.TableSchema.from_class(
            CodeChunk, primary_key=["repo", "path", "id"]
        ),
        pg_schema_name=settings.pg_schema,
    )
    table.declare_vector_index(column="embedding", metric="cosine", method="hnsw")
    # Full-text do Postgres com pesos (símbolos A, código B): usado quando o banco não tem BM25.
    # O índice BM25 (pg_search), quando disponível, é criado pelo ChunkStore após o sync.
    table.declare_sql_command_attachment(
        name="fts_index",
        setup_sql=f"CREATE INDEX IF NOT EXISTS code_chunks_fts ON {settings.chunks_table_qualified} "
        f"USING gin ({FTS_DOCUMENT})",
        teardown_sql=f'DROP INDEX IF EXISTS "{settings.pg_schema}".code_chunks_fts',
    )
    symbols_table = await postgres.mount_table_target(
        PG_DB,
        table_name=settings.symbols_table,
        table_schema=await postgres.TableSchema.from_class(
            CodeSymbol, primary_key=["repo", "path", "line", "name"]
        ),
        pg_schema_name=settings.pg_schema,
    )
    symbols_table.declare_sql_command_attachment(
        name="name_index",
        setup_sql=f"CREATE INDEX IF NOT EXISTS code_symbols_name ON {settings.symbols_table_qualified} "
        f"(lower(name) text_pattern_ops)",
        teardown_sql=f'DROP INDEX IF EXISTS "{settings.pg_schema}".code_symbols_name',
    )
    for repo, revision in repos:
        await coco.mount(
            coco.component_subpath("repo", repo.name), index_repo, repo, revision, table, symbols_table
        )


def build_app(plan: list[tuple[Repo, str]]) -> coco.App:
    return coco.App(coco.AppConfig(name="CodeIndex"), app_main, repos=plan)
