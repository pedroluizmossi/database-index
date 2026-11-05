"""Progresso do sync: retrato por repo a partir dos contadores do CocoIndex (contadores falsos)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from code_index.indexing.progress import SyncProgress


def counts(started: int, done: int, *, unchanged: int = 0, errors: int = 0) -> Any:
    return SimpleNamespace(num_execution_starts=started, num_finished=done + errors,
                           num_unchanged=unchanged, num_errors=errors)


class Source:
    def __init__(self, **by_component: Any) -> None:
        self.by_component = by_component

    def stats(self) -> Any:
        return SimpleNamespace(by_component=self.by_component)


def test_conta_arquivos_por_repo_e_marca_pronto_quando_acaba() -> None:
    p = SyncProgress()
    p.select(["a", "b"])
    p.update("a", status="indexing", files_total=25)
    p.update("b", status="indexing", files_total=3)
    p.attach("a", Source(index_repo=counts(1, 0), process_file=counts(9, 5, unchanged=2)))
    p.attach("b", Source(process_file=counts(3, 3)))
    p.set_phase("index")
    p.refresh()
    snap = p.snapshot()
    a, b = snap["repos"]
    assert (a["status"], a["files_done"], a["files_total"], a["files_unchanged"]) == ("indexing", 5, 25, 2)
    assert (b["status"], b["files_done"]) == ("done", 3)
    assert (snap["repos_done"], snap["repos_total"], snap["files_done"], snap["files_total"]) == (1, 2, 8, 28)
    assert snap["phase_label"] == "indexando"


def test_nome_qualificado_do_componente_tambem_conta() -> None:
    p = SyncProgress()
    p.select(["a"])
    p.update("a", status="indexing", files_total=10)
    p.attach("a", Source(**{"code_index.indexing.code_flow.process_file": counts(4, 4)}))
    p.refresh()
    assert p.snapshot()["repos"][0]["files_done"] == 4


def test_fim_do_pipeline_fecha_os_repos_em_andamento() -> None:
    ok, falho = SyncProgress(), SyncProgress()
    for p in (ok, falho):
        p.select(["a", "b"])
        p.update("a", status="indexing", files_total=4)
        p.update("b", status="error", error="git clone falhou")
    ok.finish_indexing(None)
    falho.finish_indexing("pipeline: sem conexão com o servidor de embeddings")
    assert [(r["status"], r["files_done"]) for r in ok.snapshot()["repos"]] == [("done", 4), ("error", 0)]
    a, b = falho.snapshot()["repos"]
    assert (a["status"], a["error"]) == ("error", "pipeline: sem conexão com o servidor de embeddings")
    assert b["error"] == "git clone falhou"  # erro anterior não é sobrescrito


def test_repo_fora_da_selecao_e_ignorado() -> None:
    p = SyncProgress()
    p.select(["a"])
    p.update("x", status="indexing")
    assert not p.tracks("x") and p.tracks("a")
    assert [r["name"] for r in p.snapshot()["repos"]] == ["a"]
