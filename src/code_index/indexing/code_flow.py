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
from pathlib import Path, PurePath
from typing import Annotated, Any

import asyncpg
import cocoindex as coco
import litellm
from cocoindex.connectors import localfs, postgres
from cocoindex.ops.litellm import LiteLLMEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.file import FileLike, FilePathMatcher, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator
from numpy.typing import NDArray

from ..domain.models import Repo
from ..infra import llm
from ..settings import Settings, get_settings
from .content import CONTENT_LOGIC, file_language, redact, skip_reason
from .chunking import (
    STRUCTURED_LANGS, chunking_for_tokens, enclosing_member, heading_path, markdown_headings, markdown_spans,
    package_name, structured_spans,
)
from .context import Chunking, Decl, chunk_header, chunk_spans, declarations, full_lines, symbol_names
from ..domain import doc_links
from ..domain.sync_errors import explain_error
from ..domain.doc_content import strip_embedded_blobs
from ..domain.doc_links import (
    MANIFESTS, alias_is_strong, blank_lines, manifest_aliases, page_mentions, page_path, parse_front_matter,
)
from ..profiles.registry import active_profiles
from ..domain.search_text import code_terms
from ..sources.wikijs import page_matches
from ..infra.chunk_store import FTS_DOCUMENT
from . import progress

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
) -> str | None:
    """Embeda e grava o trecho. Devolve None, ou o motivo quando o modelo recusou o texto (erro do pedido, que
    repetir não resolve: texto maior que o contexto, entrada inválida). Erro de rede/servidor sobe e o arquivo
    conta como erro (o CocoIndex tenta de novo no próximo sync)."""
    # Cabeçalho (caminho, linguagem, classe envolvente, declarações) dá contexto ao embedding.
    text = f"{item.header}\n{item.code}"
    limit = get_settings().embed_max_chars  # rede de segurança: só uma linha gigante sozinha passa disso
    try:
        embedding = await coco.use_context(EMBEDDER).embed(text[:limit] if limit else text, input_type="document")
    except litellm.BadRequestError as e:  # inclui ContextWindowExceededError
        return f"linhas {item.start_line}-{item.end_line}: {explain_error(str(e))}"
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
    return None


@dataclass
class DocPage:
    """Página de documentação externa: de onde veio (URL) e como se chama."""

    repo: str
    path: str
    url: str
    title: str
    tags: str          # separadas por espaço
    updated_at: str


@dataclass
class RepoAlias:
    """Apelido de um repo (nome, pacote, artefato, componente), usado para ligar documentação."""

    repo: str
    alias: str
    origin: str        # name | package.json | pom.xml | pyproject.toml | ... | catalog
    path: str          # arquivo de onde saiu ("" para os derivados do nome)
    strong: bool       # específico o bastante para casar por menção no texto


@dataclass
class DocMention:
    """Termo citado numa página de documentação (explicit | tag | url | mention)."""

    doc_repo: str
    doc_path: str
    term: str
    origin: str


@dataclass
class SkippedFile:
    """Arquivo descartado na indexação (acima do limite de tamanho, binário, CRD...). Some quando volta a ser indexado."""

    repo: str
    path: str
    reason: str
    size_bytes: int


@dataclass(frozen=True)
class ChunkInput:
    """Chunk pronto para embedar: linhas inteiras (o que é guardado/exibido) + cabeçalho de contexto."""

    code: str
    header: str
    symbols: str          # caminho + nomes de declarações relevantes para o trecho
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DocTargets:
    """Tabelas de documentação/vínculos, agrupadas para não alongar as assinaturas."""

    pages: postgres.TableTarget[DocPage]
    aliases: postgres.TableTarget[RepoAlias]
    mentions: postgres.TableTarget[DocMention]
    skipped: postgres.TableTarget[SkippedFile]

    def __coco_memo_key__(self) -> tuple[str, str, str, str]:
        return (self.pages.__coco_memo_key__(), self.aliases.__coco_memo_key__(),
                self.mentions.__coco_memo_key__(), self.skipped.__coco_memo_key__())


@coco.fn(memo=True, version=9)  # 9: trechos pela estrutura (seções, blocos de config), alvo em tokens, cabeçalho rico
async def process_file(
    file: FileLike,
    repo: str,
    root: str,
    kind: str,
    link_rules: tuple[tuple[str, tuple[str, ...]], ...],
    chunking: Chunking,
    table: postgres.TableTarget[CodeChunk],
    symbols_table: postgres.TableTarget[CodeSymbol],
    doc_targets: DocTargets,
) -> None:
    coco.use_logic_dep(CONTENT_LOGIC)
    path = file.file_path.path.relative_to(root).as_posix()
    text = await file.read_text()  # decodifica com errors="replace": binários são barrados em skip_reason
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # CRLF/CR: linhas iguais às do read_file
    if kind == "docs":
        text = strip_embedded_blobs(text)  # diagramas/imagens em base64 saem antes do limite de tamanho
    if not text.strip():
        return
    size = len(text.encode())
    if size > get_settings().max_file_bytes:
        doc_targets.skipped.declare_row(row=SkippedFile(repo=repo, path=path, reason="tamanho", size_bytes=size))
        return
    # tipos de arquivo dos perfis ativos (formatos que o detector não conhece); mesma exceção do get_settings acima
    file_types = active_profiles(get_settings()).file_types
    language = file_language(file.file_path.path.name, file_types)
    splitter_language = detect_code_language(filename=file.file_path.path.name)  # só o que o tree-sitter entende
    reason = skip_reason(path, language, text)
    if reason:
        doc_targets.skipped.declare_row(row=SkippedFile(repo=repo, path=path, reason=reason, size_bytes=size))
        return
    text, _ = redact(text, language, file_types)  # segredos/certificados nunca vão para a Voyage nem para o banco
    filename = file.file_path.path.name
    page_title = ""
    if kind == "docs":
        meta, fm_lines = parse_front_matter(text)
        text = blank_lines(text, fm_lines)  # front-matter fora dos chunks, numeração de linhas preservada
        page_title = str(meta.get("title") or path)
        tags = [str(t) for t in meta.get("tags") or []]
        doc_targets.pages.declare_row(row=DocPage(
            repo=repo, path=path, url=str(meta.get("url") or ""), title=page_title,
            tags=" ".join(tags), updated_at=str(meta.get("updated_at") or ""),
        ))
        for term, origin in page_mentions(page_path(path), tags, text, link_rules, page_matches):
            doc_targets.mentions.declare_row(row=DocMention(doc_repo=repo, doc_path=path, term=term, origin=origin))
    elif filename in MANIFESTS:
        for alias, origin in manifest_aliases(filename, text):
            doc_targets.aliases.declare_row(row=RepoAlias(
                repo=repo, alias=alias, origin=origin, path=path, strong=alias_is_strong(alias),
            ))
    lines = text.split("\n")
    decls = declarations(text, language, file.file_path.path.name, file_types)
    _declare_symbols(symbols_table, repo, path, language, decls, lines)
    headings = markdown_headings(lines) if language == "markdown" else []
    if language == "markdown":
        spans = markdown_spans(lines, chunking)
    elif language in STRUCTURED_LANGS:
        spans = structured_spans(lines, language, chunking)
    else:
        chunks = _splitter.split(
            text,
            chunk_size=chunking.size,
            min_chunk_size=chunking.min_size,
            chunk_overlap=chunking.overlap,
            language=splitter_language,
        )
        spans = chunk_spans([(c.start.line, c.end.line, c.end.column) for c in chunks], lines, chunking)
    package = package_name(text, language)
    items = []
    for start, end in spans:
        crumbs = heading_path(headings, start, end) if headings else None
        header = chunk_header(repo, path, language, decls, start, end, package=package,
                              member=enclosing_member(lines, decls, start, language),
                              sections=None if page_title else crumbs)
        if page_title:
            header += "\n# página: " + " › ".join([page_title, *(crumbs or [])])
        items.append(ChunkInput(
            code=full_lines(lines, start, end),
            header=header,
            symbols=" ".join([path, page_title, *symbol_names(decls, start, end)]),
            start_line=start,
            end_line=end,
        ))
    id_gen = IdGenerator()
    refused = [r for r in await coco.map(process_chunk, items, repo, path, language, id_gen, table) if r]
    if refused:
        # Visível em "descartados" (tela e `code-index skips`): o resto do arquivo entrou no índice.
        more = f" (e mais {len(refused) - 1})" if len(refused) > 1 else ""
        doc_targets.skipped.declare_row(row=SkippedFile(
            repo=repo, path=path, reason=f"trecho sem embedding, {refused[0]}{more}"[:500], size_bytes=size))


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


class _RepoMatcher(FilePathMatcher):
    """Filtros do repo (include/exclude) e nada de symlinks: o walker do CocoIndex segue links, e um link
    commitado (`config.yaml -> /app/.env`, `lib -> /etc`) levaria arquivos de fora do clone ao índice."""

    def __init__(self, root: Path, repo: Repo) -> None:
        self._root = root
        self._patterns = PatternFilePathMatcher(included_patterns=list(repo.include), excluded_patterns=repo.all_excludes)

    def is_dir_included(self, path: PurePath) -> bool:
        return self._patterns.is_dir_included(path) and not (self._root / path).is_symlink()

    def is_file_included(self, path: PurePath) -> bool:
        return self._patterns.is_file_included(path) and not (self._root / path).is_symlink()


def repo_name_aliases(repo: str) -> list[tuple[str, str]]:
    """Apelidos do nome do repo com as regras de nome dos perfis ativos (`doc_links.repo_name_aliases`).

    O perfil vem de `get_settings()`, como o resto deste módulo (o CocoIndex chama estas funções fora da injeção
    do serviço; mesma exceção do lifespan). Fica fora do `index_repo` memoizado de propósito: o corpo dele não
    muda. Trocar de perfil não revarre os repos: os apelidos do nome mudam no próximo commit de cada repo (ou
    com `sync --full`)."""
    return doc_links.repo_name_aliases(repo, active_profiles(get_settings()).naming)


def _walk_repo(repo: Repo) -> Any:
    root = repo.local_path(get_settings().repos_dir)
    return localfs.walk_dir(root, recursive=True, path_matcher=_RepoMatcher(root.resolve(), repo))


@coco.fn(memo=True)
async def index_repo(
    repo: Repo,
    revision: str,
    chunking: Chunking,
    table: postgres.TableTarget[CodeChunk],
    symbols_table: postgres.TableTarget[CodeSymbol],
    doc_targets: DocTargets,
) -> None:
    """Memoizado por (repo, revision, chunking): se o commit e o tamanho dos trechos não mudaram, o repo nem é percorrido
    e as linhas já gravadas são mantidas. Por isso o custo de um sync é proporcional
    aos repos que mudaram, não ao tamanho da org."""
    root = repo.local_path(get_settings().repos_dir)
    files = _walk_repo(repo)
    if repo.kind == "code":
        for alias, origin in repo_name_aliases(repo.name):
            doc_targets.aliases.declare_row(row=RepoAlias(
                repo=repo.name, alias=alias, origin=origin, path="", strong=alias_is_strong(alias),
            ))
    rules = tuple((r.path, r.repos) for r in repo.doc_source.links) if repo.doc_source else ()
    await coco.mount_each(
        process_file, files.items(), repo.name, str(root), repo.kind, rules, chunking,
        table, symbols_table, doc_targets,
    )


def chunking_from(settings: Settings) -> Chunking:
    """Tamanho dos trechos pelas configurações. É argumento do `process_file` (memo): mudar reprocessa."""
    return chunking_for_tokens(settings.chunk_tokens, settings.chunk_min_tokens, settings.chunk_max_tokens)


async def count_repo_files(repo: Repo) -> int:
    """Quantos arquivos o `index_repo` vai percorrer (mesma pasta e filtros). Só para o progresso do job.

    Fica fora do `index_repo` de propósito: mudar o corpo de uma função memoizada força revarrer os repos.
    """
    files = _walk_repo(repo)
    return sum([1 async for _ in files.items()])


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
    doc_targets = await _mount_doc_tables(settings)
    tracker = progress.active()
    chunking = chunking_from(settings)
    for repo, revision in repos:
        subpath = coco.component_subpath("repo", repo.name)
        if tracker is not None and tracker.tracks(repo.name):  # todos: os pedidos e os que o CocoIndex percorre
            # Contadores próprios do repo (arquivos/trechos) para o progresso do job. O grupo não muda o
            # caminho do componente: nada é reprocessado por causa dele.
            with coco.stats_group(repo.name) as group:
                tracker.attach(repo.name, group)
                await coco.mount(subpath, index_repo, repo, revision, chunking, table, symbols_table, doc_targets)
        else:
            await coco.mount(subpath, index_repo, repo, revision, chunking, table, symbols_table, doc_targets)


async def _mount_doc_tables(settings: Settings) -> DocTargets:
    async def mount(cls: type, name: str, pk: list[str]) -> Any:  # tipo da linha varia por tabela
        return await postgres.mount_table_target(
            PG_DB,
            table_name=name,
            table_schema=await postgres.TableSchema.from_class(cls, primary_key=pk),
            pg_schema_name=settings.pg_schema,
        )

    pages = await mount(DocPage, settings.doc_pages_table, ["repo", "path"])
    aliases = await mount(RepoAlias, settings.aliases_table, ["repo", "alias", "origin", "path"])
    mentions = await mount(DocMention, settings.mentions_table, ["doc_repo", "doc_path", "term", "origin"])
    skipped = await mount(SkippedFile, settings.skipped_table, ["repo", "path"])
    aliases.declare_sql_command_attachment(
        name="alias_index",
        setup_sql=f"CREATE INDEX IF NOT EXISTS repo_aliases_alias ON {settings.qualified(settings.aliases_table)} (alias)",
        teardown_sql=f'DROP INDEX IF EXISTS "{settings.pg_schema}".repo_aliases_alias',
    )
    mentions.declare_sql_command_attachment(
        name="term_index",
        setup_sql=f"CREATE INDEX IF NOT EXISTS doc_mentions_term ON {settings.qualified(settings.mentions_table)} (term)",
        teardown_sql=f'DROP INDEX IF EXISTS "{settings.pg_schema}".doc_mentions_term',
    )
    return DocTargets(pages=pages, aliases=aliases, mentions=mentions, skipped=skipped)


def build_app(plan: list[tuple[Repo, str]]) -> coco.App:
    return coco.App(coco.AppConfig(name="CodeIndex"), app_main, repos=plan)
