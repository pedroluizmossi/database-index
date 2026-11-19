"""Orquestra descoberta, seleção de lote, git sync e o pipeline CocoIndex.

Fluxo de um `sync`, em etapas (métodos privados, nesta ordem):
  1. `exclusive`: um sync por vez (advisory lock do Postgres, vale entre processos e pods);
  2. `_discover_all`: redescobre os repos das orgs pela API (opcional);
  3. `_load_rows`: espelha os repos avulsos e fontes de docs da configuração (banco) na tabela de estado e poda orgs removidas;
  4. `_select`: os nomes pedidos, ou um lote de até N repos (alterados upstream > pendentes > com erro);
  5. `_fetch`: clona/atualiza só os selecionados;
  6. `_run_pipeline`: roda o pipeline com TODOS os repos indexados. Os não selecionados passam com
     a revisão já gravada, então o `index_repo` memoizado os pula sem percorrer nada;
  7. `_record`: grava commit/revisão/erro de cada selecionado.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any
from pathlib import Path

import asyncpg
import cocoindex as coco

from ..domain.sync_errors import explain_error
from ..domain.models import Org, Repo, RepoState, SyncResult
from ..domain.policies import (
    name_from_path,
    name_from_url,
    normalize_source,
    pick_batch,
    resolve_name,
)
from ..infra.config_store import ConfigStore
from ..infra.db import try_advisory_lock
from ..infra.git import GitClient
from ..infra.github import GitHubClient
from ..infra.repo_store import RepoStore
from ..profiles.base import ActiveProfiles
from ..profiles.registry import active_profiles
from ..settings import Settings
from ..sources.github_org import GitHubOrgSource
from ..sources.registry import doc_plugin
from ..sources.base import OWNER_MARKER, is_docs_folder
from . import progress as progress_mod
from .progress import SyncProgress

log = logging.getLogger(__name__)

ProgressSink = Callable[[dict[str, Any]], Awaitable[None]]
# De quanto em quanto tempo o progresso do pipeline é lido e publicado (o job é gravado no banco).
PROGRESS_INTERVAL_S = 2.0
# De quanto em quanto tempo o sync confere a conexão que segura a trava (caiu = trava perdida: o sync para).
LOCK_HEARTBEAT_S = 30.0


class SyncBusy(RuntimeError):
    """Outro sync (CLI, watch ou worker, em qualquer processo ou pod do mesmo banco) detém a trava."""


class Indexer:
    def __init__(
        self,
        settings: Settings,
        repos: RepoStore,
        config: ConfigStore,
        git: GitClient,
        github: GitHubClient,
        pool: asyncpg.Pool,
        profiles: ActiveProfiles | None = None,
    ) -> None:
        self._settings = settings
        # Perfis de organização ativos (None = os de settings.profiles): ruído do exclude padrão dos repos.
        self._profiles = profiles if profiles is not None else active_profiles(settings)
        self._pool = pool  # trava do sync (advisory lock): o mesmo banco das tabelas de estado
        self._repos = repos
        self._config = config
        self._git = git
        self._github = github
        self._stop_reason: str | None = None   # parada pedida (botão "Parar" ou atualização do sistema)
        self._tracker: SyncProgress | None = None
        self._in_pipeline = False

    async def discover(self, orgs: list[Org] | None = None) -> dict[str, dict]:
        """Redescobre os repos das orgs (sem clonar nem indexar)."""
        orgs = await self._config.orgs() if orgs is None else orgs
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
        on_progress: ProgressSink | None = None,
        only_changed: bool = False,
    ) -> SyncResult:
        """Sincroniza e indexa.

        names: indexa exatamente estes (revarrendo mesmo sem mudança de commit);
               aceita nomes desconhecidos no formato 'org/repo', URL ou diretório.
        batch: sem `names`, processa no máximo N repos (None = todos os candidatos).
        dry_run: só mostra a seleção: não trava o sync (pode rodar junto de um), não registra nomes novos
               (nem na configuração), não clona, não indexa e não gasta tokens. Só atualiza o catálogo como
               `orgs refresh` faria: descoberta das orgs e configuração → tabela de estado.
        allow_register: aceita nomes desconhecidos (URL, 'org/repo', diretório) registrando-os
               como repo avulso. Desligado para chamadas remotas sem scope admin.
        doc_pages: só para fontes de documentação: sincroniza apenas estas páginas (caminho sem idioma).
        only_changed: no lote automático, só o que já foi indexado e mudou (push novo, fontes de docs); sem
               pendentes nem erros (`pick_batch`).
        on_progress: recebe retratos do andamento (etapa, e por repo: arquivos e trechos processados).
            Falhas ao publicar não interrompem o sync.
        """
        res = SyncResult()
        self._stop_reason = None
        if dry_run:
            orgs = await self._config.orgs()
            if do_discover and orgs:
                await self._discover_all(orgs, res)
            rows = await self._load_rows(orgs)
            res.selected, res.remaining, _ = await self._select(names, rows, batch, orgs, allow_register, dry_run=True,
                                                                only_changed=only_changed)
            return res
        tracker = SyncProgress()
        self._tracker = tracker
        publish = _publisher(tracker, on_progress)
        try:
            return await self._sync_locked(names, res, tracker, publish, batch=batch, do_discover=do_discover,
                                           pull=pull, full=full, progress=progress, allow_register=allow_register,
                                           doc_pages=doc_pages, only_changed=only_changed)
        finally:
            self._tracker = None
            self._stop_reason = None

    def request_stop(self, reason: str) -> bool:
        """Pede para o sync em andamento parar sem perder o que já foi feito: entre um download e outro, ou (na
        indexação) pelo cancelamento do próprio CocoIndex, que guarda os arquivos já processados. False se não há
        sync rodando neste processo."""
        tracker = self._tracker
        if tracker is None:
            return False
        if self._stop_reason is None:
            self._stop_reason = reason
            tracker.set_stopping(reason)
            log.warning("parada pedida: %s", reason)
            if self._in_pipeline:
                from cocoindex._internal import core as coco_core

                coco_core.cancel_all()
        return True

    async def _sync_locked(self, names: list[str] | None, res: SyncResult, tracker: SyncProgress,
                           publish: Callable[[], Awaitable[None]], *, batch: int | None, do_discover: bool, pull: bool,
                           full: bool, progress: bool, allow_register: bool, doc_pages: frozenset[str] | None,
                           only_changed: bool) -> SyncResult:
        async with self.exclusive():
            orgs = await self._config.orgs()
            if do_discover and orgs:
                await publish()
                await self._discover_all(orgs, res)

            tracker.set_phase("select")
            await publish()
            rows = await self._load_rows(orgs)
            selected, remaining, rows = await self._select(names, rows, batch, orgs, allow_register,
                                                           only_changed=only_changed)
            res.selected = selected
            res.remaining = remaining

            tracker.select(selected)
            tracker.set_phase("fetch")
            await publish()
            manual = await configured_repos(self._config)
            revisions = await self._fetch(
                selected, rows, orgs, manual, forced=bool(names), pull=pull, res=res, doc_pages=doc_pages,
                tracker=tracker, publish=publish,
            )
            plan = self._plan(rows, revisions, orgs, manual)
            if not selected:
                log.info("nenhum repo para sincronizar; aplicando apenas remoções pendentes")
            pipeline_error: str | None = None
            if self._stop_reason is None:
                await self._count_files(plan, revisions, tracker)
                tracker.set_phase("index")
                await publish()
                pipeline_error = await self._run_pipeline(plan, progress=progress, full=full, tracker=tracker,
                                                          publish=publish)
            tracker.set_phase("finish")
            await publish()
            if self._stop_reason is not None:
                done = tracker.done_names()
                res.stopped = self._stop_reason
                res.not_done = [n for n in selected if n not in done and n not in res.errors]
                revisions = {n: r for n, r in revisions.items() if n in done}  # só os que terminaram contam
                pipeline_error = None
                for name in res.not_done:
                    tracker.update(name, status="skipped", error="não terminou: o job foi parado")
            await self._record(rows, revisions, res, pipeline_error)
            for name, msg in res.errors.items():
                tracker.update(name, status="error", error=msg.splitlines()[0][:300] if msg else msg)
            await publish()
            if self._stop_reason is None:
                await self._prune_orphan_folders()
        return res

    async def _prune_orphan_folders(self) -> None:
        """Apaga as pastas de `repos_dir` de repos e fontes que saíram (repo/org/fonte removidos pela API, cujo pod
        monta os clones só para leitura e não apaga nada). Só pastas que o code-index criou: clone git ou pasta de
        documentação; o resto fica. Roda sob a trava do sync (nenhum sync lendo os clones)."""
        root = self._settings.repos_dir
        if not root.is_dir():
            return
        # A pasta de clones pode ser compartilhada por outro índice (outro schema, ex.: um teste com outro modelo):
        # o que é órfão para um é clone do outro. Só limpa a pasta que pertence a este schema (marcador na raiz);
        # sem marcador, marca e não apaga nada nesta rodada.
        owner = await asyncio.to_thread(_claim_repos_dir, root, self._settings.pg_schema)
        if owner is None:
            return
        if owner != self._settings.pg_schema:
            log.warning("pastas órfãs não apagadas: %s pertence ao schema %r (este é %r); use outra "
                        "CODE_INDEX_REPOS_DIR para cada índice", root, owner, self._settings.pg_schema)
            return
        known = set(await self._repos.all_rows()) | set(await configured_repos(self._config))
        for entry in await asyncio.to_thread(lambda: sorted(root.iterdir())):
            if entry.name in known or entry.name.startswith(".") or not entry.is_dir() or entry.is_symlink():
                continue
            if not ((entry / ".git").exists() or is_docs_folder(entry)):
                continue
            try:
                await asyncio.to_thread(shutil.rmtree, entry)
                log.info("pasta órfã apagada: %s (repo ou fonte que saiu da configuração)", entry)
            except OSError as e:
                log.warning("não consegui apagar a pasta órfã %s: %s", entry, e)

    # --- etapas -------------------------------------------------------------------

    @property
    def lock_key(self) -> str:
        """Chave do advisory lock do sync: por schema (dois índices no mesmo banco não se travam)."""
        return f"code_index.sync.{self._settings.pg_schema}"

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Trava do sync: advisory lock do Postgres numa conexão reservada do pool, mantida durante todo o
        bloco (vale entre processos e pods). Também a usa quem apaga clones, para não tirar a pasta de baixo
        de um sync em andamento. SyncBusy (RuntimeError) se outro sync já está rodando."""
        async with try_advisory_lock(self._pool, self.lock_key, heartbeat_s=LOCK_HEARTBEAT_S) as got:
            if not got:
                raise SyncBusy("já existe um sync em andamento (watch, worker ou outro terminal)")
            yield

    async def busy(self) -> bool:
        """True se outro sync detém a trava agora (sonda sem esperar; o worker a usa antes de reservar um job)."""
        async with try_advisory_lock(self._pool, self.lock_key) as got:
            return not got

    async def _discover_all(self, orgs: list[Org], res: SyncResult) -> None:
        for org in orgs:
            try:
                res.discovered[org.name] = (await self.discover([org]))[org.name]
            except Exception as e:  # noqa: BLE001 - falha numa org não impede as demais
                log.error("descoberta da org %s falhou: %s", org.name, e)
                res.errors[f"org:{org.name}"] = str(e)

    async def _load_rows(self, orgs: list[Org]) -> dict[str, RepoState]:
        # repos avulsos e fontes de documentação (cada fonte vira um repo kind="docs")
        await self._repos.reconcile_manual((await configured_repos(self._config)).values())
        await self._repos.prune_orgs([o.name for o in orgs])
        return await self._repos.all_rows()

    async def _select(
        self,
        names: list[str] | None,
        rows: dict[str, RepoState],
        batch: int | None,
        orgs: list[Org],
        allow_register: bool = True,
        *,
        dry_run: bool = False,
        only_changed: bool = False,
    ) -> tuple[list[str], int, dict[str, RepoState]]:
        """Devolve (selecionados, quantos ficaram para os próximos lotes, linhas atualizadas).
        dry_run: nome desconhecido entra na seleção com o nome que teria, sem ser registrado."""
        if names:
            selected: list[str] = []
            for n in names:
                resolved = resolve_name(n, rows, self._profiles.naming.repo_prefixes)
                if resolved is None:
                    if not allow_register:
                        raise PermissionError(f"repo desconhecido: {n} (só admins registram repos novos)")
                    if dry_run:
                        resolved = self._manual_name(n)[0]
                    else:
                        resolved = await self._register_manual(n)
                        rows = await self._load_rows(orgs)
                elif rows[resolved].ignored and not allow_register:
                    # Ignorar e reativar é de admin (DELETE /repos, /unignore); indexar pelo nome reativaria.
                    raise PermissionError(f"repo ignorado: {resolved} (só admins reativam repos ignorados)")
                selected.append(resolved)
            return list(dict.fromkeys(selected)), 0, rows
        manual_changed = await self._manual_changed(rows)
        selected, remaining = pick_batch(rows, batch, orgs, manual_changed, only_changed=only_changed)
        return selected, remaining, rows

    @staticmethod
    def _manual_name(source: str) -> tuple[str, str | None, str | None]:
        """(nome, url, caminho) de um repo avulso a partir de 'org/repo', URL git ou diretório."""
        url, path = normalize_source(source)
        if url:
            return name_from_url(url), url, None
        if path:
            return name_from_path(path), None, path
        raise ValueError(f"repositório desconhecido: {source}")

    async def _register_manual(self, source: str) -> str:
        """Nome não encontrado mas é 'org/repo', URL git ou diretório: cadastra como repo avulso."""
        name, url, path = self._manual_name(source)
        existing = next((r for r in await self._config.repos() if r.name == name), None)
        if existing is None:
            if not await self._config.add(Repo(name=name, url=url, path=path)):
                return await self._register_manual(source)  # outro processo cadastrou agora: confere a origem
            log.info("registrado novo repo avulso %s", name)
        elif not _same_source(existing, url, path):
            # Mesmo nome, outra origem (~/a/api e ~/b/api; github e gitlab com o mesmo org/repo).
            raise ValueError(
                f"já existe um repo chamado {name} ({existing.url or existing.path}); "
                "registre este com outro nome (`code-index repo add --name`)"
            )
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
        tracker: SyncProgress | None = None,
        publish: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, tuple[str | None, str]]:
        """Git sync dos selecionados. Devolve {nome: (commit, revisão)} dos que deram certo."""
        revisions: dict[str, tuple[str | None, str]] = {}
        for name in selected:
            if self._stop_reason is not None:  # parada pedida: os que faltam não são baixados
                break
            if tracker is not None and publish is not None:
                tracker.update(name, status="fetching")
                await publish()
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
                if tracker is not None:
                    tracker.update(name, status="queued")
            except Exception as e:  # noqa: BLE001
                log.error("git sync falhou para %s: %s", name, e)
                res.errors[name] = explain_error(str(e))
                if tracker is not None:
                    tracker.update(name, status="error", error=res.errors[name][:300])
        return revisions

    async def _count_files(
        self, plan: list[tuple[Repo, str]], revisions: dict[str, tuple[str | None, str]], tracker: SyncProgress
    ) -> None:
        """Total de arquivos de cada repo que vai ser indexado (para mostrar "5 de 25")."""
        from .code_flow import count_repo_files

        for repo, _revision in plan:
            if repo.name not in revisions:
                continue
            try:
                total = await count_repo_files(repo)
            except Exception as e:  # noqa: BLE001 - sem total, o progresso só mostra o que já foi feito
                log.warning("não consegui contar os arquivos de %s: %s", repo.name, e)
                total = None
            tracker.update(repo.name, status="indexing", files_total=total)

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
            raise RuntimeError(f"fonte de documentação {repo.name} não está mais configurada")
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
        self,
        plan: list[tuple[Repo, str]],
        *,
        progress: bool,
        full: bool,
        tracker: SyncProgress | None = None,
        publish: Callable[[], Awaitable[None]] | None = None,
    ) -> str | None:
        """Roda o pipeline CocoIndex. Devolve a mensagem de erro, ou None se deu certo.

        Enquanto roda (numa thread), lê os contadores por repo e publica o progresso a cada
        PROGRESS_INTERVAL_S segundos.
        """
        # import tardio: só carrega o CocoIndex quando indexar
        from .code_flow import build_app

        from cocoindex._internal import core as coco_core

        error: str | None = None
        progress_mod.set_active(tracker)
        coco_core.reset_global_cancellation()  # uma parada anterior não pode cancelar esta rodada
        self._in_pipeline = True
        try:
            run = asyncio.ensure_future(asyncio.to_thread(
                build_app(plan).update_blocking,
                report_to_stdout=progress,
                full_reprocess=full,
            ))
            while not run.done():
                await asyncio.wait({run}, timeout=PROGRESS_INTERVAL_S)
                if tracker is not None and publish is not None:
                    tracker.refresh()
                    await publish()
            run.result()
        except Exception as e:
            if self._stop_reason is not None:
                log.warning("indexação interrompida: %s", self._stop_reason)
            else:
                log.exception("pipeline falhou")
                error = f"indexação: {explain_error(str(e))}"
        finally:
            self._in_pipeline = False
            coco_core.reset_global_cancellation()
            progress_mod.set_active(None)
            # Finaliza o lifespan (fecha pool do Postgres e sessões HTTP do LiteLLM);
            # o ambiente é recriado sob demanda no próximo sync (watch/MCP).
            await asyncio.to_thread(coco.stop_blocking)
        if tracker is not None:
            tracker.finish_indexing(error)
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

    @property
    def github(self) -> GitHubClient:
        return self._github

    def _to_repo(self, row: RepoState, orgs: list[Org], manual: dict[str, Repo]) -> Repo:
        return repo_for_row(row, orgs, manual, default_include=self._profiles.default_include,
                            default_exclude=self._profiles.default_exclude)


REPOS_DIR_OWNER = ".code-index-schema"


def _claim_repos_dir(root: Path, schema: str) -> str | None:
    """Dono da pasta de clones (schema que pode apagar órfãs nela). Sem marcador, grava este schema e devolve None
    (primeira rodada: não apaga nada até a pasta ter dono)."""
    marker = root / REPOS_DIR_OWNER
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        try:
            marker.write_text(schema + "\n", encoding="utf-8")
        except OSError as e:
            log.warning("não consegui marcar %s como do schema %r: %s", root, schema, e)
        return None


def _same_source(repo: Repo, url: str | None, path: str | None) -> bool:
    """O repo avulso já registrado aponta para a mesma origem (URL igual, ou a mesma pasta depois de resolver ~ e links)."""
    if url is not None or repo.url is not None:
        return repo.url == url
    if repo.path is None or path is None:
        return False
    return Path(repo.path).expanduser().resolve() == Path(path).expanduser().resolve()


def _publisher(tracker: SyncProgress, sink: ProgressSink | None) -> Callable[[], Awaitable[None]]:
    """Publica o retrato atual; erro ao publicar vira aviso no log, nunca falha do sync."""

    async def publish() -> None:
        if sink is None:
            return
        try:
            await sink(tracker.snapshot())
        except Exception as e:  # noqa: BLE001
            log.warning("não consegui publicar o progresso do sync: %s", e)

    return publish


async def configured_repos(config: ConfigStore) -> dict[str, Repo]:
    """Repos da configuração: avulsos + um repo kind="docs" por fonte de documentação."""
    repos = {r.name: r for r in await config.repos()}
    for source in await config.docs():
        if source.repo_name in repos:
            raise ValueError(f"fonte de documentação conflita com repo: {source.repo_name}")
        repos[source.repo_name] = source.to_repo()
    return repos


def repo_for_row(row: RepoState, orgs: list[Org], manual: dict[str, Repo], *,
                 default_include: Sequence[str], default_exclude: Sequence[str]) -> Repo:
    """Repo (com pasta e filtros de arquivo) de uma linha da tabela de estado. `default_include`/`default_exclude`:
    os padrões dos perfis ativos (`ActiveProfiles.default_include`/`default_exclude`), no lugar dos padrões do
    núcleo de quem não tem filtros próprios."""
    if row.org is None:
        repo = manual.get(row.name) or Repo(name=row.name, url=row.url, path=row.path)
    else:
        org = next((o for o in orgs if o.name == row.org), None) or Org(name=row.org)
        repo = Repo(
            name=row.name,
            url=row.url,
            include=org.include,
            exclude=org.exclude,
            extra_exclude=org.extra_exclude,
        )
    return repo.with_default_include(default_include).with_default_exclude(default_exclude)
