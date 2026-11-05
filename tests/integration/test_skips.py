"""Arquivos descartados (tamanho, binário) entram em skipped_files e saem quando deixam de ser descartados."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


async def test_descartados_aparecem_e_somem(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    project = tmp_path / "meu-projeto"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("def ola():\n    return 'oi'\n", encoding="utf-8")
    (project / "dados.py").write_bytes(b"texto\x00\x00binario" * 20)  # extensão de código, conteúdo binário
    (project / "grande.py").write_text("x = 1\n" * 100_000, encoding="utf-8")  # ~600 KB > 512 KB

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(project), name="meu-projeto")
        res = await svc.sync(["meu-projeto"], batch=None, do_discover=False, progress=False)
        assert res.errors == {}

        descartados = {(r["path"], r["reason"]) for r in await svc.skipped_files("meu-projeto")}
        assert descartados == {("dados.py", "binário"), ("grande.py", "tamanho")}
        assert {r.name: r for r in await svc.list_repos()}["meu-projeto"].skipped == 2

        # O arquivo grande sai do projeto: a linha de descartado some no próximo sync.
        (project / "grande.py").unlink()
        await svc.sync(["meu-projeto"], batch=None, do_discover=False, progress=False)
        assert {r["path"] for r in await svc.skipped_files("meu-projeto")} == {"dados.py"}
