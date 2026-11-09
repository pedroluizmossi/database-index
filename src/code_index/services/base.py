"""Base dos casos de uso: dependências injetadas e utilitários comuns a todas as áreas."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.auth import Principal
from ..domain.models import DocSource, RepoState, RepoStatus, validate_name
from ..domain.policies import resolve_name
from ..domain.repo_card import FACTS_VERSION
from ..indexing.linkers import rebuild_relations
from ..indexing.orchestrator import Indexer, configured_repos
from ..infra.auth_store import AuditStore, TokenStore
from ..infra.chunk_store import ChunkStore
from ..infra.config_file import ConfigFile
from ..infra.document_store import DocumentStore
from ..infra.enrich_store import EnrichStore
from ..infra.git import GitClient
from ..infra.grep import Grep
from ..infra.job_store import JobStore
from ..infra.oauth_store import OAuthStore
from ..infra.relation_store import RelationStore
from ..infra.repo_store import RepoStore
from ..infra.symbol_store import SymbolStore
from ..infra.user_store import UserStore
from ..retrieval.search import Searcher
from ..settings import Settings

log = logging.getLogger(__name__)


def _iso_local(dt: datetime | None) -> str | None:
    """Datetime aware -> ISO 8601 em hora local, sem frações de segundo."""
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


class ServiceBase:
    """Dependências compartilhadas e utilitários dos casos de uso (ver services/__init__.py)."""

    def __init__(
        self,
        settings: Settings,
        *,
        repos: RepoStore,
        chunks: ChunkStore,
        config: ConfigFile,
        git: GitClient,
        indexer: Indexer,
        searcher: Searcher,
        tokens: TokenStore,
        audit: AuditStore,
        jobs: JobStore,
        symbols: SymbolStore,
        grep: Grep,
        enrich: EnrichStore,
        documents: DocumentStore,
        relations: RelationStore,
        oauth: OAuthStore,
        users: UserStore,
    ) -> None:
        self.settings = settings
        self._repos = repos
        self._chunks = chunks
        self._config = config
        self._git = git
        self._indexer = indexer
        self._searcher = searcher
        self._tokens = tokens
        self._audit = audit
        self._jobs = jobs
        self._symbols = symbols
        self._grep = grep
        self._enrich = enrich
        self._documents = documents
        self._relations = relations
        self._oauth = oauth
        self._users = users
        self._job_lock = asyncio.Lock()  # checagem + criação de job, sem corrida entre requisições
        self._job_tasks: set[asyncio.Task[None]] = set()
        self._owner_cache: Any = None  # dono no DDD resolvido (services/ownership.py); limpo ao recalcular relações

    # ------------------------------------------------------------ repos e fontes

    def _local_path(self, repo: str) -> Path:
        try:
            validate_name(repo)
        except ValueError:
            raise ValueError(f"repositório desconhecido: {repo}") from None
        configured = configured_repos(self._config).get(repo)
        if configured is not None:
            return configured.local_path(self.settings.repos_dir)
        return self.settings.repos_dir / repo

    async def _resolve_repos(self, repos: Sequence[str] | None) -> list[str] | None:
        """Aceita 'org__repo', 'org/repo' ou só 'repo'. Nome desconhecido → ValueError."""
        if not repos:
            return None
        known = (await self._repos.all_rows()).keys()
        out = []
        for name in repos:
            resolved = resolve_name(name, known)
            if resolved is None:
                raise ValueError(f"repositório desconhecido: {name}")
            out.append(resolved)
        return out

    @staticmethod
    def _code_repo_names(rows: dict[str, RepoState]) -> list[str]:
        return [r.name for r in rows.values() if r.kind == "code" and not r.ignored]

    def _doc_source(self, name: str) -> DocSource:
        source = next((d for d in self._config.load_docs() if d.name == name or d.repo_name == name), None)
        if source is None:
            raise ValueError(f"fonte de documentação não encontrada: {name}")
        return source

    async def _ensure_search_indexes(self) -> None:
        """BM25: cria o índice se o banco tiver pg_search (o CocoIndex pode recriar a tabela num sync)."""
        try:
            await self._chunks.ensure_bm25_index()
        except Exception as e:  # noqa: BLE001 - busca segue funcionando sem BM25 (cai no full-text)
            log.warning("não foi possível garantir o índice BM25: %s", e)

    # ------------------------------------------------------------ relações

    async def rebuild_relations(self, only: Sequence[str] | None = None) -> dict[str, Any]:
        """Recalcula o catálogo de documentos e as relações derivadas (sem LLM e sem Voyage).
        `only`: nomes de linkers a rodar (sem recalcular o catálogo de documentos)."""
        self._owner_cache = None
        return await rebuild_relations(self._chunks.pool, self.settings, self._config.load_docs(), only=only)

    async def ensure_relations(self) -> None:
        """Primeiro uso (banco novo ou recém-migrado): calcula documentos e relações derivadas se ainda não
        existem e já há o que ligar. Barato quando já calculado (uma consulta)."""
        sources = self._config.load_docs()
        if await self._relations.has_derived() and not (sources and not await self._documents.has_present()):
            return
        if not sources and not await self._enrich.has_cards():
            return
        log.info("calculando relações pela primeira vez")
        await self.rebuild_relations()

    async def relation_stats(self) -> dict[str, Any]:
        return await self._relations.stats()

    async def related(self, entity: str, *, type_: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Tudo ligado a uma entidade. Aceita chave ('repo:x', 'doc:fonte/caminho', 'op:dom/serv/op',
        'code:repo/caminho:linha', 'dir:repo/pasta', 'svc:dominio/servico', 'dom:dominio') ou só o nome de um repo."""
        from ..domain.relations import RELATION_TYPES, key_kind, repo_key

        raw = entity.strip()
        if not raw:
            raise ValueError("informe a entidade (nome do repo ou chave como 'op:platform/email/sendEmail')")
        if type_ is not None and type_ not in RELATION_TYPES:
            raise ValueError(f"tipo inválido: {type_} (use {', '.join(RELATION_TYPES)})")
        if ":" in raw and key_kind(raw) in ("repo", "doc", "op", "code", "dir", "svc", "dom"):
            key = raw.lower() if key_kind(raw) in ("svc", "dom") else raw
            if key_kind(raw) == "repo":
                key = repo_key((await self._resolve_repos([raw.split(":", 1)[1]]) or [""])[0])
        else:
            key = repo_key((await self._resolve_repos([raw]) or [""])[0])
        return {"entity": key, "relations": await self._relations.neighbors(key, type_=type_, limit=limit)}

    # ------------------------------------------------------------ ciclo de vida e auditoria

    async def shutdown(self) -> None:
        """Encerra os jobs em andamento (marca como interrompidos). Chamado antes de fechar o pool."""
        tasks = list(self._job_tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def audit(
        self,
        principal: Principal | None,
        action: str,
        *,
        params: dict[str, Any] | None = None,
        status: int | None = None,
        client_ip: str | None = None,
    ) -> None:
        await self._audit.record(
            actor=principal.actor if principal else "anonymous",
            token_id=principal.token_id if principal else None,
            action=action, params=params, status=status, client_ip=client_ip,
        )


def _facts_key(row: RepoState) -> str:
    """Chave de memo dos fatos: revisão do repo + versão da coleta."""
    return f"{row.revision}|f{FACTS_VERSION}"


def _to_status(r: RepoState, n_files: int, n_chunks: int, n_skipped: int = 0) -> RepoStatus:
    return RepoStatus(
        name=r.name,
        org=r.org,
        status=r.status,
        language=r.language,
        source=r.url or r.path,
        commit=r.indexed_commit[:7] if r.indexed_commit else None,
        indexed_at=_iso_local(r.indexed_at),
        pushed_at=_iso_local(r.pushed_at),
        outdated=r.changed_upstream,
        files=n_files,
        chunks=n_chunks,
        error=r.error,
        size_kb=r.size_kb,
        kind=r.kind,
        skipped=n_skipped,
    )
