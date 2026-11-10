"""Modelos do domínio. Puro: sem I/O, sem dependência de infra ou settings."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .file_patterns import DEFAULT_EXCLUDE, DEFAULT_INCLUDE

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError(f"nome de repositório inválido: {name!r}")
    return name


@dataclass(frozen=True)
class DocLinkRule:
    """Vínculo explícito: páginas cujo caminho casa `path` (glob) ficam ligadas aos repos que casam `repos` (globs)."""

    path: str
    repos: tuple[str, ...]


@dataclass(frozen=True)
class DocSource:
    """Fonte de documentação externa. `type` escolhe o plugin (sources/registry.py: wikijs, openapi_s3...).

    As páginas viram arquivos .md em <repos_dir>/docs__<name>/ e são indexadas como um "repo" de
    kind="docs" pelo mesmo pipeline do código. Só páginas novas/alteradas são baixadas.
    """

    name: str
    url: str                              # base da wiki, ex.: https://wiki.exemplo.com
    type: str = "wikijs"
    locale: str = "pt-br"
    include: tuple[str, ...] = ()         # globs de caminho de página (vazio = todas)
    exclude: tuple[str, ...] = ()
    links: tuple[DocLinkRule, ...] = ()

    def __post_init__(self) -> None:
        validate_name(self.name)
        if not self.type or not self.type.replace("_", "").isalnum():
            raise ValueError(f"fonte {self.name!r}: tipo inválido {self.type!r}")

    @property
    def repo_name(self) -> str:
        return f"docs__{self.name}"

    def page_url(self, locale: str, path: str) -> str:
        return f"{self.url.rstrip('/')}/{locale}/{path}"

    def to_repo(self) -> Repo:
        return Repo(name=self.repo_name, url=self.url, kind="docs", include=("**/*.md",),
                    exclude=("**/.*",), doc_source=self)


@dataclass(frozen=True)
class Repo:
    """Um repositório a indexar (o que o pipeline recebe).

    `url`: remoto git, clonado em <repos_dir>/<name>. `path`: diretório local indexado
    diretamente (sem git sync). Exatamente um dos dois.
    Os campos fazem parte da chave de memo do CocoIndex: mudar filtros revarre o repo.
    """

    name: str
    url: str | None = None
    path: str | None = None
    branch: str | None = None
    include: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_INCLUDE))
    exclude: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_EXCLUDE))
    extra_exclude: tuple[str, ...] = ()  # somados a `exclude`
    kind: str = "code"                   # "code" (git/pasta) | "docs" (páginas de uma DocSource)
    doc_source: DocSource | None = None  # só para kind="docs"

    def __post_init__(self) -> None:
        validate_name(self.name)
        if self.kind not in ("code", "docs"):
            raise ValueError(f"repo {self.name!r}: kind inválido {self.kind!r}")
        if (self.url is None) == (self.path is None):
            raise ValueError(f"repo {self.name!r}: informe exatamente um de 'url' ou 'path'")

    @property
    def all_excludes(self) -> list[str]:
        return list(self.exclude) + list(self.extra_exclude)

    def local_path(self, repos_dir: Path) -> Path:
        if self.path is not None:
            return Path(self.path).expanduser().resolve()
        return repos_dir / self.name


@dataclass(frozen=True)
class Org:
    """Org/usuário do GitHub cujos repos são descobertos pela API e indexados em lotes."""

    name: str
    match: tuple[str, ...] = ()          # globs de nome de repo a incluir (vazio = todos)
    skip: tuple[str, ...] = ()           # globs de nome de repo a ignorar
    languages: tuple[str, ...] = ()      # linguagem principal (GitHub) aceita, em ordem de prioridade
    include_archived: bool = False
    include_forks: bool = False
    max_size_mb: int | None = None
    include: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_INCLUDE))
    exclude: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_EXCLUDE))
    extra_exclude: tuple[str, ...] = ()

    def repo_name(self, gh_repo: str) -> str:
        return f"{self.name}__{gh_repo}"

    def language_rank(self, language: str | None) -> int:
        """Posição da linguagem em `languages` (menor = indexa antes)."""
        langs = [x.lower() for x in self.languages]
        lang = (language or "none").lower()
        return langs.index(lang) if lang in langs else len(langs)

    def to_repo(self, name: str, url: str) -> Repo:
        return Repo(name=name, url=url, include=self.include, exclude=self.exclude,
                    extra_exclude=self.extra_exclude)


@dataclass(frozen=True)
class DiscoveredRepo:
    """Repo encontrado na API de um provedor (ex.: GitHub), já filtrado pela Org."""

    name: str               # nome no índice: <org>__<repo>
    url: str                # clone_url https
    default_branch: str
    pushed_at: datetime | None
    size_kb: int
    language: str | None


@dataclass
class RepoState:
    """Linha da tabela de estado. "Indexado" = `revision` não nula (chave de memo do pipeline).
    Um erro de sync fica em `error` sem apagar o que já foi indexado."""

    name: str
    org: str | None
    url: str | None
    path: str | None
    default_branch: str | None
    pushed_at: datetime | None
    size_kb: int | None
    ignored: bool
    indexed_commit: str | None
    revision: str | None
    indexed_pushed_at: datetime | None
    indexed_at: datetime | None
    error: str | None
    updated_at: datetime
    language: str | None = None
    kind: str = "code"

    @property
    def indexed(self) -> bool:
        return self.revision is not None

    @property
    def status(self) -> str:
        if self.ignored:
            return "ignored"
        if self.error:
            return "error"
        return "indexed" if self.indexed else "pending"

    @property
    def changed_upstream(self) -> bool:
        """Repo de org: houve push depois da última indexação?"""
        return (self.indexed and self.pushed_at is not None
                and (self.indexed_pushed_at is None or self.pushed_at > self.indexed_pushed_at))


@dataclass
class RepoStatus:
    """Visão de status para CLI/MCP (estado + contagens do índice)."""

    name: str
    org: str | None
    status: str
    language: str | None
    source: str | None
    commit: str | None
    indexed_at: str | None
    pushed_at: str | None
    outdated: bool
    files: int
    chunks: int
    error: str | None
    size_kb: int | None = None   # tamanho do repositório no GitHub (inclui histórico)
    kind: str = "code"
    skipped: int = 0             # arquivos descartados na indexação (tamanho, binário...)
    summary: str | None = None   # resumo do cartão do repo (code-index enrich), se houver
    tags: list[str] = field(default_factory=list)  # domain_tags do cartão
    card_stale: bool = False     # o repo mudou desde que o cartão foi escrito
    owners: list[str] = field(default_factory=list)  # domínio/serviço do DDD ('platform/audit')
    owner_origin: str | None = None                  # de onde veio o dono (manual, declared, sibling...)
    owner_confidence: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Hit:
    """Um trecho de código retornado pela busca."""

    repo: str
    path: str
    language: str
    start_line: int
    end_line: int
    score: float
    code: str
    kind: str = "code"                      # "code" | "doc" (Markdown/RST no repo ou página de wiki)
    url: str | None = None                  # página original (documentação externa)
    title: str | None = None
    linked_repos: list[str] = field(default_factory=list)  # repos ligados à página
    also_in: list[str] = field(default_factory=list)       # outros repos com este mesmo trecho (boilerplate)
    implemented_in: list[str] = field(default_factory=list)  # página de API: handler da operação (repo/path:linha)
    owners: list[str] = field(default_factory=list)          # domínio/serviço do DDD dono do arquivo
    summary: str | None = None              # resumo do arquivo escrito por agent (fila file_summaries), se houver
    summary_keywords: list[str] = field(default_factory=list)  # palavras-chave desse resumo

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SyncResult:
    selected: list[str] = field(default_factory=list)
    indexed: dict[str, str | None] = field(default_factory=dict)   # nome -> commit
    errors: dict[str, str] = field(default_factory=dict)
    discovered: dict[str, dict] = field(default_factory=dict)      # org -> {new, removed, total}
    remaining: int = 0                                              # candidatos para os próximos lotes

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SymbolHit:
    """Declaração encontrada por find_symbol."""

    repo: str
    path: str
    line: int
    name: str
    kind: str          # container | member | section
    container: str
    signature: str
    language: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GrepMatch:
    """Linha encontrada pelo grep (texto com segredos mascarados)."""

    repo: str
    path: str
    line: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)
