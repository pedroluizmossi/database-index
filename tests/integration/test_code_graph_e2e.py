"""Grafo de chamadas: lido no fim do sync (sem embedding), gravado por repo e consultado por quem chama/é chamado."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


def _project(base: Path) -> Path:
    root = base / "billing"
    pkg = root / "src" / "main" / "java" / "com" / "x"
    pkg.mkdir(parents=True)
    (pkg / "Billing.java").write_text(
        "package com.x;\n\npublic class Billing {\n    private final Sender sender;\n\n"
        "    Billing(Sender sender) {\n        this.sender = sender;\n    }\n\n"
        "    public void charge(String order) {\n        sender.send(order);\n        sender.send(order);\n"
        "        log(order);\n    }\n\n    private void log(String s) {\n    }\n}\n", encoding="utf-8")
    (pkg / "Sender.java").write_text(
        "package com.x;\n\npublic class Sender {\n    public void send(String o) {\n    }\n}\n", encoding="utf-8")
    web = root / "web"
    web.mkdir()
    (web / "api.ts").write_text("export function fetchOrders() {\n  return [];\n}\n", encoding="utf-8")
    (web / "page.ts").write_text(
        "import { fetchOrders } from './api';\n\nexport function render() {\n  fetchOrders();\n}\n", encoding="utf-8")
    return root


async def test_grafo_lido_no_sync_e_consultado(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(_project(tmp_path)), name="billing")
        res = await svc.sync(["billing"], batch=None, do_discover=False, progress=False)
        assert res.errors == {}

        st = await svc.graph_status()
        assert (st["repos"], st["files"], st["edges"]) == (1, 4, 3)
        assert st["edges_by_via"] == {"tipo": 1, "arquivo": 1, "import": 1}

        callers = await svc.call_graph("Sender.send")
        assert callers["total"] == 1
        (e,) = callers["edges"]
        assert (e["name"], e["path"], e["line"], e["call_line"], e["calls"], e["via"]) == (
            "Billing.charge", "src/main/java/com/x/Billing.java", 10, 11, 2, "tipo")
        assert e["of"] == {"path": "src/main/java/com/x/Sender.java", "line": 4, "name": "Sender.send"}
        assert e["inferred"] is False

        # só o nome do método, o caminho:linha da definição e o arquivo também acham
        assert (await svc.call_graph("send"))["total"] == 1
        assert (await svc.call_graph("src/main/java/com/x/Sender.java:4"))["total"] == 1
        assert {x["name"] for x in (await svc.call_graph("web/api.ts"))["edges"]} == {"render"}

        callees = await svc.call_graph("Billing.charge", direction="callees")
        assert {(x["name"], x["via"]) for x in callees["edges"]} == {("Sender.send", "tipo"), ("Billing.log", "arquivo")}

        assert (await svc.call_graph("Inexistente"))["edges"] == []
        with pytest.raises(ValueError, match="desconhecido"):
            await svc.call_graph("send", repos=["outro-repo"])

        # mesma revisão: não relê; --force relê
        assert (await svc.scan_calls())["repos"] == 0
        assert (await svc.scan_calls(["billing"], force=True)) == {"repos": 1, "edges": 3, "pruned": 0}

        # nada do grafo foi para a Voyage além dos trechos normais do sync
        assert not any("code_edges" in t for t in fake_llm.embedded)
