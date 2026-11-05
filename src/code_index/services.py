"""Fachada de casos de uso: ÚNICO ponto que a CLI e o servidor MCP usam.

Toda regra de negócio de orgs e repositórios (validações, filtros, montagem do status com
contagens) mora aqui. As interfaces só fazem parsing e formatação.

Uso:
    async with open_service() as svc:
        await svc.sync(["org/repo"])
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal


from .domain.auth import ADMIN, Principal, check_can_issue, scopes_for_user, validate_scopes
from .domain.models import DocLinkRule, DocSource, Hit, SymbolHit, Org, Repo, RepoState, RepoStatus, SyncResult, validate_name
from .domain.policies import name_from_path, name_from_url, normalize_source, resolve_name
from .indexing.content import file_language
from .indexing.orchestrator import Indexer, configured_repos, repo_for_row
from .infra import llm
from .infra.auth_store import AuditStore, TokenInfo, TokenStore
from .infra.chunk_store import ChunkStore
from .infra.config_file import ConfigFile
from .infra.db import open_pool
from .infra.job_store import JobRecord, JobStore
from .infra.git import GitClient, resolve_github_token
from .infra.github import GitHubClient
from .infra.grep import Grep, GrepResult, GrepTarget
from .infra.symbol_store import SymbolStore
from .infra.repo_store import RepoStore
from .retrieval import files as retrieval_files
from .retrieval.evaluation import CaseResult, ConfigReport, EvalCase, first_hit, load_cases
from .retrieval.redaction import mask_output
from .retrieval.search import RerankOptions, Searcher
from .settings import Settings, get_settings

log = logging.getLogger(__name__)


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
        jobs: JobStore,
        symbols: SymbolStore,
        grep: Grep,
    ) -> None:
        self.settings = settings
        self._tokens = tokens
        self._audit = audit
        self._jobs = jobs
        self._symbols = symbols
        self._grep = grep
        self._job_lock = asyncio.Lock()  # checagem + criação de job, sem corrida entre requisições
        self._job_tasks: set[asyncio.Task[None]] = set()
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
        if any(d.repo_name == name for d in self._config.load_docs()):
            raise ValueError(f"nome reservado por uma fonte de documentação: {name!r}")
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
        skipped = await self._chunks.skips.counts_by_repo()
        out: list[RepoStatus] = []
        for r in sorted(rows.values(), key=lambda r: r.name):
            if (status and r.status != status) or (org and r.org != org) or (name and name not in r.name):
                continue
            n_files, n_chunks = counts.get(r.name, (0, 0))
            out.append(_to_status(r, n_files, n_chunks, skipped.get(r.name, 0)))
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

    # ------------------------------------------------------------ fontes de documentação

    def list_docs(self) -> list[DocSource]:
        return self._config.load_docs()

    async def add_docs(
        self,
        name: str,
        *,
        url: str,
        locale: str = "pt-br",
        include: Sequence[str] = (),
        exclude: Sequence[str] = (),
        links: Sequence[tuple[str, Sequence[str]]] = (),
    ) -> dict:
        """Cadastra uma wiki, validando acesso à lista e ao conteúdo de uma página."""
        from .sources.wikijs import WikiJsClient, WikiJsError

        docs = self._config.load_docs()
        if any(d.name == name for d in docs):
            raise ValueError(f"fonte de documentação já existe: {name}")
        if not url.startswith(("https://", "http://")):
            raise ValueError("url da wiki deve começar com https://")
        source = DocSource(
            name=name, url=url.rstrip("/"), locale=locale, include=tuple(include), exclude=tuple(exclude),
            links=tuple(DocLinkRule(path=p, repos=tuple(r)) for p, r in links),
        )
        if any(r.name == source.repo_name for r in self._config.load_repos()):
            raise ValueError(f"fonte de documentação conflita com repo: {source.repo_name}")
        if source.repo_name in await self._repos.all_rows():
            raise ValueError(f"fonte de documentação conflita com repo existente: {source.repo_name}")
        if (self.settings.repos_dir / source.repo_name).exists():
            raise ValueError(f"pasta já existe para {source.repo_name}; escolha outro nome")
        token = self.settings.wikijs_token.get_secret_value() if self.settings.wikijs_token else None
        client = WikiJsClient(source.url, token)
        pages = await client.list_pages(source.locale)
        if not pages:
            raise WikiJsError("nenhuma página acessível para validar read:source; publique uma página antes de cadastrar")
        await client.get_page(pages[0].id)
        selected = _select_pages(source, pages)
        self._config.save(docs=docs + [source])
        return {"name": source.repo_name, "pages": len(selected), "pages_in_locale": len(pages)}

    async def docs_preview(self, name: str, *, limit: int = 200) -> dict:
        """Prévia sem custo: as páginas que entrariam na indexação, agrupadas pela raiz.

        Lista a wiki (leitura), mas não baixa conteúdo nem chama a Voyage.
        """
        from .sources.wikijs import WikiJsClient

        docs = self._config.load_docs()
        source = next((d for d in docs if d.name == name or d.repo_name == name), None)
        if source is None:
            raise ValueError(f"fonte de documentação não encontrada: {name}")
        token = self.settings.wikijs_token.get_secret_value() if self.settings.wikijs_token else None
        pages = await WikiJsClient(source.url, token).list_pages(source.locale)
        selected = sorted(_select_pages(source, pages), key=lambda p: p.path.lower())
        roots = Counter(p.path.split("/", 1)[0] for p in selected)
        cap = max(1, min(limit, 1000))
        return {
            "name": source.repo_name,
            "url": source.url,
            "locale": source.locale,
            "total": len(selected),
            "pages_in_locale": len(pages),
            "roots": [{"root": r, "pages": n} for r, n in sorted(roots.items(), key=lambda kv: kv[0].lower())],
            "pages": [
                {"path": p.path, "title": p.title, "url": source.page_url(p.locale, p.path),
                 "tags": list(p.tags), "updated_at": p.updated_at}
                for p in selected[:cap]
            ],
            "truncated": len(selected) > cap,
        }

    def _doc_source(self, name: str) -> DocSource:
        source = next((d for d in self._config.load_docs() if d.name == name or d.repo_name == name), None)
        if source is None:
            raise ValueError(f"fonte de documentação não encontrada: {name}")
        return source

    async def _check_link(self, origin: str, reason: str, repo: str) -> tuple[str, str]:
        """Valida origem, motivo e o repo de código de um vínculo. Devolve (repo resolvido, motivo limpo)."""
        if origin not in ("manual", "agent"):
            raise ValueError("origem inválida: use manual ou agent")
        reason = reason.strip()
        if origin == "agent" and not reason:
            raise ValueError("vínculo de agent precisa de motivo (o texto que justifica a ligação)")
        (resolved,) = await self._resolve_repos([repo]) or (None,)
        if resolved is None:
            raise ValueError(f"repositório desconhecido: {repo}")
        state = (await self._repos.all_rows()).get(resolved)
        if state is None or state.kind != "code":
            raise ValueError(f"só repos de código podem receber vínculo: {resolved}")
        return resolved, reason

    async def link_doc(self, source: str, page: str, repo: str, *, reason: str = "", origin: str = "manual") -> dict:
        """Grava um vínculo página → repo (origem manual ou agent) a partir do caminho sem idioma.

        `page` é o caminho da página na wiki, sem idioma (ex.: 'Plataforma/Email'). Não reindexa nada:
        a busca passa a usar o vínculo na próxima consulta, e ele some se a página ou o repo saírem.
        """
        doc = self._doc_source(source)
        page = page.strip().strip("/")
        if not page or ".." in page.split("/"):
            raise ValueError(f"caminho de página inválido: {page!r}")
        result = await self.link_doc_key(doc.repo_name, f"{doc.locale}/{page}.md", repo, reason=reason, origin=origin)
        return {**result, "page": page}

    async def link_doc_key(self, doc_repo: str, doc_path: str, repo: str, *, reason: str = "",
                           origin: str = "manual") -> dict:
        """Grava vínculo pela chave da página (doc_repo + doc_path, como aparece em docs_overview)."""
        doc = self._doc_source(doc_repo)
        if not doc_path.startswith(f"{doc.locale}/") or not doc_path.endswith(".md") or ".." in doc_path.split("/"):
            raise ValueError(f"caminho de página inválido: {doc_path!r}")
        resolved, reason = await self._check_link(origin, reason, repo)
        await self._chunks.links.set_link(doc.repo_name, doc_path, resolved, origin=origin, reason=reason)
        return {
            "doc_repo": doc.repo_name, "doc_path": doc_path, "repo": resolved, "origin": origin,
            "indexed": await self._chunks.links.page_indexed(doc.repo_name, doc_path),
        }

    async def ignore_doc(self, source: str, page: str, reason: str = "") -> dict:
        """Ignora uma página da documentação: ela sai dos resultados da busca (reversível com unignore_doc)."""
        doc = self._doc_source(source)
        page = page.strip().strip("/")
        if not page or ".." in page.split("/"):
            raise ValueError(f"caminho de página inválido: {page!r}")
        doc_path = f"{doc.locale}/{page}.md"
        await self._chunks.links.set_ignore(doc.repo_name, doc_path, reason.strip())
        return {"page": page, "doc": doc.repo_name, "doc_path": doc_path}

    async def unignore_doc(self, source: str, page: str) -> bool:
        doc = self._doc_source(source)
        return await self._chunks.links.remove_ignore(doc.repo_name, f"{doc.locale}/{page.strip('/')}.md")

    async def unlink_doc(self, source: str, page: str, repo: str) -> bool:
        """Remove um vínculo gravado manualmente ou por agent. Devolve False se não existia."""
        doc = self._doc_source(source)
        return await self.unlink_doc_key(doc.repo_name, f"{doc.locale}/{page.strip('/')}.md", repo)

    async def unlink_doc_key(self, doc_repo: str, doc_path: str, repo: str) -> bool:
        doc = self._doc_source(doc_repo)
        (resolved,) = await self._resolve_repos([repo]) or (repo,)
        return await self._chunks.links.remove_link(doc.repo_name, doc_path, resolved)

    async def docs_overview(self, *, limit: int = 500) -> list[dict]:
        """Páginas indexadas, com a raiz e os repos ligados (ver DocLinkStore.overview)."""
        from .domain.doc_links import page_path

        out = []
        for r in await self._chunks.links.overview(limit=limit):
            page = page_path(r["path"])
            out.append({
                "doc_repo": r["repo"], "doc_path": r["path"], "page": page, "root": page.split("/", 1)[0],
                "title": r["title"], "url": r["url"], "links": r["links"],
            })
        return out

    async def doc_links(self, *, source: str | None = None, repo: str | None = None) -> list[dict]:
        """Vínculos gravados (manual/agent), com a página e o repo de cada um."""
        doc_repo = self._doc_source(source).repo_name if source else None
        resolved: str | None = None
        if repo:
            names = await self._resolve_repos([repo]) or []
            resolved = names[0] if names else None
        return await self._chunks.links.list_links(doc_repo=doc_repo, repo=resolved)

    async def index_doc_pages(self, name: str, pages: Sequence[str]) -> SyncResult:
        """Indexa só as páginas indicadas de uma fonte já cadastrada (caminho sem idioma, ex.: 'Plataforma/Email').

        A página precisa estar na seleção da fonte. A seleção não muda, e as outras páginas já baixadas
        ficam como estão (sem reprocessar nem apagar nada).
        """
        from .sources.wikijs import WikiJsClient

        source = self._doc_source(name)
        wanted = {p.strip().strip("/") for p in pages if p.strip().strip("/")}
        if not wanted or any(".." in p.split("/") for p in wanted):
            raise ValueError("informe caminhos de página válidos, ex.: Plataforma/Email")
        token = self.settings.wikijs_token.get_secret_value() if self.settings.wikijs_token else None
        listed = _select_pages(source, await WikiJsClient(source.url, token).list_pages(source.locale))
        missing = sorted(wanted - {p.path for p in listed})
        if missing:
            raise ValueError(f"fora da seleção da fonte {source.name} (ou inexistente): {', '.join(missing)}")
        result = await self._indexer.sync(
            [source.repo_name], batch=None, do_discover=False, pull=True, progress=False,
            allow_register=False, doc_pages=frozenset(wanted),
        )
        await self._ensure_search_indexes()
        return result

    async def skipped_files(self, repo: str | None = None, *, limit: int = 200) -> list[dict]:
        """Arquivos descartados na indexação (de um repo, ou de todos), com o motivo e o tamanho."""
        resolved = None
        if repo:
            names = await self._resolve_repos([repo]) or []
            resolved = names[0] if names else None
        return await self._chunks.skips.rows_for(repo=resolved, limit=limit)

    async def linked_docs(self, repo: str, *, limit: int = 100) -> list[dict]:
        """Páginas de documentação externa ligadas a um repo, com a origem de cada vínculo."""
        resolved = await self._resolve_repos([repo])
        assert resolved
        return await self._chunks.linked_docs(resolved[0], limit=limit)

    async def remove_docs(self, name: str, *, keep_files: bool = False) -> None:
        """Remove a fonte; os trechos somem no próximo sync."""
        from .sources.wikijs import MANIFEST, OWNER_MARKER

        docs = self._config.load_docs()
        source = next((d for d in docs if d.name == name or d.repo_name == name), None)
        if source is None:
            raise ValueError(f"fonte de documentação não encontrada: {name}")
        if any(r.name == source.repo_name for r in self._config.load_repos()):
            raise ValueError(f"fonte de documentação conflita com repo: {source.repo_name}")
        row = (await self._repos.all_rows()).get(source.repo_name)
        if row is not None and (row.kind != "docs" or row.org is not None):
            raise ValueError(f"fonte de documentação conflita com repo existente: {source.repo_name}")
        folder = self.settings.repos_dir / source.repo_name
        if not keep_files and folder.exists() and ((folder / ".git").exists() or
                not ((folder / MANIFEST).is_file() or (folder / OWNER_MARKER).is_file())):
            raise ValueError(f"pasta de {source.repo_name} não identificada como documentação; use --keep-files")
        self._config.save(docs=[d for d in docs if d is not source])
        await self._repos.delete([source.repo_name])
        if not keep_files and folder.is_dir():
            await asyncio.to_thread(shutil.rmtree, folder)

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
        result = await self._indexer.sync(
            names,
            batch=batch,
            do_discover=do_discover,
            pull=pull,
            full=full,
            progress=progress,
            dry_run=dry_run,
            allow_register=allow_register,
        )
        if not dry_run:
            await self._ensure_search_indexes()
        return result

    async def _ensure_search_indexes(self) -> None:
        """Índice BM25 (se o banco tiver pg_search). O CocoIndex pode ter recriado a tabela no sync."""
        try:
            await self._chunks.ensure_bm25_index()
        except Exception as e:  # noqa: BLE001 - índice é otimização; a busca cai no full-text
            log.warning("não consegui criar o índice BM25: %s", e)

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
        kind: str | None = None,
        include_linked_docs: bool = True,
        exclude_repos: list[str] | None = None,
        exclude_path_glob: list[str] | None = None,
        max_docs: int | None = None,
    ) -> list[Hit]:
        """Busca híbrida (vetor + full-text) com rerank opcional, em código e documentação.

        max_docs: limite de páginas de documentação quando a busca mistura código e docs
        (padrão: um quarto do top_k, no mínimo 2).

        exclude_repos / exclude_path_glob: o que não pode aparecer no resultado (nomes de repo e globs de caminho).
        """
        hits = await self._searcher.search(
            query,
            top_k=top_k,
            repos=await self._resolve_repos(repos),
            language=language,
            path_glob=path_glob,
            mode=mode,
            rerank=rerank,
            candidates=candidates,
            kind=kind,
            include_linked_docs=include_linked_docs,
            exclude_repos=await self._resolve_repos(exclude_repos),
            exclude_path_glob=exclude_path_glob,
            max_docs=max_docs,
        )
        for h in hits:  # regras de máscara que só valem na saída (ver retrieval/redaction)
            h.code = mask_output(h.code, h.language)
        return hits

    async def read_file(
        self,
        repo: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Lê um arquivo do clone local (ou do diretório de um repo avulso), com números de linha.

        Aceita o nome como nas outras consultas: 'org__repo', 'org/repo' ou só 'repo'."""
        repo = (await self._resolve_repos([repo]) or [repo])[0]
        root = await asyncio.to_thread(self._local_path, repo)
        return await asyncio.to_thread(retrieval_files.read_file, root, repo, path, start_line, end_line)

    def _local_path(self, repo: str) -> Path:
        try:
            validate_name(repo)
        except ValueError:
            raise ValueError(f"repositório desconhecido: {repo}") from None
        configured = configured_repos(self._config).get(repo)
        if configured is not None:
            return configured.local_path(self.settings.repos_dir)
        return self.settings.repos_dir / repo


    # ------------------------------------------------------------ símbolos e grep

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
    def rerank_variants() -> list[dict[str, Any]]:
        """Configurações que comparam as variações do rerank com a busca híbrida pura."""
        def cfg(name: str, **opts: bool) -> dict[str, Any]:
            return {"name": name, "mode": "hybrid", "rerank": True, "rerank_options": RerankOptions(**opts)}

        return [
            {"name": "hybrid (sem rerank)", "mode": "hybrid", "rerank": False},
            cfg("rerank"),
            cfg("+instrução", instruction=True),
            cfg("+cabeçalho", header=True),
            cfg("+só código", code_only=True),
            cfg("+instr+cab", instruction=True, header=True),
            cfg("+todas", instruction=True, header=True, code_only=True),
            cfg("+instr, pula ident.", instruction=True, skip_identifiers=True),
        ]

    def load_eval_cases(self, path: str | Path) -> list[EvalCase]:
        """Perguntas de avaliação de um arquivo JSON (formato de evals/queries.json)."""
        return load_cases(Path(path))

    async def evaluate(self, cases: Sequence[EvalCase], configs: Sequence[dict[str, Any]],
                       *, top_k: int = 10) -> list[ConfigReport]:
        """Roda as perguntas de avaliação em cada configuração de busca e mede os acertos.

        configs: [{"name", "mode", "rerank", "weights": (vetor, termos) | None,
        "rerank_options": RerankOptions | None}]. Custo: um embedding
        por pergunta (reaproveitado entre configurações) e um rerank por pergunta em cada
        configuração com rerank.
        """
        reports = []
        for cfg in configs:
            results = []
            for case in cases:
                hits = await self._searcher.search(
                    case.query, top_k=top_k, repos=await self._resolve_repos(case.repos),
                    kind=case.kind, mode=cfg.get("mode", "hybrid"), rerank=cfg.get("rerank", True),
                    weights=cfg.get("weights"), rerank_options=cfg.get("rerank_options"),
                )
                results.append(CaseResult(case.query, first_hit(hits, case.expect),
                                          [f"{h.repo}/{h.path}:{h.start_line}" for h in hits[:3]], case.tag))
            reports.append(ConfigReport(cfg["name"], results))
        return reports

    async def search_engine(self) -> str:
        """Motor da busca por termos em uso: bm25 | fts | fts-legacy | none (ver infra/chunk_store)."""
        return (await self._chunks.capabilities(refresh=True)).keyword_engine

    async def find_symbol(
        self,
        name: str,
        *,
        kind: str | None = None,
        repos: Sequence[str] | None = None,
        language: str | None = None,
        limit: int = 20,
    ) -> list[SymbolHit]:
        """Onde uma classe/função/método/recurso é declarado (tabela preenchida na indexação)."""
        hits = await self._symbols.find(
            name, kind=kind, repos=await self._resolve_repos(repos), language=language, limit=limit
        )
        return await asyncio.to_thread(self._fix_annotated_lines, hits)

    def _fix_annotated_lines(self, hits: list[SymbolHit]) -> list[SymbolHit]:
        """Classes anotadas: o índice guarda a linha da 1ª anotação (`@Service`). Acha a linha da
        declaração no clone, sem reindexar. Se não achar, mantém o que veio do índice."""
        out = []
        for h in hits:
            if h.signature.startswith("@"):
                found = retrieval_files.declaration_line(self._local_path(h.repo), h.path, h.line, h.name)
                if found:
                    h = replace(h, line=found[0], signature=found[1])
            out.append(h)
        return out

    async def grep(
        self,
        pattern: str,
        *,
        repos: Sequence[str] | None = None,
        path_glob: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> GrepResult:
        """Texto exato ou regex nos clones locais, com os filtros de arquivo do índice e segredos mascarados."""
        wanted = await self._resolve_repos(repos)
        rows = await self._repos.all_rows()
        orgs = self._config.load_orgs()
        manual = configured_repos(self._config)
        targets: list[GrepTarget] = []
        for row in rows.values():
            if row.ignored or (wanted is not None and row.name not in wanted):
                continue
            repo = repo_for_row(row, orgs, manual)
            root = repo.local_path(self.settings.repos_dir)
            if root.is_dir():
                targets.append(GrepTarget(repo.name, root.resolve(), repo.include, tuple(repo.all_excludes)))
        if not targets:
            return GrepResult(matches=[], truncated=False)

        def mask(path: str, line: str) -> str:
            return mask_output(line, file_language(path.rsplit("/", 1)[-1]))

        return await self._grep.search(
            targets, pattern, regex=regex, case_sensitive=case_sensitive, path_glob=path_glob,
            max_results=max_results, mask=mask,
        )

    # ------------------------------------------------------------ jobs de sync

    async def start_sync(
        self,
        principal: Principal | None,
        names: list[str] | None,
        *,
        batch: int,
        do_discover: bool = True,
        pull: bool = True,
    ) -> JobRecord:
        """Inicia uma indexação em segundo plano e devolve o job (já com id). Um por vez.

        Levanta RuntimeError se já houver um job rodando.
        """
        actor = principal.actor if principal else "operador local"
        params = {"repos": names, "batch": batch, "discover": do_discover, "pull": pull}
        async with self._job_lock:
            running = await self._jobs.running()
            if running is not None:
                raise RuntimeError(f"já existe um sync em andamento: job {running.id}")
            job_id = await self._jobs.create(actor, params)
            task = asyncio.create_task(self._run_job(job_id, principal, names, batch, do_discover, pull))
            self._job_tasks.add(task)
            task.add_done_callback(self._job_tasks.discard)
        job = await self._jobs.get(job_id)
        assert job is not None
        return job

    async def _run_job(
        self,
        job_id: str,
        principal: Principal | None,
        names: list[str] | None,
        batch: int,
        do_discover: bool,
        pull: bool,
    ) -> None:
        status, result, error = "failed", None, None
        try:
            res = await self.sync(
                names, batch=batch, do_discover=do_discover, pull=pull, progress=False,
                allow_register=principal is None or principal.has(ADMIN),
            )
            status, result = "done", res.to_dict()
            if res.selected and not res.indexed and res.errors:
                # Nada foi indexado e todos os selecionados falharam: o job falhou, não "concluiu".
                first = next(iter(res.errors.values())).splitlines()[0]
                status = "failed"
                error = f"todos os {len(res.selected)} repo(s) falharam; ex.: {first[:300]}"
        except asyncio.CancelledError:
            error = "interrompido: o servidor foi encerrado durante a indexação"
            await self._jobs.finish(job_id, status=status, result=None, error=error)
            raise
        except Exception as e:  # noqa: BLE001 - o erro vai para o job, não derruba o servidor
            error = str(e)
            log.exception("job de sync %s falhou", job_id)
        await self._jobs.finish(job_id, status=status, result=result, error=error)
        await self.audit(principal, "sync:job", params={"job": job_id, "status": status})

    async def recover_interrupted_jobs(self) -> int:
        """Marca como `failed` os jobs que ficaram `running` de um servidor que caiu.

        Chamar SÓ na inicialização do servidor HTTP, o único processo que executa jobs.
        Não pode rodar em `open_service()`: todo comando da CLI abre o serviço, e marcaria
        como falho o job que um servidor ainda está executando.
        """
        n = await self._jobs.fail_interrupted("o servidor reiniciou durante a indexação")
        if n:
            log.warning("%d job(s) de sync interrompido(s) por reinício marcados como falhos", n)
        return n

    async def get_job(self, job_id: str) -> JobRecord | None:
        return await self._jobs.get(job_id)

    async def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        return await self._jobs.recent(limit)

    async def shutdown(self) -> None:
        """Encerra os jobs em andamento (marca como interrompidos). Chamado antes de fechar o pool."""
        tasks = list(self._job_tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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


def _select_pages(source: DocSource, pages: Sequence[Any]) -> list[Any]:
    """Páginas que passam nos filtros include/exclude da fonte (glob sobre o caminho da página)."""
    from .sources.wikijs import page_matches

    return [p for p in pages if (not source.include or page_matches(p.path, source.include))
            and not page_matches(p.path, source.exclude)]


@asynccontextmanager
async def open_service(settings: Settings | None = None) -> AsyncIterator[CodeIndexService]:
    """Abre o pool do Postgres, monta os componentes e fecha tudo na saída (inclusive clientes LiteLLM)."""
    settings = settings if settings is not None else get_settings()
    try:
        token = resolve_github_token(settings)
        async with open_pool(settings) as pool:
            jobs = JobStore(pool, settings)
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
            )
            await svc._ensure_search_indexes()
            try:
                yield svc
            finally:
                await svc.shutdown()  # antes de fechar o pool
    finally:
        await llm.close_clients()
