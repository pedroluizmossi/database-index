"""Laço do worker da fila de sync, com fila e trava falsas (sem banco): regra "todos falharam", trava ocupada,
retomada após erro, encerramento limpo e cancelamento (shutdown do `serve`)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from code_index.domain.models import SyncResult
from code_index.indexing.orchestrator import SyncBusy
from code_index.infra.job_store import JobRecord
from code_index.services.sync import SyncMixin
from code_index.settings import Settings


class FakeJobs:
    """Fila em memória com a mesma semântica da JobStore (reserva conta tentativa; release pode desfazer)."""

    def __init__(self, *jobs: JobRecord) -> None:
        self.jobs = {j.id: j for j in jobs}
        self.renewals = 0

    async def expire_leases(self, *, max_attempts: int) -> list[tuple[str, str]]:
        return []

    async def lease_next(self, *, worker: str, lease_seconds: float) -> JobRecord | None:
        for j in self.jobs.values():
            if j.status == "queued":
                j = replace(j, status="running", worker=worker, attempts=j.attempts + 1, error=None)
                self.jobs[j.id] = j
                return j
        return None

    async def renew(self, job_id: str, *, worker: str, lease_seconds: float) -> bool:
        self.renewals += 1
        return self.jobs[job_id].worker == worker

    async def release(self, job_id: str, *, worker: str, count_attempt: bool = False,
                      error: str | None = None) -> bool:
        j = self.jobs[job_id]
        self.jobs[job_id] = replace(j, status="queued", worker=None, error=error,
                                    attempts=j.attempts if count_attempt else max(j.attempts - 1, 0))
        return True

    async def finish(self, job_id: str, *, status: str, result: Any, error: str | None,
                     worker: str | None = None) -> bool:
        self.jobs[job_id] = replace(self.jobs[job_id], status=status, result=result, error=error, worker=None)
        return True

    async def update_progress(self, job_id: str, progress: dict[str, Any], *, worker: str | None = None) -> None:
        pass


class FakeIndexer:
    def __init__(self, busy: bool = False) -> None:
        self._busy = busy

    def busy(self) -> bool:
        return self._busy


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kw: Any) -> None:
        self.records.append(kw)


class Svc(SyncMixin):
    def __init__(self, jobs: FakeJobs, *, busy: bool = False, sync: Any = None, **settings: Any) -> None:
        self.settings = Settings(_env_file=None, sync_worker_poll_seconds=0.01, **settings)  # type: ignore[call-arg]
        self._jobs = jobs  # type: ignore[assignment]
        self._indexer = FakeIndexer(busy)  # type: ignore[assignment]
        self._audit = FakeAudit()  # type: ignore[assignment]
        self._job_wakeup = asyncio.Event()
        if sync is not None:
            self.sync = sync  # type: ignore[method-assign]


def job(**kw: Any) -> JobRecord:
    base: dict[str, Any] = dict(id="j1", requested_by="dev@example.com [bot]", status="queued",
                                params={"repos": ["a"], "batch": 3, "discover": False, "pull": True},
                                started_at=datetime.now(timezone.utc), finished_at=None, result=None, error=None,
                                allow_register=False, token_id="t1")
    return JobRecord(**{**base, **kw})


async def test_executa_o_job_com_os_parametros_guardados_e_audita() -> None:
    seen: dict[str, Any] = {}

    async def sync(names: Any, **kw: Any) -> SyncResult:
        seen.update(kw, names=names)
        res = SyncResult()
        res.selected, res.indexed = ["a"], {"a": "abc"}
        return res

    jobs = FakeJobs(job())
    svc = Svc(jobs, sync=sync)
    assert await svc.run_sync_worker(once=True) == 1
    assert jobs.jobs["j1"].status == "done"
    assert (seen["names"], seen["batch"], seen["do_discover"], seen["allow_register"]) == (["a"], 3, False, False)
    assert svc._audit.records == [{"actor": "dev@example.com [bot]", "token_id": "t1", "action": "sync:job",  # type: ignore[attr-defined]
                                   "params": {"job": "j1", "status": "done"}}]


async def test_todos_falharam_vira_failed_sem_nova_tentativa() -> None:
    async def sync(names: Any, **kw: Any) -> SyncResult:
        res = SyncResult()
        res.selected, res.errors = ["a"], {"a": "clone recusado"}
        return res

    jobs = FakeJobs(job())
    assert await Svc(jobs, sync=sync).run_sync_worker(once=True) == 1
    assert jobs.jobs["j1"].status == "failed"
    assert (jobs.jobs["j1"].error or "").startswith("todos os 1 repo(s) falharam")


async def test_trava_ocupada_nao_reserva() -> None:
    jobs = FakeJobs(job())
    assert await Svc(jobs, busy=True).run_sync_worker(once=True) == 0
    assert jobs.jobs["j1"].status == "queued"


async def test_trava_tomada_depois_da_reserva_devolve_sem_gastar_tentativa() -> None:
    async def sync(names: Any, **kw: Any) -> SyncResult:
        raise SyncBusy("já existe um sync em andamento")

    jobs = FakeJobs(job())
    await Svc(jobs, sync=sync).run_sync_worker(once=True)
    assert (jobs.jobs["j1"].status, jobs.jobs["j1"].attempts) == ("queued", 0)


async def test_pedido_invalido_falha_sem_repetir() -> None:
    async def sync(names: Any, **kw: Any) -> SyncResult:
        raise ValueError("repositório desconhecido: x")

    jobs = FakeJobs(job())
    await Svc(jobs, sync=sync).run_sync_worker(once=True)
    assert jobs.jobs["j1"].status == "failed" and jobs.jobs["j1"].error == "repositório desconhecido: x"


async def test_parar_sem_job_encerra_logo() -> None:
    stop = asyncio.Event()
    stop.set()
    assert await Svc(FakeJobs()).run_sync_worker(stop) == 0


async def test_cancelar_o_worker_devolve_o_job_e_o_heartbeat_renova() -> None:
    started = asyncio.Event()

    async def sync(names: Any, **kw: Any) -> SyncResult:
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("não chega aqui")

    jobs = FakeJobs(job())
    svc = Svc(jobs, sync=sync, sync_job_lease_seconds=0.06)
    task = asyncio.create_task(svc.run_sync_worker())
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.sleep(0.1)
    task.cancel()  # shutdown do `serve`
    await asyncio.gather(task, return_exceptions=True)
    j = jobs.jobs["j1"]
    assert (j.status, j.attempts, j.worker) == ("queued", 0, None)
    assert "voltou à fila" in (j.error or "")
    assert jobs.renewals >= 1
    assert svc._audit.records == []  # type: ignore[attr-defined]
