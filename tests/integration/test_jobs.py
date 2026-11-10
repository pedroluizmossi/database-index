"""Fila durável de sync: status dos jobs (repos que falham, sem rede: remoto que recusa conexão na hora),
reserva atômica, lease vencido com retomada, limite de tentativas e encerramento limpo do worker."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


def open_service(settings: Settings) -> Any:
    from code_index.services import open_service as _open

    return _open(settings)


async def _wait(svc: Any, job_id: str) -> Any:
    """Executa o job pela fila, como o worker (embutido no `serve` ou `code-index worker`)."""
    assert await svc.run_sync_worker(once=True) == 1
    job = await svc.get_job(job_id)
    assert job.status in ("done", "failed"), job
    return job


async def test_job_em_que_todos_falham_vira_failed(int_settings: Settings, fake_llm: Any) -> None:
    async with open_service(int_settings) as svc:
        await svc.add_repo("git://127.0.0.1:1/org/quebrado.git")
        job = await svc.start_sync(None, ["org__quebrado"], batch=5, do_discover=False)
        job = await _wait(svc, job.id)
        assert job.status == "failed"
        assert job.error and job.error.startswith("todos os 1 repo(s) falharam")
        assert job.result and set(job.result["errors"]) == {"org__quebrado"}


async def test_job_parcial_continua_done_com_erros(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    ok = tmp_path / "ok"
    (ok / "src").mkdir(parents=True)
    (ok / "src" / "app.py").write_text("def ola():\n    return 1\n")
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(ok), name="ok")
        await svc.add_repo("git://127.0.0.1:1/org/quebrado.git")
        job = await svc.start_sync(None, ["ok", "org__quebrado"], batch=5, do_discover=False)
        job = await _wait(svc, job.id)
        assert job.status == "done" and job.error is None
        assert set(job.result["indexed"]) == {"ok"}
        assert set(job.result["errors"]) == {"org__quebrado"}


async def test_progresso_por_repo_durante_a_indexacao(
    int_settings: Settings, tmp_path: Path, fake_llm: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import litellm

    from code_index.indexing import orchestrator

    monkeypatch.setattr(orchestrator, "PROGRESS_INTERVAL_S", 0.05)
    fast = fake_llm.aembedding

    async def slow_embedding(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.05)  # dá tempo de ver o meio da indexação
        return await fast(*args, **kwargs)

    monkeypatch.setattr(litellm, "aembedding", slow_embedding)

    sizes = {"grande": 12, "pequeno": 5}
    for name, n in sizes.items():
        root = tmp_path / name / "src"
        root.mkdir(parents=True)
        for i in range(n):
            (root / f"m{i}.py").write_text(f"def funcao_{name}_{i}():\n    return {i}\n")

    seen: list[dict[str, Any]] = []

    async def sink(snapshot: dict[str, Any]) -> None:
        seen.append(snapshot)

    async with open_service(int_settings) as svc:
        for name in sizes:
            await svc.add_repo(str(tmp_path / name), name=name)
        res = await svc.sync(list(sizes), batch=5, do_discover=False, on_progress=sink)

    assert set(res.indexed) == set(sizes)
    phases = [s["phase"] for s in seen]
    assert phases.index("fetch") < phases.index("index") < phases.index("finish")

    final = seen[-1]
    by_name = {r["name"]: r for r in final["repos"]}
    for name, n in sizes.items():
        assert by_name[name]["status"] == "done"
        assert by_name[name]["files_total"] == n
        assert by_name[name]["files_done"] == n
    assert final["repos_done"] == final["repos_total"] == 2
    assert final["files_total"] == final["files_done"] == sum(sizes.values())

    # No meio: algum retrato com arquivos parcialmente feitos (o objetivo do recurso).
    partial = [
        r for s in seen if s["phase"] == "index" for r in s["repos"]
        if r["status"] == "indexing" and 0 < r["files_done"] < r["files_total"]
    ]
    assert partial, "nenhum retrato intermediário com arquivos parcialmente processados"


async def test_job_grava_o_progresso(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    ok = tmp_path / "ok"
    (ok / "src").mkdir(parents=True)
    for i in range(3):
        (ok / "src" / f"a{i}.py").write_text(f"def f{i}():\n    return {i}\n")
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(ok), name="ok")
        job = await svc.start_sync(None, ["ok"], batch=5, do_discover=False)
        job = await _wait(svc, job.id)
        assert job.status == "done"
        assert job.progress is not None
        repo = job.progress["repos"][0]
        assert (repo["name"], repo["files_done"], repo["files_total"]) == ("ok", 3, 3)
        assert job.to_dict()["progress"] == job.progress


# ------------------------------------------------------------------ fila: reserva, lease, tentativas


async def _set(settings: Settings, sql: str, *args: Any) -> None:
    import asyncpg

    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(sql.replace("{t}", f'"{settings.pg_schema}".sync_jobs'), *args)
    finally:
        await conn.close()


async def test_pedido_enfileira_e_recusa_segundo_ativo(int_settings: Settings, fake_llm: Any) -> None:
    async with open_service(int_settings) as svc:
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        assert job.status == "queued" and job.attempts == 0 and job.allow_register
        with pytest.raises(RuntimeError, match=f"já existe um sync na fila: job {job.id}"):
            await svc.start_sync(None, None, batch=1, do_discover=False)
        # A regra vale no banco (índice único), mesmo sem a checagem prévia do serviço.
        from code_index.infra.job_store import ActiveJobExists

        with pytest.raises(ActiveJobExists):
            await svc._jobs.create("outro", {})


async def test_reserva_atomica_so_um_worker_leva(int_settings: Settings, fake_llm: Any) -> None:
    async with open_service(int_settings) as svc:
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        got = await asyncio.gather(*(svc._jobs.lease_next(worker=f"w{i}", lease_seconds=60) for i in range(8)))
        winners = [g for g in got if g is not None]
        assert len(winners) == 1 and winners[0].id == job.id
        assert winners[0].status == "running" and winners[0].attempts == 1
        assert winners[0].lease_until is not None
        # Só o dono renova, entrega ou devolve.
        assert await svc._jobs.renew(job.id, worker="intruso", lease_seconds=60) is False
        assert await svc._jobs.renew(job.id, worker=winners[0].worker, lease_seconds=60) is True
        assert await svc._jobs.finish(job.id, status="done", result=None, error=None, worker="intruso") is False


async def test_lease_vencido_volta_para_a_fila_e_e_retomado(
    int_settings: Settings, tmp_path: Path, fake_llm: Any
) -> None:
    ok = tmp_path / "ok"
    (ok / "src").mkdir(parents=True)
    (ok / "src" / "a.py").write_text("def f():\n    return 1\n")
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(ok), name="ok")
        job = await svc.start_sync(None, ["ok"], batch=5, do_discover=False)
        leased = await svc._jobs.lease_next(worker="caiu", lease_seconds=60)  # worker que morre sem devolver
        assert leased is not None
        await _set(int_settings, "UPDATE {t} SET lease_until = now() - interval '1 second' WHERE id = $1::uuid", job.id)

        assert await svc.run_sync_worker(once=True) == 1  # expira, reserva de novo e executa
        job = await svc.get_job(job.id)
        assert job.status == "done" and job.attempts == 2
        assert set(job.result["indexed"]) == {"ok"}


async def test_limite_de_tentativas(int_settings: Settings, fake_llm: Any) -> None:
    settings = int_settings.model_copy(update={"sync_job_max_attempts": 2})
    async with open_service(settings) as svc:
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        for expected in ("queued", "failed"):
            assert await svc._jobs.lease_next(worker="caiu", lease_seconds=60) is not None
            await _set(settings, "UPDATE {t} SET lease_until = now() - interval '1 second'")
            assert await svc.recover_interrupted_jobs() == 1
            assert (await svc.get_job(job.id)).status == expected
        job = await svc.get_job(job.id)
        assert job.attempts == 2 and "2 tentativa" in job.error
        assert await svc.run_sync_worker(once=True) == 0  # nada mais na fila


async def test_erro_inesperado_tenta_de_novo_ate_o_limite(int_settings: Settings, fake_llm: Any) -> None:
    settings = int_settings.model_copy(update={"sync_job_max_attempts": 2})
    async with open_service(settings) as svc:
        calls = 0

        async def boom(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise OSError("disco cheio")

        svc.sync = boom  # type: ignore[method-assign]
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        await svc.run_sync_worker(once=True)
        job = await svc.get_job(job.id)
        assert (job.status, job.attempts) == ("queued", 1) and "disco cheio" in job.error
        await svc.run_sync_worker(once=True)
        job = await svc.get_job(job.id)
        assert job.status == "failed" and calls == 2 and "após 2 tentativa" in job.error


async def test_encerramento_limpo_devolve_o_job(int_settings: Settings, fake_llm: Any) -> None:
    settings = int_settings.model_copy(update={"sync_job_lease_seconds": 0.3})
    async with open_service(settings) as svc:
        started = asyncio.Event()
        leases: list[Any] = []

        async def slow(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await asyncio.sleep(1.0)  # mais que o lease: o heartbeat precisa renová-lo
            leases.append((await svc.get_job(job.id)).lease_until)
            await asyncio.sleep(60)

        svc.sync = slow  # type: ignore[method-assign]
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        stop = asyncio.Event()
        worker = asyncio.create_task(svc.run_sync_worker(stop, name="w-teste"))
        await asyncio.wait_for(started.wait(), 5)
        running = await svc.get_job(job.id)
        assert running.status == "running" and running.worker == "w-teste"
        await asyncio.sleep(1.2)
        assert await svc.recover_interrupted_jobs() == 0  # o heartbeat manteve o lease em dia
        assert leases and leases[0] > running.lease_until

        stop.set()  # SIGINT/SIGTERM no `code-index worker`
        assert await asyncio.wait_for(worker, 5) == 1
        job = await svc.get_job(job.id)
        assert job.status == "queued" and job.attempts == 0 and job.worker is None
        assert "voltou à fila" in job.error


async def test_trava_local_ocupada_deixa_o_job_na_fila(int_settings: Settings, fake_llm: Any) -> None:
    """Sync da CLI segurando o flock: o worker não reserva (e nem gasta tentativa)."""
    import fcntl

    async with open_service(int_settings) as svc:
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        int_settings.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(int_settings.lock_file, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert await svc.run_sync_worker(once=True) == 0
        job = await svc.get_job(job.id)
        assert (job.status, job.attempts) == ("queued", 0)
