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
    assert job.status in ("done", "failed", "stopped"), job
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


async def test_fila_com_varios_pedidos_teto_repetido_e_cancelamento(
    int_settings: Settings, tmp_path: Path, fake_llm: Any
) -> None:
    from code_index.infra.job_store import AlreadyQueued, QueueFull

    for name in ("a", "b"):
        (tmp_path / name / "src").mkdir(parents=True)
        (tmp_path / name / "src" / "x.py").write_text("def f():\n    return 1\n")
    settings = int_settings.model_copy(update={"sync_queue_max": 3})
    async with open_service(settings) as svc:
        await svc.add_repo(str(tmp_path / "a"), name="a")
        await svc.add_repo(str(tmp_path / "b"), name="b")
        first = await svc.start_sync(None, None, batch=1, do_discover=False)
        assert first.status == "queued" and first.attempts == 0 and first.allow_register
        second = await svc.start_sync(None, ["a"], batch=5, do_discover=False)   # enfileira atrás do primeiro
        with pytest.raises(AlreadyQueued, match="a"):                           # repo já num job da fila
            await svc.start_sync(None, ["a", "b"], batch=5, do_discover=False)
        third = await svc.start_sync(None, ["b"], batch=5, do_discover=False)
        with pytest.raises(QueueFull):                                          # teto da fila (3)
            await svc.start_sync(None, None, batch=1, do_discover=False)
        with pytest.raises(QueueFull):                                          # vale no banco também
            await svc._jobs.create("outro", {}, max_queued=3)

        canceled = await svc.cancel_job(None, second.id)
        assert canceled.status == "canceled" and "cancelado" in (canceled.error or "")
        assert (await svc._jobs.active()).id == first.id                        # o mais antigo primeiro

        # Um rodando por vez: com o primeiro reservado, ninguém leva o terceiro.
        leased = await svc._jobs.lease_next(worker="w1", lease_seconds=60)
        assert leased is not None and leased.id == first.id
        assert await svc._jobs.lease_next(worker="w2", lease_seconds=60) is None
        assert (await svc._jobs.active()).id == first.id                        # rodando vem antes da fila
        with pytest.raises(RuntimeError, match="não está na fila"):             # rodando não cancela
            await svc.cancel_job(None, first.id)
        await svc._jobs.finish(first.id, status="done", result=None, error=None, worker="w1")
        nxt = await svc._jobs.lease_next(worker="w2", lease_seconds=60)
        assert nxt is not None and nxt.id == third.id


def _projetos(tmp_path: Path, *names: str) -> None:
    for name in names:
        (tmp_path / name / "src").mkdir(parents=True)
        (tmp_path / name / "src" / "x.py").write_text(f"def {name}():\n    return 1\n")


async def test_parar_job_rodando_mantem_o_que_terminou(int_settings: Settings, tmp_path: Path, fake_llm: Any,
                                                       monkeypatch: Any) -> None:
    _projetos(tmp_path, "a", "b")
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(tmp_path / "a"), name="a")
        await svc.add_repo(str(tmp_path / "b"), name="b")
        job = await svc.start_sync(None, ["a", "b"], batch=5, do_discover=False)
        sync_git = svc._indexer._git.sync

        def git_sync_que_para(repo: Any) -> Any:
            # o pedido chega durante o download do primeiro repo: o segundo nem é baixado
            svc._indexer.request_stop("pedido por ana@example.com")
            return sync_git(repo)

        monkeypatch.setattr(svc._indexer._git, "sync", git_sync_que_para)
        job = await _wait(svc, job.id)
        assert job.status == "stopped"
        assert job.error.startswith("parado por ana@example.com: 0 de 2 repo(s) concluído(s)")
        assert set(job.result["not_done"]) == {"a", "b"} and job.result["stopped"]
        rows = {r.name: r for r in await svc.list_repos()}
        assert rows["a"].status == "pending" and rows["a"].error is None   # não vira erro: só não terminou


async def test_pedido_de_parada_pela_api_e_parar_na_fila_cancela(int_settings: Settings, fake_llm: Any) -> None:
    async with open_service(int_settings) as svc:
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        assert (await svc.stop_job(None, job.id)).status == "canceled"          # na fila: sai da fila
        job2 = await svc.start_sync(None, None, batch=1, do_discover=False)
        leased = await svc._jobs.lease_next(worker="w", lease_seconds=60)
        assert leased is not None and leased.heartbeat_at is not None
        stopped = await svc.stop_job(None, job2.id)                             # rodando: pedido gravado
        assert stopped.stop_requested_at is not None and stopped.stop_requested_by == "operador local"
        assert await svc._jobs.stop_requested(job2.id) == "operador local"


async def test_atualizacao_do_sistema_devolve_o_resto_a_fila(int_settings: Settings, tmp_path: Path, fake_llm: Any,
                                                              monkeypatch: Any) -> None:
    _projetos(tmp_path, "a", "b")
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(tmp_path / "a"), name="a")
        await svc.add_repo(str(tmp_path / "b"), name="b")
        job = await svc.start_sync(None, ["a", "b"], batch=5, do_discover=False)
        sync_git = svc._indexer._git.sync

        def git_sync_no_deploy(repo: Any) -> Any:
            svc._draining = True
            svc._indexer.request_stop("atualização do sistema (o worker está reiniciando)")
            return sync_git(repo)

        monkeypatch.setattr(svc._indexer._git, "sync", git_sync_no_deploy)
        assert await svc.run_sync_worker(once=True) == 1
        svc._draining = False
        back = await svc.get_job(job.id)
        assert back.status == "queued" and back.attempts == 0                    # não conta tentativa
        assert "atualização do sistema" in back.error and back.params["repos"] == ["a", "b"]


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
    # o sync falso ignora o pedido de parada: passada a drenagem, cai no cancelamento (o job inteiro volta)
    settings = int_settings.model_copy(update={"sync_job_lease_seconds": 0.3, "sync_worker_drain_seconds": 0.5})
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
    """Sync da CLI (ou de outro pod) segurando a trava do sync: o worker não reserva (e nem gasta tentativa)."""
    import asyncpg

    async with open_service(int_settings) as svc:
        job = await svc.start_sync(None, None, batch=1, do_discover=False)
        other = await asyncpg.connect(int_settings.database_url, timeout=5)
        try:
            assert await other.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                                        f"code_index.sync.{int_settings.pg_schema}")
            assert await svc.run_sync_worker(once=True) == 0
        finally:
            await other.close()
        job = await svc.get_job(job.id)
        assert (job.status, job.attempts) == ("queued", 0)
