"""Orquestra descoberta, seleção de lote, git sync e o pipeline CocoIndex.

Fluxo de um `sync`, em etapas (métodos privados, nesta ordem):
  1. `_lock`: um sync por vez (flock em `lock_file`);
  2. `_discover_all`: redescobre os repos das [[org]] pela API (opcional);
  3. `_load_rows`: espelha os [[repo]] do repos.toml na tabela de estado e poda orgs removidas;
  4. `_select`: os nomes pedidos, ou um lote de até N repos (alterados upstream > pendentes > com erro);
  5. `_fetch`: clona/atualiza só os selecionados;
  6. `_run_pipeline`: roda o pipeline com TODOS os repos indexados. Os não selecionados passam com
     a revisão já gravada, então o `index_repo` memoizado os pula sem percorrer nada;
  7. `_record`: grava commit/revisão/erro de cada selecionado.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import time
from collections.abc import Iterator

import cocoindex as coco

from ..domain.models import Org, Repo, RepoState, SyncResult
from ..domain.policies import (
    name_from_path,
    name_from_url,
    normalize_source,
    pick_batch,
    resolve_name,
)
from ..infra.config_file import ConfigFile
from ..infra.git import GitClient
from ..infra.github import GitHubClient
from ..infra.repo_store import RepoStore
from ..settings import Settings
from ..sources.github_org import GitHubOrgSource
from ..sources.registry import doc_plugin
from ..sources.base import OWNER_MARKER, is_docs_folder

log = logging.getLogger(__name__)


class Indexer:
    def __init__(
        self,
        settings: Settings,
        repos: RepoStore,
        config: ConfigFile,
        git: GitClient,
        github: GitHubClient,
    ) -> None:
        self._settings = settings
        self._repos = repos
        self._config = config
        self._git = git
        self._github = github

    async def discover(self, orgs: list[Org] | None = None) -> dict[str, dict]:
        """Redescobre os repos das orgs (sem clonar nem indexar)."""
        orgs = self._config.load_orgs() if orgs is None else orgs
        out: dict[str, dict] = {}
        for org in orgs:
            found = await GitHubOrgSource(org, self._github).discover()
            out[org.name] = await self._repos.reconcile_org(org.name, found)
        return out

    async def sync(
        self,
        names: list[str] | None = None,
        *,
        batch: int | None,
        do_discover: bool = True,
        pull: bool = True,
        full: bool = False,
        progress: bool = True,
        dry_run: bool = False,
        allow_register: bool = True,
        doc_pages: frozenset[str] | None = None,
    ) -> SyncResult:
        """Sincroniza e indexa.

        names: indexa exatamente estes (revarrendo mesmo sem mudança de commit);
               aceita nomes desconhecidos no formato 'org/repo', URL ou diretório.
        batch: sem `names`, processa no máximo N repos (None = todos os candidatos).
        dry_run: só mostra a seleção (não clona, não indexa, não gasta tokens).
        allow_register: aceita nomes desconhecidos (URL, 'org/repo', diretório) registrando-os
               como repo avulso. Desligado para chamadas remotas sem scope admin.
        doc_pages: só para fontes de documentação: sincroniza apenas estas páginas (caminho sem idioma).
        """
        res = SyncResult()
        with self._lock():
            orgs = self._config.load_orgs()
            if do_discover and orgs:
                await self._discover_all(orgs, res)

            rows = await self._load_rows(orgs)
            selected, remaining, rows = await self._select(names, rows, batch, orgs, allow_register)
            res.selected = selected
            res.remaining = remaining
            if dry_run:
                return res

            manual = configured_repos(self._config)
            revisions = await self._fetch(
                selected, rows, orgs, manual, forced=bool(names), pull=pull, res=res, doc_pages=doc_pages
            )
            plan = self._plan(rows, revisions, orgs, manual)
            if not selected:
                log.info("nenhum repo para sincronizar; aplicando apenas remoções pendentes")
            pipeline_error = await self._run_pipeline(plan, progress=progress, full=full)
            await self._record(rows, revisions, res, pipeline_error)
        return res

    # --- etapas -------------------------------------------------------------------

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        path = self._settings.lock_file
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(
                    "já existe um sync em andamento (watch, MCP ou outro terminal)"
                ) from None
            yield

    async def _discover_all(self, orgs: list[Org], res: SyncResult) -> None:
        for org in orgs:
            try:
                res.discovered[org.name] = (await self.discover([org]))[org.name]
            except Exception as e:  # noqa: BLE001 - falha numa org não impede as demais
                log.error("descoberta da org %s falhou: %s", org.name, e)
                res.errors[f"org:{org.name}"] = str(e)

    async def _load_rows(self, orgs: list[Org]) -> dict[str, RepoState]:
        # [[repo]] avulsos e fontes [[docs]] (cada fonte vira um repo kind="docs")
        await self._repos.reconcile_manual(configured_repos(self._config).values())
        await self._repos.prune_orgs([o.name for o in orgs])
        return await self._repos.all_rows()

    async def _select(
        self,
        names: list[str] | None,
        rows: dict[str, RepoState],
        batch: int | None,
        orgs: list[Org],
        allow_register: bool = True,
    ) -> tuple[list[str], int, dict[str, RepoState]]:
        """Devolve (selecionados, quantos ficaram para os próximos lotes, linhas atualizadas)."""
        if names:
            selected: list[str] = []
            for n in names:
                resolved = resolve_name(n, rows)
                if resolved is None:
                    if not allow_register:
                        raise PermissionError(f"repo desconhecido: {n} (só admins registram repos novos)")
                    resolved = self._register_manual(n)
                    rows = await self._load_rows(orgs)
                selected.append(resolved)
            return list(dict.fromkeys(selected)), 0, rows
        manual_changed = await self._manual_changed(rows)
        selected, remaining = pick_batch(rows, batch, orgs, manual_changed)
        return selected, remaining, rows

    def _register_manual(self, source: str) -> str:
        """Nome não encontrado mas é 'org/repo', URL git ou diretório: adiciona como [[repo]]."""
        url, path = normalize_source(source)
        if url:
            name = name_from_url(url)
        elif path:
            name = name_from_path(path)
        else:
            raise ValueError(f"repositório desconhecido: {source}")
        repos = self._config.load_repos()
        if not any(r.name == name for r in repos):
            repos.append(Repo(name=name, url=url, path=path))
            self._config.save(repos=repos)
            log.info("registrado novo repo avulso %s", name)
        return name

    async def _manual_changed(self, rows: dict[str, RepoState]) -> set[str]:
        """Repos avulsos (com URL, sem org) cujo HEAD remoto difere do commit indexado."""
        changed: set[str] = set()
        for r in rows.values():
            if r.ignored or r.error or not r.indexed or r.path is not None or r.org is not None:
                continue
            if r.kind == "docs":  # não é git: o conector da wiki decide o que mudou
                continue
            if r.url is None:
                continue
            try:
                head = await asyncio.to_thread(self._git.remote_head, r.url, r.default_branch)
            except RuntimeError as e:
                log.warning("ls-remote falhou para %s: %s", r.name, e)
                continue
            if head != r.indexed_commit:
                changed.add(r.name)
        return changed

    async def _fetch(
        self,
        selected: list[str],
        rows: dict[str, RepoState],
        orgs: list[Org],
        manual: dict[str, Repo],
        *,
        forced: bool,
        pull: bool,
        res: SyncResult,
        doc_pages: frozenset[str] | None = None,
    ) -> dict[str, tuple[str | None, str]]:
        """Git sync dos selecionados. Devolve {nome: (commit, revisão)} dos que deram certo."""
        revisions: dict[str, tuple[str | None, str]] = {}
        for name in selected:
            row = rows[name]
            repo = self._to_repo(row, orgs, manual)
            try:
                if repo.kind == "docs":
                    commit = None
                    revision = await self._fetch_docs(repo, row, pull, only=doc_pages)
                elif pull or not repo.local_path(self._settings.repos_dir).is_dir():
                    log.info("git sync %s", name)
                    commit = await asyncio.to_thread(self._git.sync, repo)
                    revision = commit or "local"
                else:
                    commit = row.indexed_commit
                    revision = commit or "local"
                if forced or repo.path is not None:
                    revision += f"#{time.time_ns()}"  # força revarrer o repo (arquivos seguem memoizados)
                revisions[name] = (commit, revision)
            except Exception as e:  # noqa: BLE001
                log.error("git sync falhou para %s: %s", name, e)
                res.errors[name] = str(e)
        return revisions

    async def _fetch_docs(self, repo: Repo, row: RepoState, pull: bool, *, only: frozenset[str] | None = None) -> str:
        """Baixa só as páginas novas/alteradas da fonte de documentação. Revisão = hash do manifesto."""
        dest = repo.local_path(self._settings.repos_dir)
        existed = dest.exists()
        if existed:
            if not is_docs_folder(dest):
                raise RuntimeError(f"pasta de {repo.name} não identificada como documentação")
        else:
            dest.mkdir(parents=True)
            (dest / OWNER_MARKER).write_text("code-index docs\n", encoding="utf-8")
        if not pull and only is None and existed and row.revision:  # sem pull: reaproveita o que já foi baixado
            return row.revision.split("#", 1)[0]
        source = repo.doc_source
        if source is None:
            raise RuntimeError(f"fonte de documentação {repo.name} não está mais no repos.toml")
        return await doc_plugin(source.type).fetch(source, dest, self._settings, only=only)

    def _plan(
        self,
        rows: dict[str, RepoState],
        revisions: dict[str, tuple[str | None, str]],
        orgs: list[Org],
        manual: dict[str, Repo],
    ) -> list[tuple[Repo, str]]:
        """Selecionados (com a revisão nova) + indexados não selecionados (com a revisão gravada)."""
        plan: list[tuple[Repo, str]] = []
        for row in rows.values():
            if row.ignored and row.name not in revisions:
                continue
            if row.name in revisions:
                plan.append((self._to_repo(row, orgs, manual), revisions[row.name][1]))
            elif row.revision is not None:
                # mesma revisão já gravada => memo hit, o repo não é percorrido
                plan.append((self._to_repo(row, orgs, manual), row.revision))
        return plan

    async def _run_pipeline(
        self, plan: list[tuple[Repo, str]], *, progress: bool, full: bool
    ) -> str | None:
        """Roda o pipeline CocoIndex. Devolve a mensagem de erro, ou None se deu certo."""
        # import tardio: só carrega o CocoIndex quando indexar
        from .code_flow import build_app

        error: str | None = None
        try:
            await asyncio.to_thread(
                build_app(plan).update_blocking,
                report_to_stdout=progress,
                full_reprocess=full,
            )
        except Exception as e:
            log.exception("pipeline falhou")
            error = f"pipeline: {e}"
        finally:
            # Finaliza o lifespan (fecha pool do Postgres e sessões HTTP do LiteLLM);
            # o ambiente é recriado sob demanda no próximo sync (watch/MCP).
            await asyncio.to_thread(coco.stop_blocking)
        return error

    async def _record(
        self,
        rows: dict[str, RepoState],
        revisions: dict[str, tuple[str | None, str]],
        res: SyncResult,
        pipeline_error: str | None,
    ) -> None:
        for name, msg in res.errors.items():
            if name in rows:
                await self._repos.mark_error(name, msg)
        for name, (commit, revision) in revisions.items():
            if pipeline_error:
                await self._repos.mark_error(name, pipeline_error)
                res.errors[name] = pipeline_error
                continue
            if rows[name].ignored:
                await self._repos.set_ignored(name, False)
            await self._repos.mark_indexed(name, commit, revision, rows[name].pushed_at)
            res.indexed[name] = commit

    # --- auxiliares ---------------------------------------------------------------

    @staticmethod
    def _to_repo(row: RepoState, orgs: list[Org], manual: dict[str, Repo]) -> Repo:
        return repo_for_row(row, orgs, manual)


def configured_repos(config: ConfigFile) -> dict[str, Repo]:
    """Repos definidos no repos.toml: [[repo]] avulsos + um repo kind="docs" por fonte [[docs]]."""
    repos = {r.name: r for r in config.load_repos()}
    for source in config.load_docs():
        if source.repo_name in repos:
            raise ValueError(f"fonte de documentação conflita com repo: {source.repo_name}")
        repos[source.repo_name] = source.to_repo()
    return repos


def repo_for_row(row: RepoState, orgs: list[Org], manual: dict[str, Repo]) -> Repo:
    """Repo (com pasta e filtros de arquivo) de uma linha da tabela de estado."""
    if row.org is None:
        if row.name in manual:
            return manual[row.name]
        return Repo(name=row.name, url=row.url, path=row.path)
    org = next((o for o in orgs if o.name == row.org), None) or Org(name=row.org)
    return Repo(
        name=row.name,
        url=row.url,
        include=org.include,
        exclude=org.exclude,
        extra_exclude=org.extra_exclude,
    )
