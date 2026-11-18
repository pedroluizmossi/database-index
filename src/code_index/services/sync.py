"""Sync (indexação), fila durável de jobs de sync (pedido pela API, executado pelo worker) e recálculo
das relações."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Coroutine, Sequence
from typing import TYPE_CHECKING, Any, Literal

from ..domain.auth import ADMIN, Principal
from ..domain.models import SyncResult
from ..indexing.orchestrator import ProgressSink, SyncBusy
from ..infra.job_store import ActiveJobExists, JobRecord
from .enrich import EnrichMixin
from .apis import ApisMixin
from .base import ScanBusy
from .graph import GraphMixin
from .vectors import VectorsMixin, _now
from .tests import TestsMixin
from .wiki import WIKI_TYPE


log = logging.getLogger(__name__)


class ScanFailed(Exception):
    """A leitura pedida com wait=true falhou (erro interno: vira 500, o detalhe fica no log e no status)."""


def worker_id(role: str) -> str:
    """Identificação do worker no lease: papel, host, pid e um sufixo único (dois workers no mesmo pid)."""
    return f"{role}@{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class SyncMixin(VectorsMixin, ApisMixin, EnrichMixin, TestsMixin, GraphMixin):
    """Sync e jobs. Depois de indexar, recalcula fatos dos repos alterados e as relações (sem custo de API)."""

    if TYPE_CHECKING:  # WikiMixin (a fachada junta os dois; herdar daqui quebraria a MRO)
        async def index_doc_pages(self, name: str, pages: Sequence[str]) -> SyncResult: ...

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
        doc_pages: frozenset[str] | None = None,
        on_progress: ProgressSink | None = None,
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
            doc_pages=doc_pages,
            on_progress=on_progress,
        )
        if not dry_run:
            await self._ensure_search_indexes()
            await self.after_index()
        return result

    # ------------------------------------------------------------ leituras dos clones (sem custo de API)

    SCAN_KINDS = ("graph", "tests")
    _scan_tasks: dict[str, asyncio.Task[Any]] | None = None
    _scan_runs: dict[str, dict[str, Any]] | None = None

    async def scans_status(self) -> dict[str, Any]:
        """Grafo de chamadas e casos de teste lidos dos clones: números, se uma leitura está rodando neste
        processo e o resultado da última pedida pela API."""
        tasks, runs = self._scan_tasks or {}, self._scan_runs or {}
        out: dict[str, Any] = {}
        for kind, status in (("graph", await self.graph_status()), ("tests", await self.tests_status())):
            task = tasks.get(kind)
            out[kind] = {**status, "running": task is not None and not task.done(), "last_run": runs.get(kind)}
        return out

    async def start_scan(self, kind: str, principal: Principal | None, *, force: bool = False,
                         repos: list[str] | None = None) -> dict[str, Any]:
        """Relê o grafo de chamadas ou os casos de teste em segundo plano (sem custo de API). Uma leitura de cada
        tipo por vez neste processo (RuntimeError se já houver). `force`: relê mesmo os repos sem mudança;
        `repos`: só estes (padrão: todos os de código indexados)."""
        if kind not in self.SCAN_KINDS:
            raise ValueError(f"leitura desconhecida: {kind} (use {', '.join(self.SCAN_KINDS)})")
        if self._scan_tasks is None:
            self._scan_tasks, self._scan_runs = {}, {}
        assert self._scan_runs is not None
        running = self._scan_tasks.get(kind)
        if running is not None and not running.done():
            raise RuntimeError("já existe uma leitura deste tipo em andamento")
        actor = principal.actor if principal else "operador local"
        if repos:
            await self._resolve_repos(repos)  # nome desconhecido falha já (ValueError), não dentro da tarefa
        run: dict[str, Any] = {"requested_by": actor, "force": force, "repos": repos or None, "started_at": _now(),
                               "finished_at": None, "status": "running", "result": None, "error": None}
        self._scan_runs[kind] = run

        async def go() -> None:
            try:
                run["result"] = await (self.scan_calls(repos or None, force=force) if kind == "graph"
                                       else self.scan_tests(repos or None, force=force))
                run["status"] = "done"
            except ScanBusy as e:  # outra réplica (ou o fim de um sync) está lendo: não é falha da leitura
                run["status"], run["error"] = "busy", str(e)
            except Exception as e:  # noqa: BLE001 - o erro vai para o estado, não derruba o servidor
                log.exception("leitura %s falhou", kind)
                run["status"], run["error"] = "failed", str(e)[:500]
            finally:
                run["finished_at"] = _now()
            await self._audit.record(actor=actor, token_id=principal.token_id if principal else None,
                                     action=f"scan:{kind}",
                                     params={"status": run["status"], "force": force, "repos": repos or None})

        task = asyncio.create_task(go())
        self._scan_tasks[kind] = task
        self._job_tasks.add(task)
        task.add_done_callback(self._job_tasks.discard)
        return await self.scans_status()

    async def run_scan(self, kind: str, principal: Principal | None, *, force: bool = False,
                       repos: list[str] | None = None) -> dict[str, int]:
        """Como `start_scan`, mas espera o fim e devolve o resultado (o `wait=true` da API). Usa a mesma guarda:
        RuntimeError se já houver uma leitura deste tipo em andamento; a falha da leitura vira RuntimeError."""
        await self.start_scan(kind, principal, force=force, repos=repos)
        assert self._scan_tasks is not None and self._scan_runs is not None
        await asyncio.shield(self._scan_tasks[kind])  # a conexão do cliente cair não interrompe a leitura
        run = self._scan_runs[kind]
        if run["status"] == "busy":
            raise ScanBusy(run["error"])
        if run["status"] != "done":
            raise ScanFailed(f"leitura {kind} falhou: {run['error']}")
        return dict(run["result"])

    async def after_index(self) -> dict[str, Any]:
        """Fatos dos repos cuja revisão mudou + catálogo de documentos + relações derivadas + resumos de
        arquivos de volta nas colunas de busca (o sync regrava os trechos que reprocessa) + casos de teste e grafo de
        chamadas dos repos que mudaram (lidos dos clones, sem embedding)."""
        facts = await self.enrich_facts(rebuild=False)
        out = {"facts": facts, "relations": await self.rebuild_relations(),
               "summaries": await self.apply_file_summaries()}
        try:
            out["tests"] = await self.scan_tests()
        except Exception:  # noqa: BLE001 - leitura dos testes é um extra: não derruba o sync
            log.exception("leitura dos casos de teste falhou")
        try:
            out["vectors"] = await self.maintain_vectors()   # obsoletos saem; geração só com teto configurado
        except Exception:  # noqa: BLE001 - os vetores extras são um extra: não derrubam o sync
            log.exception("manutenção dos vetores extras falhou")
        try:
            out["calls"] = await self.scan_calls()
        except Exception:  # noqa: BLE001 - o grafo de chamadas é um extra: não derruba o sync
            log.exception("leitura do grafo de chamadas falhou")
        return out

    async def start_sync(
        self,
        principal: Principal | None,
        names: list[str] | None,
        *,
        batch: int | None,
        do_discover: bool = True,
        pull: bool = True,
        full: bool = False,
        doc_pages: list[str] | None = None,
        openapi_domain: str | None = None,
        openapi_services: list[str] | None = None,
    ) -> JobRecord:
        """Enfileira uma indexação e devolve o job (`queued`, já com id). Quem executa é o worker
        (`run_sync_worker`: embutido no `serve` ou o `code-index worker`). Um job ativo por vez.

        `batch` None = sem limite de lote (o `--all` da CLI). `full`: reprocessa ignorando caches (caro; só
        admin). `doc_pages`: só estas páginas (sem idioma) de UMA fonte de documentação (o `--page` da CLI).
        Levanta RuntimeError (ActiveJobExists) se já houver um job na fila ou rodando.
        """
        actor = principal.actor if principal else "operador local"
        params: dict[str, Any] = {"repos": names, "batch": batch, "all": batch is None,
                                  "discover": do_discover, "pull": pull}
        if full:
            if principal is not None and not principal.has(ADMIN):
                raise PermissionError("só administradores pedem reprocessamento completo (full): ignora os caches")
            params["full"] = True
        if batch is None and not names and doc_pages is None and openapi_domain is None:
            # Lote automático sem teto (o --all): tão caro quanto o full; quem não é admin pede um lote limitado.
            if principal is not None and not principal.has(ADMIN):
                raise PermissionError("só administradores pedem o lote sem limite (all); informe um lote (batch)")
        if doc_pages is not None:
            if openapi_domain is not None or full:
                raise ValueError("páginas avulsas não combinam com full nem com OpenAPI")
            if not names or len(names) != 1:
                raise ValueError("páginas avulsas exigem exatamente uma fonte de documentação, ex.: docs__wiki")
            source = await self._doc_source(names[0])
            if source.type != WIKI_TYPE:
                raise ValueError("indexação por página só existe para wikis")
            pages = sorted({p.strip().strip("/") for p in doc_pages if p.strip().strip("/")})
            if not pages or any(".." in p.split("/") for p in pages):
                raise ValueError("informe caminhos de página válidos, ex.: Plataforma/Email")
            params["repos"], params["doc_pages"] = [source.repo_name], pages
        active = await self._jobs.active()
        if active is not None:  # antes de registrar fonte nova (a criação abaixo também recusa, atomicamente)
            raise ActiveJobExists(active.id, active.status)
        if openapi_domain is not None:
            if principal is not None and not principal.has(ADMIN):
                raise PermissionError("só administradores registram fontes")
            params["repos"] = [await self.register_openapi(openapi_domain, openapi_services)]
            params["openapi_domain"] = openapi_domain
            params["openapi_services"] = openapi_services
        # O worker não tem o Principal: guarda só a decisão (admin registra repo desconhecido) e o id do token.
        job_id = await self._jobs.create(
            actor, params, allow_register=principal is None or principal.has(ADMIN),
            token_id=principal.token_id if principal else None,
        )
        self._job_wakeup.set()  # acorda o worker embutido, se houver
        job = await self._jobs.get(job_id)
        assert job is not None
        return job

    # ------------------------------------------------------------ worker da fila

    def start_background_worker(self) -> None:
        """Worker embutido (o `serve` com `sync_worker_embedded`): o mesmo laço do `code-index worker`.
        `shutdown()` o cancela e devolve à fila o job em andamento."""
        task = asyncio.create_task(self.run_sync_worker(name=worker_id("serve")))
        self._job_tasks.add(task)
        task.add_done_callback(self._job_tasks.discard)

    async def run_sync_worker(self, stop: asyncio.Event | None = None, *, name: str | None = None,
                              once: bool = False) -> int:
        """Laço do worker: devolve à fila jobs de lease vencido, reserva o próximo, executa renovando o lease.

        `stop` encerra limpo: o job em andamento é interrompido e volta à fila (sem gastar tentativa).
        Cancelar a task tem o mesmo efeito. `once`: tenta um job só e retorna (testes e scripts).
        Devolve quantos jobs reservou.
        """
        stop = stop if stop is not None else asyncio.Event()
        worker = name or worker_id("worker")
        ran = 0
        log.info("worker de sync %s aguardando jobs", worker)
        while not stop.is_set():
            job: JobRecord | None = None
            try:
                await self.recover_interrupted_jobs()
                # Trava local ocupada (sync da CLI, watch): não reserva; o job espera na fila.
                if not await self._indexer.busy():
                    job = await self._jobs.lease_next(worker=worker,
                                                      lease_seconds=self.settings.sync_job_lease_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - banco fora do ar etc.: tenta de novo depois
                log.exception("worker de sync: falha ao consultar a fila")
            if job is not None:
                ran += 1
                await self._run_until_stop(self._execute_job(job, worker), stop)
            if once:
                break
            if job is None:
                await self._wait_for_work(stop)
        log.info("worker de sync %s encerrado", worker)
        return ran

    @staticmethod
    async def _run_until_stop(coro: Coroutine[Any, Any, None], stop: asyncio.Event) -> None:
        """Roda `coro` até terminar; se `stop` disparar (ou esta task for cancelada), cancela e espera."""
        task = asyncio.ensure_future(coro)
        stopper = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopper.cancel()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _wait_for_work(self, stop: asyncio.Event) -> None:
        waiters = {asyncio.ensure_future(self._job_wakeup.wait()), asyncio.ensure_future(stop.wait())}
        try:
            await asyncio.wait(waiters, timeout=self.settings.sync_worker_poll_seconds,
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
            self._job_wakeup.clear()

    async def _heartbeat(self, job_id: str, worker: str) -> None:
        lease = self.settings.sync_job_lease_seconds
        while True:
            await asyncio.sleep(lease / 3)
            try:
                if not await self._jobs.renew(job_id, worker=worker, lease_seconds=lease):
                    log.error("job de sync %s: lease perdido (outro worker pode retomá-lo)", job_id)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - tenta de novo no próximo intervalo (o lease ainda vale)
                log.warning("job de sync %s: falha ao renovar o lease", job_id, exc_info=True)

    async def _execute_job(self, job: JobRecord, worker: str) -> None:
        """Executa um job reservado. Retomar = reexecutar com os mesmos parâmetros (o sync é incremental)."""
        params = job.params
        services = params.get("openapi_services")
        last: list[dict[str, Any]] = []

        async def save_progress(snapshot: dict[str, Any]) -> None:
            # Só grava quando algo mudou além do relógio (evita escrever no banco a cada leitura).
            key = {k: v for k, v in snapshot.items() if k != "seconds"}
            key["repos"] = [{k: v for k, v in r.items() if k != "seconds"} for r in snapshot["repos"]]
            if last and last[0] == key:
                return
            last[:] = [key]
            await self._jobs.update_progress(job.id, snapshot, worker=worker)

        status, result, error = "failed", None, None
        heartbeat = asyncio.create_task(self._heartbeat(job.id, worker))
        try:
            batch = params.get("batch")
            pages = params.get("doc_pages")
            if pages:  # páginas avulsas de uma wiki: o mesmo caminho do `code-index index docs__x --page`
                res = await self.index_doc_pages(params["repos"][0], pages)
            else:
                res = await self.sync(
                    params.get("repos"),
                    batch=None if params.get("all") else (self.settings.default_batch if batch is None else int(batch)),
                    do_discover=bool(params.get("discover", True)), pull=bool(params.get("pull", True)),
                    full=bool(params.get("full", False)), progress=False, allow_register=job.allow_register,
                    doc_pages=frozenset(services) if services is not None else None, on_progress=save_progress,
                )
            status, result = "done", res.to_dict()
            if res.all_failed:
                # Nada foi indexado e todos os selecionados falharam: o job falhou, não "concluiu".
                status = "failed"
                error = f"todos os {len(res.selected)} repo(s) falharam; ex.: {res.first_error()[:300]}"
        except SyncBusy:
            # Outro sync pegou a trava entre a sonda e a reserva: não conta tentativa, espera na fila.
            await self._jobs.release(job.id, worker=worker)
            return
        except asyncio.CancelledError:
            await self._jobs.release(job.id, worker=worker,
                                     error="interrompido: o worker foi encerrado; o job voltou à fila")
            log.warning("job de sync %s interrompido e devolvido à fila", job.id)
            raise
        except (ValueError, PermissionError) as e:  # pedido inválido: repetir não muda o resultado
            error = str(e)
        except Exception as e:  # noqa: BLE001 - o erro vai para o job, não derruba o worker
            log.exception("job de sync %s falhou (tentativa %d)", job.id, job.attempts)
            if job.attempts < self.settings.sync_job_max_attempts:
                await self._jobs.release(job.id, worker=worker, count_attempt=True,
                                         error=f"tentativa {job.attempts} falhou: {str(e)[:500]}")
                return
            error = f"falhou após {job.attempts} tentativa(s): {e}"
        finally:
            heartbeat.cancel()
        if not await self._jobs.finish(job.id, status=status, result=result, error=error, worker=worker):
            log.error("job de sync %s terminou, mas o lease já era de outro worker: resultado descartado", job.id)
            return
        await self._audit.record(actor=job.requested_by, token_id=job.token_id, action="sync:job",
                                 params={"job": job.id, "status": status})

    async def recover_interrupted_jobs(self) -> int:
        """Jobs `running` cujo lease venceu (o worker caiu, o servidor reiniciou) voltam à fila; os que já
        usaram `sync_job_max_attempts` tentativas viram `failed`. Seguro em qualquer processo: só toca em
        lease vencido. O worker chama a cada volta do laço; o servidor, também ao subir."""
        changed = await self._jobs.expire_leases(max_attempts=self.settings.sync_job_max_attempts)
        for job_id, status in changed:
            if status == "queued":
                log.warning("job de sync %s: lease vencido, voltou à fila para ser retomado", job_id)
            else:
                log.error("job de sync %s: lease vencido e tentativas esgotadas, marcado como falho", job_id)
        return len(changed)

    async def get_job(self, job_id: str) -> JobRecord | None:
        return await self._jobs.get(job_id)

    async def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        return await self._jobs.recent(limit)

