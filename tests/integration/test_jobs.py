"""Status de jobs de sync quando os repos falham (sem rede: remoto que recusa conexão na hora)."""

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
    for _ in range(200):
        job = await svc.get_job(job_id)
        if job.status != "running":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("job não terminou")


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
