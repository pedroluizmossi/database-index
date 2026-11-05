"""Enriquecimento: fatos sem LLM, fila next/submit com validação, reservas e status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration

SDL = """domain platform
service email {
    endpoints {
        public query listBlacklistedEmails { in {} out {} }
        action sendEmail { in {} out {} }
    }
    events { event emailSent { } }
}
"""


def _project(base: Path, name: str, *, sdl: bool) -> Path:
    root = base / name
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("[![b](http://x/b.svg)](http://x)\n# Email\nServiço de e-mail.\n", encoding="utf-8")
    (root / "src" / "handler.py").write_text(
        "class SendEmailHandler:\n    def handle(self):\n        return 1\n\n    def retry(self):\n        return 2\n",
        encoding="utf-8",
    )
    (root / "src" / "envio.py").write_text("def enviar_agora(destino):\n    return destino\n", encoding="utf-8")
    if sdl:
        (root / "src" / "handlers.py").write_text(
            "class ListBlacklistedEmailsHandler:\n    def handle(self):\n        return []\n", encoding="utf-8")
        (root / "email.sdl").write_text(SDL, encoding="utf-8")
        (root / "catalog-info.yaml").write_text(
            "kind: Component\nmetadata:\n  name: email-backend\nspec:\n  owner: arquitetura\n  type: service\n",
            encoding="utf-8",
        )
    return root


def _card(**extra: Any) -> str:
    return json.dumps({
        "summary": "Serviço que envia e-mails e mantém uma blocklist de endereços.",
        "kind": "service",
        "capabilities": ["envio de e-mail"],
        "domain_tags": ["e-mail"],
        **extra,
    })


async def test_fila_de_cartoes(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    async with open_service(int_settings) as svc:
        for name, sdl in (("email-backend", True), ("email-frontend", False)):
            await svc.add_repo(str(_project(tmp_path, name, sdl=sdl)), name=name)
        res = await svc.sync(["email-backend", "email-frontend"], batch=None, do_discover=False, progress=False)
        assert res.errors == {}

        # o sync já coletou os fatos (sem custo); de novo, nada muda (memo pela revisão)
        assert await svc.enrich_facts() == {"repos": 2, "updated": 0}
        assert (await svc.enrich_facts(force=True))["updated"] == 2

        info = await svc.repo_card("email-backend")
        assert info is not None and info["card"] is None
        assert sorted((i["operation"], i["path"]) for i in info["impls"]) == [
            ("listBlacklistedEmails", "src/handlers.py"), ("sendEmail", "src/handler.py")]
        [impl] = await svc.find_api_impl("email.listblacklistedemails")
        assert (impl["repo"], impl["symbol"], impl["kind"], impl["origin"]) == (
            "email-backend", "ListBlacklistedEmailsHandler", "query", "handler")
        assert [i["symbol"] for i in await svc.find_api_impl("POST /email/actions/sendEmail")] == ["SendEmailHandler"]
        assert await svc.find_api_impl("naoExiste") == []
        assert (await svc.find_api_impl("listBlack"))[0]["operation"] == "listBlacklistedEmails"  # prefixo
        facts = info["facts"]
        assert facts["catalog"]["owner"] == "arquitetura"
        assert facts["sdl"][0]["service"] == "email" and facts["sdl"][0]["events"] == ["emailSent"]

        t1 = await svc.enrich_next(agent="teste", repo="email-backend")
        assert t1 is not None and t1["repo"] == "email-backend"
        assert "SendEmailHandler" in t1["text"] and "enviar_agora()" in t1["text"] and "domain=platform service=email" in t1["text"]
        assert "[![b]" not in t1["text"]  # README sem badges
        assert t1["candidates"] == ["email-frontend"]

        # o mesmo repo não sai de novo enquanto reservado; o outro sai
        t2 = await svc.enrich_next(agent="outro")
        assert t2 is not None and t2["repo"] == "email-frontend"
        assert await svc.enrich_next() is None

        # entrega inválida: erros legíveis, tarefa continua aberta
        with pytest.raises(ValueError) as err:
            await svc.enrich_submit(t1["id"], _card(related_repos=[{"repo": "inventado", "basis": "nome", "why": "x"}],
                                                    entrypoints=["nao/existe.py"]))
        assert "CANDIDATOS" in str(err.value) and "nao/existe.py" in str(err.value)

        ok = await svc.enrich_submit(t1["id"], _card(related_repos=[{"repo": "email-frontend", "basis": "nome", "why": "UI"}],
                                                     entrypoints=["src/handler.py"]))
        assert ok["repo"] == "email-backend"
        with pytest.raises(ValueError, match="já entregue"):
            await svc.enrich_submit(t1["id"], _card())

        assert await svc.enrich_release(t2["id"]) is True
        status = await svc.enrich_status()
        assert status["cards_ok"] == 1 and status["cards_missing"] == 1 and status["pending"] == 1
        assert status["with_sdl"] == 1 and status["rejections"] == 1
        assert status["tasks"] == {"done": 1, "released": 1}

        repos = {r.name: r for r in await svc.list_repos()}
        assert repos["email-backend"].summary and "blocklist" in repos["email-backend"].summary
        assert repos["email-backend"].tags == ["e-mail"] and repos["email-frontend"].summary is None
        found = await svc.find_repos("quem mantém a blocklist de endereços?")
        assert [h["repo"] for h in found][:1] == ["email-backend"]
        assert any(h["repo"] == "email-backend" for h in await svc.find_repos("listBlacklistedEmails"))  # operação SDL
        assert await svc.find_repos("kubernetes argocd") == []
        with pytest.raises(ValueError):
            await svc.find_repos("   ")

        card = await svc.repo_card("email-backend")
        assert card is not None and card["stale"] is False and card["card_agent"] == "teste"
        assert card["card"]["related_repos"] == [{"repo": "email-frontend", "basis": "nome", "why": "UI"}]

        # mudou o material (README) → cartão desatualizado e volta para a fila
        (tmp_path / "email-backend" / "README.md").write_text("# Email\nAgora também envia SMS.\n", encoding="utf-8")
        await svc.enrich_facts(["email-backend"], force=True)
        card = await svc.repo_card("email-backend")
        assert card is not None and card["stale"] is True
        assert (await svc.enrich_status())["pending"] == 2


async def test_reserva_expirada_volta_para_fila(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(_project(tmp_path, "email-backend", sdl=True)), name="email-backend")
        await svc.sync(["email-backend"], batch=None, do_discover=False, progress=False)
        t1 = await svc.enrich_next()
        assert t1 is not None
        await svc._enrich._pool.execute(  # simula o tempo passando
            f'UPDATE "{int_settings.pg_schema}".enrich_tasks SET lease_until = now() - interval \'1 minute\'')
        t2 = await svc.enrich_next()
        assert t2 is not None and t2["id"] != t1["id"]
        with pytest.raises(ValueError, match="outro agent"):
            await svc.enrich_submit(t1["id"], _card())
        assert (await svc.enrich_submit(t2["id"], _card()))["remaining"] == 0
