"""Fachada de casos de uso: ÚNICO ponto que a CLI e o servidor MCP usam.

Toda regra de negócio de orgs e repositórios (validações, filtros, montagem do status com
contagens) mora aqui. As interfaces só fazem parsing e formatação.

Uso:
    async with open_service() as svc:
        await svc.sync(["org/repo"])
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .domain.auth import ADMIN, Principal, check_can_issue, scopes_for_user, validate_scopes
from .domain.models import Hit, Org, Repo, RepoState, RepoStatus, SyncResult, validate_name
from .domain.policies import name_from_path, name_from_url, normalize_source
from .indexing.orchestrator import Indexer
from .infra import llm
from .infra.auth_store import AuditStore, TokenInfo, TokenStore
from .infra.chunk_store import ChunkStore
from .infra.config_file import ConfigFile
from .infra.db import open_pool
from .infra.git import GitClient, resolve_github_token
from .infra.github import GitHubClient
from .infra.repo_store import RepoStore
from .retrieval import files as retrieval_files
from .retrieval.search import Searcher
from .settings import Settings, get_settings


def _iso_local(dt: datetime | None) -> str | None:
    """Datetime aware -> ISO 8601 em hora local, sem frações de segundo."""
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


class CodeIndexService:
    """Casos de uso do índice. Construído por `open_service()`, que cuida do ciclo de vida."""

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
    ) -> None:
        self.settings = settings
        self._tokens = tokens
        self._audit = audit
        self._repos = repos
        self._chunks = chunks
        self._config = config
        self._git = git
        self._indexer = indexer
        self._searcher = searcher

    # ------------------------------------------------------------ repos

    async def add_repo(
        self,
        source: str,
        *,
        name: str | None = None,
        branch: str | None = None,
        include: Sequence[str] | None = None,
        extra_exclude: Sequence[str] | None = None,
    ) -> Repo:
        """Adiciona um repo avulso (URL git, 'org/repo' do GitHub ou diretório local) ao repos.toml."""
        url, path = normalize_source(source)
        if url is not None:
            default_name = name_from_url(url)
        elif path is not None:
            default_name = name_from_path(path)
        else:
            raise ValueError(f"repositório desconhecido: {source}")
        name = name or default_name
        repos = self._config.load_repos()
        if any(r.name == name for r in repos):
            raise ValueError(f"já existe um repo chamado {name!r}")
        kwargs: dict[str, Any] = {}
        if include:
            kwargs["include"] = tuple(include)
        if extra_exclude:
            kwargs["extra_exclude"] = tuple(extra_exclude)
        repo = Repo(name=name, url=url, path=path, branch=branch, **kwargs)
        repos.append(repo)
        self._config.save(repos=repos)
        return repo

    async def remove_repo(self, name: str, *, keep_clone: bool = False) -> Literal["removed", "ignored"]:
        """Remove um repo avulso; um repo de org é apenas marcado como ignorado."""
        repos = self._config.load_repos()
        manual = next((r for r in repos if r.name == name), None)
        if manual is not None:
            self._config.save(repos=[r for r in repos if r.name != name])
            await self._repos.delete([name])
            if not keep_clone:
                await asyncio.to_thread(self._git.remove_clone, manual)
            return "removed"
        if await self._repos.set_ignored(name, True):
            return "ignored"
        raise ValueError(f"repo não encontrado: {name}")

    async def unignore_repo(self, name: str) -> None:
        """Devolve um repo de org ignorado à fila de indexação."""
        if not await self._repos.set_ignored(name, False):
            raise ValueError(f"repo não encontrado: {name}")

    async def list_repos(
        self,
        *,
        status: str | None = None,
        org: str | None = None,
        name: str | None = None,
    ) -> list[RepoStatus]:
        """Estado de cada repo (tabela de estado) com contagens de arquivos e chunks do índice."""
        rows = await self._repos.all_rows()
        counts = await self._chunks.counts_by_repo()
        out: list[RepoStatus] = []
        for r in sorted(rows.values(), key=lambda r: r.name):
            if (status and r.status != status) or (org and r.org != org) or (name and name not in r.name):
                continue
            n_files, n_chunks = counts.get(r.name, (0, 0))
            out.append(_to_status(r, n_files, n_chunks))
        return out

    # ------------------------------------------------------------ orgs

    async def add_org(
        self,
        name: str,
        *,
        match: Sequence[str] = (),
        skip: Sequence[str] = (),
        languages: Sequence[str] = (),
        include_archived: bool = False,
        include_forks: bool = False,
        max_size_mb: int | None = None,
        extra_exclude: Sequence[str] = (),
    ) -> dict:
        """Adiciona uma org e descobre seus repos (não indexa). Valida o acesso antes de salvar."""
        orgs = self._config.load_orgs()
        if any(o.name == name for o in orgs):
            raise ValueError(f"org já configurada: {name} (edite o repos.toml para mudar filtros)")
        org = Org(
            name=name,
            match=tuple(match),
            skip=tuple(skip),
            languages=tuple(languages),
            include_archived=include_archived,
            include_forks=include_forks,
            max_size_mb=max_size_mb,
            extra_exclude=tuple(extra_exclude),
        )
        stats = (await self._indexer.discover([org]))[org.name]  # valida acesso antes de salvar
        self._config.save(orgs=orgs + [org])
        return stats

    async def set_org(
        self,
        name: str,
        *,
        languages: Sequence[str] | None = None,
        match: Sequence[str] | None = None,
        skip: Sequence[str] | None = None,
        max_size_mb: int | None = None,
    ) -> dict:
        """Altera os filtros de uma org e redescobre seus repos.

        Lista vazia limpa o filtro; `max_size_mb=0` remove o limite.
        """
        orgs = self._config.load_orgs()
        org = next((o for o in orgs if o.name == name), None)
        if org is None:
            raise ValueError(f"org não configurada: {name}")
        changes: dict[str, Any] = {}
        for key, values in (("languages", languages), ("match", match), ("skip", skip)):
            if values is not None:
                changes[key] = tuple(x for x in values if x)
        if max_size_mb is not None:
            changes["max_size_mb"] = max_size_mb or None
        if not changes:
            raise ValueError("nada a alterar (use --languages, --match, --skip ou --max-size-mb)")
        new = replace(org, **changes)
        self._config.save(orgs=[new if o.name == name else o for o in orgs])
        return (await self._indexer.discover([new]))[new.name]

    async def remove_org(self, name: str, *, keep_clones: bool = False) -> int:
        """Remove uma org: apaga seus repos do estado (e, por padrão, os clones). Retorna quantos repos saíram."""
        orgs = self._config.load_orgs()
        if not any(o.name == name for o in orgs):
            raise ValueError(f"org não configurada: {name}")
        remaining = [o for o in orgs if o.name != name]
        self._config.save(orgs=remaining)
        removed = await self._repos.prune_orgs([o.name for o in remaining])
        if not keep_clones:
            for repo_name in removed:
                await asyncio.to_thread(self._git.remove_clone, Repo(name=repo_name, url="-"))
        return len(removed)

    async def refresh_orgs(self, name: str | None = None) -> dict[str, dict]:
        """Redescobre os repos das orgs (todas, ou só a informada)."""
        orgs = [o for o in self._config.load_orgs() if not name or o.name == name]
        if not orgs:
            raise ValueError("nenhuma org configurada" + (f" com nome {name}" if name else ""))
        return await self._indexer.discover(orgs)

    async def org_summary(self) -> list[dict]:
        """Por org: total e contagem por status, desatualizados, filtros ativos e linguagens."""
        rows = await self.list_repos()
        out: list[dict] = []
        for org in self._config.load_orgs():
            mine = [r for r in rows if r.org == org.name]
            by_status = Counter(r.status for r in mine)
            filters = {
                k: v
                for k, v in (
                    ("languages", list(org.languages)),
                    ("match", list(org.match)),
                    ("skip", list(org.skip)),
                    ("max_size_mb", org.max_size_mb),
                )
                if v
            }
            langs = Counter(r.language or "none" for r in mine)
            out.append({
                "name": org.name,
                "total": len(mine),
                "status": {s: by_status[s] for s in ("indexed", "pending", "error", "ignored")},
                "outdated": sum(r.outdated for r in mine),
                "filters": filters,
                "languages": dict(langs.most_common()),
            })
        return out

    # ------------------------------------------------------------ sync / consulta

    async def sync(
        self,
        names: list[str] | None = None,
        *,
        batch: int | None | Literal["default"] = "default",
        do_discover: bool = True,
        pull: bool = True,
        full: bool = False,
        progress: bool = True,
        dry_run: bool = False,
        allow_register: bool = True,
    ) -> SyncResult:
        """Indexa os nomes informados, ou um lote automático.

        batch: máximo de repos no lote automático; None = sem limite; "default" = settings.default_batch.
        """
        if isinstance(batch, str):
            batch = self.settings.default_batch
        return await self._indexer.sync(
            names,
            batch=batch,
            do_discover=do_discover,
            pull=pull,
            full=full,
            progress=progress,
            dry_run=dry_run,
            allow_register=allow_register,
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        repos: list[str] | None = None,
        language: str | None = None,
        path_glob: str | None = None,
        mode: str = "hybrid",
        rerank: bool = True,
        candidates: int | None = None,
    ) -> list[Hit]:
        """Busca híbrida (vetor + full-text) com rerank opcional."""
        return await self._searcher.search(
            query,
            top_k=top_k,
            repos=repos,
            language=language,
            path_glob=path_glob,
            mode=mode,
            rerank=rerank,
            candidates=candidates,
        )

    async def read_file(
        self,
        repo: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Lê um arquivo do clone local (ou do diretório de um repo avulso), com números de linha."""
        root = await asyncio.to_thread(self._local_path, repo)
        return await asyncio.to_thread(retrieval_files.read_file, root, repo, path, start_line, end_line)

    def _local_path(self, repo: str) -> Path:
        try:
            validate_name(repo)
        except ValueError:
            raise ValueError(f"repositório desconhecido: {repo}") from None
        for r in self._config.load_repos():
            if r.name == repo:
                return r.local_path(self.settings.repos_dir)
        return self.settings.repos_dir / repo


    # ------------------------------------------------------------ autenticação

    async def authenticate(self, token: str) -> Principal | None:
        """Principal de um token (agent ou sessão); None se inválido, expirado ou revogado."""
        return await self._tokens.authenticate(token)

    async def start_session(self, email: str) -> tuple[str, TokenInfo]:
        """Sessão de uma pessoa já autenticada pelo Google. Scopes vêm da lista de admins."""
        scopes = scopes_for_user(email, self.settings.admin_emails)
        return await self._tokens.create(
            kind="session", name="google-session", owner_email=email, scopes=scopes,
            ttl=timedelta(hours=self.settings.session_ttl_hours),
        )

    async def end_session(self, principal: Principal) -> None:
        if principal.kind == "session" and principal.token_id:
            await self._tokens.revoke(principal.token_id)

    async def create_agent_token(
        self,
        issuer: Principal | None,
        *,
        name: str,
        scopes: Sequence[str],
        ttl_days: int | None = None,
        owner_email: str | None = None,
    ) -> tuple[str, TokenInfo]:
        """Emite um token de agent. Retorna (valor — mostrar uma única vez, metadados).

        issuer=None: operador local (CLI no servidor), que informa `owner_email`.
        Caso contrário, só sessões Google emitem, e só com scopes que já possuem.
        """
        if not name.strip():
            raise ValueError("informe um nome para o token")
        if issuer is None:
            if not owner_email:
                raise ValueError("informe o e-mail do responsável pelo token")
            normalized = validate_scopes(scopes)
            owner = owner_email
        else:
            try:
                normalized = check_can_issue(issuer, scopes)
            except PermissionError as e:
                raise PermissionError(str(e)) from None
            owner = issuer.subject
        days = self.settings.token_ttl_days if ttl_days is None else ttl_days
        if days <= 0 or days > 365:
            raise ValueError("validade do token deve ser entre 1 e 365 dias")
        return await self._tokens.create(
            kind="agent", name=name.strip(), owner_email=owner, scopes=normalized, ttl=timedelta(days=days),
        )

    async def list_tokens(self, principal: Principal | None, *, include_inactive: bool = False) -> list[TokenInfo]:
        """Admin (ou operador local) vê todos; os demais, só os próprios."""
        owner = None if principal is None or principal.has(ADMIN) else principal.subject
        return await self._tokens.list(owner_email=owner, include_inactive=include_inactive)

    async def revoke_token(self, principal: Principal | None, token_id: str) -> None:
        info = await self._tokens.get(token_id)
        if info is None or info.kind != "agent":
            raise ValueError(f"token não encontrado: {token_id}")
        if principal is not None and not principal.has(ADMIN) and info.owner_email != principal.subject:
            raise PermissionError("só o dono do token ou um admin pode revogá-lo")
        await self._tokens.revoke(token_id)

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

    async def recent_audit(self, *, limit: int = 100, actor: str | None = None) -> list[dict[str, Any]]:
        return await self._audit.recent(limit=limit, actor=actor)


def _to_status(r: RepoState, n_files: int, n_chunks: int) -> RepoStatus:
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
    )


@asynccontextmanager
async def open_service(settings: Settings | None = None) -> AsyncIterator[CodeIndexService]:
    """Abre o pool do Postgres, monta os componentes e fecha tudo na saída (inclusive clientes LiteLLM)."""
    settings = settings if settings is not None else get_settings()
    try:
        token = resolve_github_token(settings)
        async with open_pool(settings) as pool:
            repos = RepoStore(pool, settings)
            chunks = ChunkStore(pool, settings)
            config = ConfigFile(settings.repos_file)
            git = GitClient(token, settings.repos_dir)
            indexer = Indexer(
                settings,
                repos=repos,
                config=config,
                git=git,
                github=GitHubClient(token),
            )
            searcher = Searcher(settings, chunks=chunks)
            yield CodeIndexService(
                settings,
                repos=repos,
                chunks=chunks,
                config=config,
                git=git,
                indexer=indexer,
                searcher=searcher,
                tokens=TokenStore(pool, settings),
                audit=AuditStore(pool, settings),
            )
    finally:
        await llm.close_clients()
