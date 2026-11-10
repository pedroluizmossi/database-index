"""Configuração da aplicação (variáveis de ambiente e .env), via pydantic-settings.

Uso:
    settings = get_settings()      # objeto imutável, cacheado
    bootstrap(settings)            # efeitos colaterais de runtime; chamar UMA vez na borda (CLI/MCP)

Caminhos relativos são resolvidos a partir de `base_dir` (pasta do .env, ou o diretório atual).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from dotenv import find_dotenv, load_dotenv
from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_ENV_FILE = find_dotenv(usecwd=True) or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore", frozen=True, populate_by_name=True)

    # Pasta base para caminhos relativos
    base_dir: Path = Field(
        default_factory=lambda: Path(_ENV_FILE).parent if _ENV_FILE else Path.cwd(),
        validation_alias="CODE_INDEX_HOME",
    )

    # Postgres
    database_url: str = Field(
        "postgres://cocoindex:cocoindex@localhost:5432/cocoindex", validation_alias="POSTGRES_URL"
    )
    pg_schema: str = "code_index"
    chunks_table: str = "code_chunks"
    symbols_table: str = "code_symbols"
    doc_pages_table: str = "doc_pages"
    aliases_table: str = "repo_aliases"
    mentions_table: str = "doc_mentions"
    skipped_table: str = "skipped_files"

    # Embedding / rerank (nomes no formato LiteLLM)
    embed_model: str = Field("voyage/voyage-code-4", validation_alias="EMBED_MODEL")
    embed_dimensions: int | None = Field(None, validation_alias="EMBED_DIMENSIONS")
    embed_max_inflight: int = Field(4, validation_alias="EMBED_MAX_INFLIGHT")
    rerank_model: str = Field("voyage/rerank-2.5", validation_alias="RERANK_MODEL")  # vazio desliga
    rerank_candidates: int = Field(40, validation_alias="RERANK_CANDIDATES")
    # Busca com filtro (repos, domínio, tipo, linguagem, caminho) sobre até N trechos: vetor exato em vez do
    # HNSW (que perde resultados quando o filtro é seletivo). Acima disso, HNSW com varredura iterativa.
    search_exact_max_chunks: int = Field(60_000, ge=0, validation_alias="SEARCH_EXACT_MAX_CHUNKS")
    # Vizinhos que o HNSW examina por busca. O padrão do pgvector (40) é menor que o nº de candidatos pedidos e
    # perdia vizinhos: em ~90 mil trechos, recall@10 de 0,88 (pior caso 0,1) com 40 e de 1,00 com 400, em ~7 ms.
    search_hnsw_ef_search: int = Field(400, ge=40, le=1000, validation_alias="SEARCH_HNSW_EF_SEARCH")
    # Variações do rerank (medidas com `code-index eval --rerank-variants`). A instrução foi a única com
    # ganho claro (MRR 0,76 -> 0,88 em evals/queries.json com rerank-3); cabeçalho e "só código", marginais.
    rerank_instruction: bool = Field(True, validation_alias="RERANK_INSTRUCTION")   # pede código na consulta
    rerank_header: bool = Field(False, validation_alias="RERANK_HEADER")            # classe/declarações no trecho
    rerank_code_only: bool = Field(False, validation_alias="RERANK_CODE_ONLY")      # docs ficam na ordem híbrida
    # Nome exato (HmacServiceImpl, get_user) já é resolvido pelos termos; o reranker só atrapalhava
    rerank_skip_identifiers: bool = Field(True, validation_alias="RERANK_SKIP_IDENTIFIERS")

    # GitHub
    github_token: str | None = Field(None, validation_alias=AliasChoices("GITHUB_TOKEN", "GH_TOKEN"))

    # Wiki.js (fontes [[docs]]): API key de leitura, criada em Administração → API Access
    wikijs_token: SecretStr | None = Field(None, validation_alias="WIKIJS_TOKEN")
    openapi_bucket_url: str = Field("https://s3.amazonaws.com/br-com-senior-ecossistema-api", validation_alias="OPENAPI_BUCKET_URL")
    openapi_ref_max_documents: int = Field(32, ge=0, le=256, validation_alias="OPENAPI_REF_MAX_DOCUMENTS")
    openapi_ref_max_bytes: int = Field(4 * 1024 * 1024, ge=1024, validation_alias="OPENAPI_REF_MAX_BYTES")
    # Página que cita mais repos que isto (índice, lista de times...) não liga por simples menção
    doc_mention_max_repos: int = Field(15, validation_alias="DOC_MENTION_MAX_REPOS")

    # Enriquecimento offline (`code-index enrich`): minutos de reserva de uma tarefa por um agent
    enrich_lease_minutes: int = Field(20, ge=1, le=24 * 60, validation_alias="ENRICH_LEASE_MINUTES")

    # Arquivos locais (relativos a `base_dir`)
    repos_file: Path = Field(Path("repos.toml"), validation_alias="CODE_INDEX_REPOS")
    repos_dir: Path = Field(Path(".repos"), validation_alias="CODE_INDEX_REPOS_DIR")
    cocoindex_db: Path = Field(Path(".cocoindex"), validation_alias="COCOINDEX_DB")
    lock_file: Path = Path(".code-index.lock")

    # Indexação
    default_batch: int = Field(10, validation_alias="CODE_INDEX_BATCH")
    max_file_bytes: int = 512 * 1024

    # Servidor HTTP (API REST + MCP em /mcp)
    http_host: str = Field("127.0.0.1", validation_alias="CODE_INDEX_HTTP_HOST")
    http_port: int = Field(8765, validation_alias="CODE_INDEX_HTTP_PORT")
    # URL pública (atrás do proxy/TLS). Define o redirect do OAuth, o cookie Secure e a origem aceita.
    public_url: str = Field("http://127.0.0.1:8765", validation_alias="CODE_INDEX_PUBLIC_URL")
    # Hosts extras aceitos no header Host (DNS rebinding); o host do public_url sempre entra.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list, validation_alias="CODE_INDEX_ALLOWED_HOSTS")

    # Autenticação
    # Segredo do servidor: pepper dos hashes de token e chave do cookie de sessão. Obrigatório no `serve`.
    auth_secret: SecretStr | None = Field(None, validation_alias="CODE_INDEX_AUTH_SECRET")
    google_client_id: str | None = Field(None, validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr | None = Field(None, validation_alias="GOOGLE_CLIENT_SECRET")
    # Quem pode entrar: e-mails listados (contas pessoais) e/ou um domínio Google Workspace (claim `hd`).
    # Admins sempre podem. Sem nenhum dos dois configurado, ninguém entra.
    google_allowed_emails: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="GOOGLE_ALLOWED_EMAILS"
    )
    google_allowed_domain: str | None = Field(None, validation_alias="GOOGLE_ALLOWED_DOMAIN")
    # Login Microsoft (Entra ID), ao lado do Google. Um único tenant (GUID); contas convidadas não entram.
    azure_tenant_id: str | None = Field(None, validation_alias="AZURE_TENANT_ID")
    azure_client_id: str | None = Field(None, validation_alias="AZURE_CLIENT_ID")
    azure_client_secret: SecretStr | None = Field(None, validation_alias="AZURE_CLIENT_SECRET")
    azure_allowed_emails: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="AZURE_ALLOWED_EMAILS"
    )
    azure_allowed_domain: str | None = Field(None, validation_alias="AZURE_ALLOWED_DOMAIN")
    # OAuth do MCP (o servidor emite tokens para clientes MCP após o login no navegador)
    oauth_access_ttl_hours: int = Field(8, validation_alias="CODE_INDEX_OAUTH_ACCESS_TTL_HOURS")
    oauth_refresh_ttl_days: int = Field(30, validation_alias="CODE_INDEX_OAUTH_REFRESH_TTL_DAYS")
    # redirect_uri aceitos no registro de clientes OAuth, além de http://localhost/127.0.0.1 (sempre aceitos).
    # Prefixos exatos, ex.: https://claude.ai/api/mcp/auth_callback
    oauth_redirect_uris: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CODE_INDEX_OAUTH_REDIRECT_URIS"
    )
    admin_emails:Annotated[list[str], NoDecode] = Field(default_factory=list, validation_alias="CODE_INDEX_ADMIN_EMAILS")
    session_ttl_hours: int = Field(12, validation_alias="CODE_INDEX_SESSION_TTL_HOURS")
    token_ttl_days: int = Field(90, validation_alias="CODE_INDEX_TOKEN_TTL_DAYS")

    # Uso da aplicação (usage_events): um evento por chamada de tool MCP ou rota da API
    usage_tracking: bool = Field(True, validation_alias="CODE_INDEX_USAGE_TRACKING")
    usage_retention_days: int = Field(365, validation_alias="CODE_INDEX_USAGE_RETENTION_DAYS")

    # Rate limit por principal (requisições por minuto); sync/reindex tem limite próprio, por hora
    rate_limit_read_per_min: int = Field(120, validation_alias="CODE_INDEX_RATE_READ_PER_MIN")
    rate_limit_search_per_min: int = Field(30, validation_alias="CODE_INDEX_RATE_SEARCH_PER_MIN")
    rate_limit_sync_per_hour: int = Field(6, validation_alias="CODE_INDEX_RATE_SYNC_PER_HOUR")

    @field_validator("admin_emails", "allowed_hosts", "google_allowed_emails", "azure_allowed_emails",
                     "oauth_redirect_uris", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Aceita lista JSON ou texto separado por vírgula (ex.: `a@x.com,b@x.com`)."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        base = self.base_dir.expanduser().resolve()
        object.__setattr__(self, "base_dir", base)
        for name in ("repos_file", "repos_dir", "cocoindex_db", "lock_file"):
            p = getattr(self, name).expanduser()
            object.__setattr__(self, name, p if p.is_absolute() else (base / p).resolve())
        return self

    @property
    def chunks_table_qualified(self) -> str:
        return f'"{self.pg_schema}"."{self.chunks_table}"'

    @property
    def symbols_table_qualified(self) -> str:
        return f'"{self.pg_schema}"."{self.symbols_table}"'

    def qualified(self, table: str) -> str:
        return f'"{self.pg_schema}"."{table}"'

    @property
    def admin_set(self) -> frozenset[str]:
        return frozenset(e.lower() for e in self.admin_emails)

    @property
    def login_emails(self) -> frozenset[str]:
        """E-mails autorizados a logar pelo Google explicitamente (lista + admins)."""
        return frozenset(e.lower() for e in [*self.google_allowed_emails, *self.admin_emails])

    @property
    def azure_login_emails(self) -> frozenset[str]:
        """E-mails autorizados a logar pela Microsoft explicitamente (lista + admins)."""
        return frozenset(e.lower() for e in [*self.azure_allowed_emails, *self.admin_emails])

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    def require_auth_secret(self) -> str:
        if self.auth_secret is None or len(self.auth_secret.get_secret_value()) < 32:
            raise RuntimeError(
                "defina CODE_INDEX_AUTH_SECRET (>= 32 caracteres) no .env; "
                "gere um com: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        return self.auth_secret.get_secret_value()

    @property
    def embed_kwargs(self) -> dict:
        """Parâmetros extras repassados ao LiteLLM em toda chamada de embedding."""
        return {"dimensions": self.embed_dimensions} if self.embed_dimensions else {}


_current: Settings | None = None


def get_settings() -> Settings:
    """Settings do processo (carregado do ambiente na primeira chamada)."""
    global _current
    if _current is None:
        _current = Settings()
    return _current


def use_settings(settings: Settings) -> Settings:
    """Substitui o Settings do processo (testes). Chamar antes de iniciar o CocoIndex."""
    global _current
    _current = settings
    return settings


def bootstrap(settings: Settings | None = None) -> Settings:
    """Efeitos de runtime que bibliotecas externas exigem via os.environ.

    - exporta o .env para os.environ (o LiteLLM lê VOYAGE_API_KEY etc. de lá);
    - aponta o estado do CocoIndex (LMDB) para `cocoindex_db`.
    Chamar uma vez no início de cada processo (CLI, MCP, testes de integração).
    """
    settings = settings or get_settings()
    if _ENV_FILE:
        load_dotenv(_ENV_FILE, override=False)
    os.environ["COCOINDEX_DB"] = str(settings.cocoindex_db)
    return settings
