"""Sync (indexação), jobs de sync da API e recálculo das relações."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal


from ..domain.auth import ADMIN, Principal
from ..domain.models import SyncResult
from ..indexing.orchestrator import ProgressSink
from ..infra.job_store import JobRecord
from .enrich import EnrichMixin
from .apis import ApisMixin


log = logging.getLogger(__name__)


class SyncMixin(ApisMixin, EnrichMixin):
    """Sync e jobs. Depois de indexar, recalcula fatos dos repos alterados e as relações (sem custo de API)."""

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

    async def after_index(self) -> dict[str, Any]:
        """Fatos dos repos cuja revisão mudou + catálogo de documentos + relações derivadas."""
        facts = await self.enrich_facts(rebuild=False)
        return {"facts": facts, "relations": await self.rebuild_relations()}

    async def start_sync(
        self,
        principal: Principal | None,
        names: list[str] | None,
        *,
        batch: int,
        do_discover: bool = True,
        pull: bool = True,
        openapi_domain: str | None = None,
        openapi_services: list[str] | None = None,
    ) -> JobRecord:
        """Inicia uma indexação em segundo plano e devolve o job (já com id). Um por vez.

        Levanta RuntimeError se já houver um job rodando.
        """
        actor = principal.actor if principal else "operador local"
        params: dict[str, Any] = {"repos": names, "batch": batch, "discover": do_discover, "pull": pull}
        async with self._job_lock:
            running = await self._jobs.running()
            if running is not None:
                raise RuntimeError(f"já existe um sync em andamento: job {running.id}")
            if openapi_domain is not None:
                if principal is not None and not principal.has(ADMIN):
                    raise PermissionError("só administradores registram fontes")
                names = [await self.register_openapi(openapi_domain, openapi_services)]
                params["repos"] = names
                params["openapi_domain"] = openapi_domain
                params["openapi_services"] = openapi_services
            job_id = await self._jobs.create(actor, params)
            only = frozenset(openapi_services) if openapi_services is not None else None
            task = asyncio.create_task(self._run_job(job_id, principal, names, batch, do_discover, pull, only))
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
        doc_pages: frozenset[str] | None = None,
    ) -> None:
        status, result, error = "failed", None, None
        last: list[dict[str, Any]] = []

        async def save_progress(snapshot: dict[str, Any]) -> None:
            # Só grava quando algo mudou além do relógio (evita escrever no banco a cada leitura).
            key = {k: v for k, v in snapshot.items() if k != "seconds"}
            key["repos"] = [{k: v for k, v in r.items() if k != "seconds"} for r in snapshot["repos"]]
            if last and last[0] == key:
                return
            last[:] = [key]
            await self._jobs.update_progress(job_id, snapshot)

        try:
            res = await self.sync(
                names, batch=batch, do_discover=do_discover, pull=pull, progress=False,
                allow_register=principal is None or principal.has(ADMIN),
                doc_pages=doc_pages, on_progress=save_progress,
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

