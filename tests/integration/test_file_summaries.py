"""Resumos de arquivos: fila next/submit, validação, aplicação nas colunas de busca e reaplicação após o sync."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from code_index.domain.file_summary import MARK
from code_index.settings import Settings

pytestmark = pytest.mark.integration


def _project(base: Path) -> Path:
    root = base / "portal"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Portal\nTela de login.\n", encoding="utf-8")
    (root / "src" / "browser_version.py").write_text(
        "def browser_version(user_agent):\n    return user_agent.split('/')[-1]\n", encoding="utf-8")
    (root / "src" / "shade.py").write_text("def shade(color, percent):\n    return color\n", encoding="utf-8")
    return root


def _delivery(paths: list[str], **override: Any) -> str:
    texts = {
        "src/browser_version.py": ("Detecta o navegador e a versão a partir do cabeçalho enviado pelo cliente.",
                                   ["navegador", "versão mínima", "compatibilidade"]),
        "src/shade.py": ("Clareia ou escurece uma cor conforme um percentual informado.",
                         ["cor", "contraste", "tonalidade"]),
    }
    files = [{"path": p, "resumo": texts[p][0], "termos": texts[p][1],
              "perguntas": ["Como funciona?", "Quando é usado?", "O que devolve?"], **override} for p in paths]
    return json.dumps({"files": files})


async def test_fila_de_resumos_e_busca(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    table = int_settings.chunks_table_qualified
    async with open_service(int_settings) as svc:
        root = _project(tmp_path)
        await svc.add_repo(str(root), name="portal")
        res = await svc.sync(["portal"], batch=None, do_discover=False, progress=False)
        assert res.errors == {}
        pool = svc._chunks.pool

        async def symbols(path: str) -> str:
            return await pool.fetchval(f"SELECT search_symbols FROM {table} WHERE path = $1 LIMIT 1", path)

        assert (await svc.enrich_status())["summaries"] == {"files": 2, "fresh": 0, "stale": 0, "missing": 2}
        base = await symbols("src/browser_version.py")
        assert not await svc.search("navegador", mode="keyword", rerank=False)   # português não casa com o código

        task = await svc.enrich_next(kind="file_summaries", agent="teste", repo="portal")
        assert task is not None and task["files"] == ["src/browser_version.py", "src/shade.py"]   # README fica de fora
        assert "def browser_version(user_agent)" in task["text"] and task["remaining"] == 0
        assert await svc.enrich_next(kind="file_summaries") is None           # lote reservado não sai de novo

        with pytest.raises(ValueError) as err:
            await svc.enrich_submit(task["id"], _delivery(["src/shade.py"], resumo="curto"))
        assert "resumo: texto de" in str(err.value) and "faltam arquivos: src/browser_version.py" in str(err.value)

        ok = await svc.enrich_submit(task["id"], _delivery(task["files"]))
        assert ok["kind"] == "file_summaries" and ok["files"] == 2 and ok["chunks"] >= 2 and ok["remaining"] == 0
        after = await symbols("src/browser_version.py")
        assert after.startswith(base + f" {MARK} ") and "navegador" in after and "versao" in after
        hits = await svc.search("navegador", mode="keyword", rerank=False)
        assert hits and hits[0].path == "src/browser_version.py"
        assert MARK not in hits[0].code                                       # o código devolvido não muda
        assert hits[0].summary and hits[0].summary.startswith("Detecta o navegador")   # resumo acompanha o resultado
        assert hits[0].summary_keywords == ["navegador", "versão mínima", "compatibilidade"]
        info = await svc.file_summary("portal", "src/browser_version.py")
        assert info is not None and info["stale"] is False and len(info["questions"]) == 3 and info["agent"] == "teste"
        assert await svc.file_summary("portal", "README.md") is None
        assert [r["path"] for r in await svc.repo_summaries("portal")] == ["src/browser_version.py", "src/shade.py"]
        files = {f["path"]: f for f in await svc.repo_files("portal")}
        assert sorted(files) == ["README.md", "src/browser_version.py", "src/shade.py"]
        assert files["README.md"]["summarizable"] is False and files["README.md"]["summary"] is None
        assert files["src/shade.py"]["summary"].startswith("Clareia") and files["src/shade.py"]["stale"] is False
        assert files["src/shade.py"]["lines"] >= 2 and files["src/shade.py"]["language"] == "python"
        cov = await svc.summary_coverage()
        assert (cov["files"], cov["fresh"], cov["missing"]) == (2, 2, 0)
        assert cov["repos"] == [{"repo": "portal", "files": 2, "fresh": 2, "stale": 0}]
        assert (await svc.enrich_status())["summaries"]["fresh"] == 2
        with pytest.raises(ValueError, match="já entregue"):
            await svc.enrich_submit(task["id"], _delivery(task["files"]))

        # reaplicar não acumula; e o sync (que regrava os trechos) devolve os resumos às colunas
        assert await svc.apply_file_summaries() == {"pruned": 0, "chunks": 0}
        await pool.execute(f"UPDATE {table} SET search_symbols = $1 WHERE path = 'src/browser_version.py'", base)
        assert (await svc.apply_file_summaries())["chunks"] >= 1
        assert await symbols("src/browser_version.py") == after

        # arquivo alterado: o resumo antigo continua valendo na busca e o arquivo volta para a fila
        (root / "src" / "shade.py").write_text("def shade(color, percent):\n    return color.upper()\n", encoding="utf-8")
        res = await svc.sync(["portal"], batch=None, do_discover=False, progress=False)
        assert res.errors == {}
        assert "tonalidade" in await symbols("src/shade.py")
        st = (await svc.enrich_status())["summaries"]
        assert (st["fresh"], st["stale"]) == (1, 1)
        stale = await svc.file_summary("portal", "src/shade.py")
        assert stale is not None and stale["stale"] is True
        again = await svc.enrich_next(kind="file_summaries")
        assert again is not None and again["files"] == ["src/shade.py"]
        assert await svc.enrich_release(again["id"])

        # arquivo removido: o resumo sai junto
        (root / "src" / "shade.py").unlink()
        await svc.sync(["portal"], batch=None, do_discover=False, progress=False)
        assert await svc._summaries.get("portal", "src/shade.py") is None
        assert await svc._summaries.get("portal", "src/browser_version.py") is not None
