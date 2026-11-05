"""Exclusão na busca: repos e caminhos que não podem aparecer no resultado (sem embedding extra)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


async def test_exclui_repo_e_caminho(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    a = tmp_path / "repo-a"
    (a / "src").mkdir(parents=True)
    (a / "old").mkdir()
    (a / "src" / "pagamento.py").write_text("def cobrar_cliente(valor):\n    return valor\n", encoding="utf-8")
    (a / "old" / "pagamento_antigo.py").write_text("def cobrar_cliente(valor):\n    return valor * 2\n", encoding="utf-8")
    b = tmp_path / "repo-b"
    b.mkdir()
    (b / "cobranca.py").write_text("def cobrar_cliente(valor):\n    return valor + 1\n", encoding="utf-8")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(a), name="repo-a")
        await svc.add_repo(str(b), name="repo-b")
        await svc.sync(None, batch=None, do_discover=False, progress=False)

        base = await svc.search("cobrar cliente", mode="keyword", rerank=False, top_k=10)
        assert {h.repo for h in base} == {"repo-a", "repo-b"}
        assert any(h.path == "old/pagamento_antigo.py" for h in base)

        sem_b = await svc.search("cobrar cliente", mode="keyword", rerank=False, top_k=10, exclude_repos=["repo-b"])
        assert sem_b and {h.repo for h in sem_b} == {"repo-a"}

        sem_old = await svc.search("cobrar cliente", mode="keyword", rerank=False, top_k=10,
                                   exclude_path_glob=["**/old/**"])
        assert sem_old and all(not h.path.startswith("old/") for h in sem_old)
        assert {h.repo for h in sem_old} == {"repo-a", "repo-b"}

        with pytest.raises(ValueError, match="repositório desconhecido"):
            await svc.search("cobrar cliente", mode="keyword", rerank=False, exclude_repos=["nao-existe"])
