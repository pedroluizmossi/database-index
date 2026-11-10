"""Fachada de casos de uso: ÚNICO ponto que a CLI e o servidor MCP usam.

Toda regra de negócio de orgs e repositórios (validações, filtros, montagem do status com
contagens) mora aqui. As interfaces só fazem parsing e formatação.

Uso:
    async with open_service() as svc:
        await svc.sync(["org/repo"])
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..indexing.orchestrator import Indexer
from ..infra import llm
from ..infra.auth_store import AuditStore, TokenStore
from ..infra.chunk_store import ChunkStore
from ..infra.config_file import ConfigFile
from ..infra.db import open_pool
from ..infra.document_store import DocumentStore
from ..infra.enrich_store import EnrichStore
from ..infra.relation_store import RelationStore
from ..infra.git import GitClient, resolve_github_token
from ..infra.github import GitHubClient
from ..infra.grep import Grep
from ..infra.job_store import JobStore
from ..infra.oauth_store import OAuthStore
from ..infra.repo_store import RepoStore
from ..infra.symbol_store import SymbolStore
from ..infra.usage_store import UsageStore
from ..infra.user_store import UserStore
from ..retrieval.search import Searcher
from ..settings import Settings, get_settings
from .apis import ApisMixin
from .auth import AuthMixin
from .base import ServiceBase
from .docs import DocsMixin
from .enrich import EnrichMixin
from .oauth import OAuthMixin
from .ownership import OwnershipMixin
from .repos import ReposMixin
from .search import SearchMixin
from .sync import SyncMixin
from .usage import UsageMixin
from .users import UsersMixin
from .wiki import WikiMixin

log = logging.getLogger(__name__)


class CodeIndexService(SyncMixin, ReposMixin, DocsMixin, WikiMixin, SearchMixin, ApisMixin, EnrichMixin, AuthMixin,
                       OAuthMixin, OwnershipMixin, UsersMixin, UsageMixin, ServiceBase):
    """Casos de uso do índice. Construído por `open_service()`, que cuida do ciclo de vida.
    Cada área fica num módulo deste pacote; as interfaces só conhecem esta classe."""


@asynccontextmanager
async def open_service(settings: Settings | None = None) -> AsyncIterator[CodeIndexService]:
    """Abre o pool do Postgres, monta os componentes e fecha tudo na saída (inclusive clientes LiteLLM)."""
    settings = settings if settings is not None else get_settings()
    try:
        token = resolve_github_token(settings)
        async with open_pool(settings) as pool:
            jobs = JobStore(pool, settings)
            repos = RepoStore(pool, settings)
            documents = DocumentStore(pool, settings)
            relations = RelationStore(pool, settings)
            chunks = ChunkStore(pool, settings, documents=documents, relations=relations)
            config = ConfigFile(settings.repos_file)
            git = GitClient(token, settings.repos_dir)
            indexer = Indexer(
                settings,
                repos=repos,
                config=config,
                git=git,
                github=GitHubClient(token),
            )
            symbols = SymbolStore(pool, settings)
            searcher = Searcher(settings, chunks=chunks, symbols=symbols)
            svc = CodeIndexService(
                settings,
                repos=repos,
                chunks=chunks,
                config=config,
                git=git,
                indexer=indexer,
                searcher=searcher,
                tokens=TokenStore(pool, settings),
                audit=AuditStore(pool, settings),
                jobs=jobs,
                symbols=symbols,
                grep=Grep(),
                enrich=EnrichStore(pool, settings),
                documents=documents,
                relations=relations,
                oauth=OAuthStore(pool, settings),
                users=UserStore(pool, settings),
                usage=UsageStore(pool, settings),
            )
            await svc._ensure_search_indexes()
            await svc.ensure_relations()
            try:
                yield svc
            finally:
                await svc.shutdown()  # antes de fechar o pool
    finally:
        await llm.close_clients()
