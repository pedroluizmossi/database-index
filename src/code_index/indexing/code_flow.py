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
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import FileLike, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator
from numpy.typing import NDArray

from ..domain.models import Repo
from ..infra import llm
from ..settings import get_settings
from .content import CONTENT_LOGIC, redact, skip_reason

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
    embedding: Annotated[NDArray, EMBEDDER]


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
    chunk: Chunk,
    repo: str,
    path: str,
    language: str,
    id_gen: IdGenerator,
    table: postgres.TableTarget[CodeChunk],
) -> None:
    # Cabeçalho com o caminho dá contexto ao modelo (ajuda em buscas por módulo/arquivo).
    text = f"# {repo}/{path}\n{chunk.text}"
    embedding = await coco.use_context(EMBEDDER).embed(text, input_type="document")
    table.declare_row(
        row=CodeChunk(
            repo=repo,
            path=path,
            id=await id_gen.next_id(chunk.text),
            language=language,
            code=chunk.text,
            start_line=chunk.start.line,
            end_line=chunk.end.line,
            embedding=embedding,
        )
    )


@coco.fn(memo=True, version=2)
async def process_file(
    file: FileLike, repo: str, root: str, table: postgres.TableTarget[CodeChunk]
) -> None:
    coco.use_logic_dep(CONTENT_LOGIC)
    path = file.file_path.path.relative_to(root).as_posix()
    try:
        text = await file.read_text()
    except UnicodeDecodeError:
        return
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
    id_gen = IdGenerator()
    await coco.map(process_chunk, chunks, repo, path, language, id_gen, table)


@coco.fn(memo=True)
async def index_repo(repo: Repo, revision: str, table: postgres.TableTarget[CodeChunk]) -> None:
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
    await coco.mount_each(process_file, files.items(), repo.name, str(root), table)


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
    table.declare_sql_command_attachment(
        name="fts_index",
        setup_sql=f"CREATE INDEX IF NOT EXISTS code_chunks_fts ON {settings.chunks_table_qualified} "
        f"USING gin (to_tsvector('simple', code))",
        teardown_sql=f'DROP INDEX IF EXISTS "{settings.pg_schema}".code_chunks_fts',
    )
    for repo, revision in repos:
        await coco.mount(coco.component_subpath("repo", repo.name), index_repo, repo, revision, table)


def build_app(plan: list[tuple[Repo, str]]) -> coco.App:
    return coco.App(coco.AppConfig(name="CodeIndex"), app_main, repos=plan)
