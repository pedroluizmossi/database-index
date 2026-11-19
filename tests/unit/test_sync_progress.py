"""Andamento do sync: repos não pedidos que estão sendo refeitos, última atividade, etapa e parada."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from code_index.indexing.progress import CHECKING_OTHERS_LABEL, REPROCESSING_OTHERS_LABEL, SyncProgress


class _Src:
    def __init__(self, finished: int, unchanged: int) -> None:
        self.counts = SimpleNamespace(num_finished=finished, num_unchanged=unchanged, num_errors=0)

    def stats(self) -> Any:
        return SimpleNamespace(by_component={"code_flow.process_file": self.counts})


def test_extras_so_aparecem_quando_refazem_arquivos() -> None:
    t = SyncProgress()
    t.select(["pedido"])
    t.update("pedido", status="indexing", files_total=2)
    t.attach("pedido", _Src(2, 0))
    t.attach("ja_indexado", _Src(500, 500))   # memo hit puro: não aparece
    t.attach("openapi", _Src(1200, 200))      # 1000 refeitos: aparece com o motivo
    t.set_phase("index")
    t.refresh()
    snap = t.snapshot()
    assert snap["repos_done"] == 1 and snap["phase_label"] == REPROCESSING_OTHERS_LABEL
    assert snap["others"]["repos"] == 1 and snap["others"]["files"] == 1000
    assert snap["others"]["list"][0]["name"] == "openapi" and snap["others"]["reason"]
    assert snap["activity_at"] and t.done_names() == {"pedido"}


def test_sem_extras_rodando_e_so_verificacao() -> None:
    t = SyncProgress()
    t.select(["a"])
    t.update("a", status="indexing", files_total=1)
    t.attach("a", _Src(1, 0))
    t.attach("b", _Src(10, 10))
    t.set_phase("index")
    t.refresh()
    assert t.snapshot()["phase_label"] == CHECKING_OTHERS_LABEL and t.snapshot()["others"]["repos"] == 0


def test_etapa_e_parada_aparecem_no_retrato() -> None:
    t = SyncProgress()
    t.set_phase("finish")
    t.set_step("lendo os testes")
    assert t.snapshot()["step"] == "lendo os testes"
    t.set_stopping("pedido por ana")
    assert t.snapshot()["phase_label"] == "parando: pedido por ana"
