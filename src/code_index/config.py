"""Configuração: variáveis de ambiente e repos.toml ([[repo]] manuais e [[org]] do GitHub)."""

from __future__ import annotations

import os
import pathlib
import re
import tomllib
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

_dotenv = find_dotenv(usecwd=True)
load_dotenv(_dotenv)
# Caminhos relativos são resolvidos a partir da pasta do .env (ou do diretório atual).
HOME = pathlib.Path(os.getenv("CODE_INDEX_HOME") or (pathlib.Path(_dotenv).parent if _dotenv else pathlib.Path.cwd()))


def _path(env: str, default: str) -> pathlib.Path:
    return (HOME / os.getenv(env, default)).resolve()


DATABASE_URL = os.getenv(
    "POSTGRES_URL", "postgres://cocoindex:cocoindex@localhost:5432/cocoindex"
)
EMBED_MODEL = os.getenv("EMBED_MODEL", "voyage/voyage-code-4")
EMBED_DIMENSIONS = int(os.environ["EMBED_DIMENSIONS"]) if os.getenv("EMBED_DIMENSIONS") else None

# Reranker: a busca híbrida traz RERANK_CANDIDATES trechos e o modelo devolve os top-k.
# RERANK_MODEL vazio desliga o rerank.
RERANK_MODEL = os.getenv("RERANK_MODEL", "voyage/rerank-2.5")
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "40"))

REPOS_FILE = _path("CODE_INDEX_REPOS", "repos.toml")
REPOS_DIR = _path("CODE_INDEX_REPOS_DIR", ".repos")
os.environ["COCOINDEX_DB"] = str(_path("COCOINDEX_DB", ".cocoindex"))

PG_SCHEMA = "code_index"
TABLE_NAME = "code_chunks"

MAX_FILE_BYTES = 512 * 1024  # arquivos maiores são ignorados (gerados/minificados)

DEFAULT_INCLUDE = [
    "**/*.py", "**/*.pyi", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.mjs",
    "**/*.java", "**/*.kt", "**/*.kts", "**/*.scala", "**/*.go", "**/*.rs",
    "**/*.c", "**/*.h", "**/*.cc", "**/*.cpp", "**/*.hpp", "**/*.cs", "**/*.fs",
    "**/*.rb", "**/*.php", "**/*.swift", "**/*.m", "**/*.dart", "**/*.lua",
    "**/*.sh", "**/*.bash", "**/*.zsh", "**/*.ps1", "**/*.sql", "**/*.r", "**/*.R",
    "**/*.vue", "**/*.svelte", "**/*.html", "**/*.css", "**/*.scss",
    "**/*.toml", "**/*.yaml", "**/*.yml", "**/*.json", "**/*.xml", "**/*.gradle",
    "**/*.md", "**/*.mdx", "**/*.rst", "**/Dockerfile", "**/Makefile",
    "**/*.sdl", "**/*.pdl", "**/*.properties",
]
DEFAULT_EXCLUDE = [
    "**/.*", "**/node_modules", "**/vendor", "**/dist", "**/build", "**/target",
    "**/__pycache__", "**/venv", "**/*.min.js", "**/*.min.css", "**/*.lock",
    "**/package-lock.json", "**/pnpm-lock.yaml", "**/*.map",
]

# Testes não ajudam a entender o código de produção e custam tokens: fora por padrão.
TEST_EXCLUDE = [
    # diretórios de teste (Maven/Gradle src/test, Python tests/, JS __tests__, e2e, fixtures...)
    "**/test", "**/tests", "**/Test", "**/Tests", "**/__tests__", "**/__test__",
    "**/e2e", "**/integration-test", "**/integrationTest",
    "**/androidTest", "**/testFixtures", "**/testdata", "**/test-data",
    "**/__mocks__", "**/__fixtures__", "**/__snapshots__", "**/cypress", "**/playwright",
    # Java / Kotlin / Scala / Groovy
    "**/*Test.java", "**/*Tests.java", "**/*IT.java", "**/*TestCase.java",
    "**/*Test.kt", "**/*Tests.kt", "**/*Test.scala", "**/*Spec.scala", "**/*Spec.groovy", "**/*Test.groovy",
    # JS / TS
    "**/*.test.js", "**/*.test.jsx", "**/*.test.ts", "**/*.test.tsx", "**/*.test.mjs",
    "**/*.spec.js", "**/*.spec.jsx", "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.mjs",
    "**/*.cy.js", "**/*.cy.ts", "**/karma.conf.js", "**/jest.config.*", "**/vitest.config.*",
    # Python
    "**/test_*.py", "**/*_test.py", "**/conftest.py",
    # Go / Rust / Dart / Elixir / C++
    "**/*_test.go", "**/*_test.rs", "**/*_test.dart", "**/*_test.exs", "**/*_test.cc", "**/*_test.cpp",
    # C# / .NET
    "**/*Test.cs", "**/*Tests.cs", "**/*.Tests", "**/*.UnitTests", "**/*.IntegrationTests",
    # Ruby / PHP / Swift
    "**/*_spec.rb", "**/*_test.rb", "**/*Test.php", "**/*Tests.swift", "**/*Test.swift",
]
# Ruído: histórico de versões, especificações OpenAPI (indexadas por outra fonte) e
# migrations duplicadas por banco (mantemos só PostgreSql).
_DB_VENDORS = ["H2", "h2", "Oracle", "oracle", "SqlServer", "SQLServer", "sqlserver", "MySql", "MySQL", "mysql"]
NOISE_EXCLUDE = [
    "**/CHANGELOG*", "**/CHANGES*", "**/HISTORY*", "**/RELEASE_NOTES*", "**/RELEASE-NOTES*",
    "**/openapi.json", "**/openapi.yaml", "**/openapi.yml", "**/*.openapi.json", "**/*.openapi.yaml",
    "**/swagger.json", "**/swagger.yaml", "**/swagger.yml",
] + [f"**/{d}/**/{v}" for d in ("migration", "migrations") for v in _DB_VENDORS]
DEFAULT_EXCLUDE = DEFAULT_EXCLUDE + TEST_EXCLUDE + NOISE_EXCLUDE

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Repo:
    """Um repositório a indexar.

    `url` é um remoto git (https/ssh/GitHub) clonado em REPOS_DIR/<name>;
    `path` é um diretório local indexado diretamente (sem git sync).
    """

    name: str
    url: str | None = None
    path: str | None = None
    branch: str | None = None
    include: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_INCLUDE))
    exclude: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_EXCLUDE))
    extra_exclude: tuple[str, ...] = ()  # somados aos `exclude` (padrão)

    @property
    def all_excludes(self) -> list[str]:
        return list(self.exclude) + list(self.extra_exclude)

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"nome de repositório inválido: {self.name!r}")
        if (self.url is None) == (self.path is None):
            raise ValueError(f"repo {self.name!r}: informe exatamente um de 'url' ou 'path'")

    @property
    def local_path(self) -> pathlib.Path:
        if self.path is not None:
            return pathlib.Path(self.path).expanduser().resolve()
        return REPOS_DIR / self.name


def name_from_url(url: str) -> str:
    """'https://github.com/org/proj.git' -> 'org__proj'."""
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        return f"{m.group(1)}__{m.group(2)}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", url.rstrip("/").rsplit("/", 1)[-1])


def normalize_source(source: str) -> tuple[str | None, str | None]:
    """Aceita URL git, 'org/repo' (GitHub) ou diretório local. Retorna (url, path)."""
    p = pathlib.Path(source).expanduser()
    if p.is_dir():
        return None, str(p.resolve())
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):
        return f"https://github.com/{source}.git", None
    return source, None


@dataclass(frozen=True)
class Org:
    """Uma org/usuário do GitHub cujos repositórios são descobertos via API.

    Os repositórios descobertos ficam na tabela de estado (code_index.repos) e são
    indexados aos poucos por `code-index sync --batch N`.
    """

    name: str
    match: tuple[str, ...] = ()          # globs de nome do repo a incluir (vazio = todos)
    skip: tuple[str, ...] = ()           # globs de nome do repo a ignorar
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


def _load() -> dict:
    return tomllib.loads(REPOS_FILE.read_text()) if REPOS_FILE.exists() else {}


def _tuples(entry: dict, keys: tuple[str, ...]) -> dict:
    entry = dict(entry)
    for key in keys:
        if key in entry:
            entry[key] = tuple(entry[key])
    return entry


def load_repos() -> list[Repo]:
    repos = [Repo(**_tuples(e, ("include", "exclude", "extra_exclude"))) for e in _load().get("repo", [])]
    names = [r.name for r in repos]
    if len(names) != len(set(names)):
        raise ValueError(f"nomes duplicados em {REPOS_FILE}")
    return repos


def load_orgs() -> list[Org]:
    return [Org(**_tuples(e, ("match", "skip", "languages", "include", "exclude", "extra_exclude"))) for e in _load().get("org", [])]


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(type(v))


def _toml_table(header: str, obj: object) -> list[str]:
    lines = [f"[[{header}]]"]
    defaults = type(obj)(name=obj.name, **({"path": "x"} if isinstance(obj, Repo) else {}))
    for f in obj.__dataclass_fields__:
        v = getattr(obj, f)
        if f == "name" or (v is not None and v != getattr(defaults, f)) or (f in ("url", "path") and v is not None):
            lines.append(f"{f} = {_toml_value(v)}")
    return lines + [""]


def save_config(repos: list[Repo] | None = None, orgs: list[Org] | None = None) -> None:
    repos = load_repos() if repos is None else repos
    orgs = load_orgs() if orgs is None else orgs
    lines = [
        "# Gerenciado por `code-index repo ...` / `code-index org ...`, mas pode editar à mão.",
        "# [[repo]]: repositório avulso (url git ou path local). [[org]]: org do GitHub descoberta via API.",
        "",
    ]
    for o in orgs:
        lines += _toml_table("org", o)
    for r in repos:
        lines += _toml_table("repo", r)
    REPOS_FILE.write_text("\n".join(lines))


def save_repos(repos: list[Repo]) -> None:
    save_config(repos=repos)


def local_path_for(name: str) -> pathlib.Path:
    """Diretório de trabalho de um repo (path local de um [[repo]] ou o clone em REPOS_DIR)."""
    if not _NAME_RE.match(name):
        raise ValueError(f"nome de repositório inválido: {name!r}")
    for r in load_repos():
        if r.name == name:
            return r.local_path
    return REPOS_DIR / name
