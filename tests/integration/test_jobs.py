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
