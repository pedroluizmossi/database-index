"""sources/wikijs: sync incremental contra um Wiki.js falso (httpx.MockTransport, sem rede)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from code_index.domain.models import DocSource
from code_index.sources.wikijs import MANIFEST, WikiJsClient, WikiJsError, sync_docs


class FakeWiki:
    def __init__(self) -> None:
        self.pages: dict[int, dict[str, Any]] = {
            1: {"path": "Plataforma/Email", "title": "Serviço de e-mail", "updatedAt": "2026-01-01T00:00:00Z",
                "content": "# Email\n\nVeja github.com/minha-org/snr-email-backend", "contentType": "markdown",
                "tags": ["email-backend"]},
            2: {"path": "Plataforma/Webhooks", "title": "Webhooks", "updatedAt": "2026-01-01T00:00:00Z",
                "content": "<h1>Webhooks</h1><p>Reenvio com <b>retry</b></p>", "contentType": "html", "tags": []},
            3: {"path": "Rascunhos/Ideia", "title": "Ideia", "updatedAt": "2026-01-01T00:00:00Z",
                "content": "rascunho", "contentType": "markdown", "tags": []},
        }
        self.fetched: list[int] = []
        self.auth: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization", ""))
        body = json.loads(request.content)
        if "list(" in body["query"]:
            items = [{"id": pid, "path": p["path"], "locale": "pt-br", "title": p["title"], "updatedAt": p["updatedAt"],
                      "tags": p["tags"], "isPublished": True, "isPrivate": False} for pid, p in self.pages.items()]
            return httpx.Response(200, json={"data": {"pages": {"list": items}}})
        pid = body["variables"]["id"]
        self.fetched.append(pid)
        p = self.pages[pid]
        page = {"id": pid, "path": p["path"], "locale": "pt-br", "title": p["title"], "description": "",
                "content": p["content"], "contentType": p["contentType"], "updatedAt": p["updatedAt"],
                "tags": [{"tag": t} for t in p["tags"]]}
        return httpx.Response(200, json={"data": {"pages": {"single": page}}})


@pytest.fixture
def wiki() -> FakeWiki:
    return FakeWiki()


def client(wiki: FakeWiki) -> WikiJsClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(wiki.handler))
    return WikiJsClient("https://wiki.exemplo.com", "tok", http=http)


SOURCE = DocSource(name="wiki", url="https://wiki.exemplo.com", include=("Plataforma/**",))


async def test_primeiro_sync_baixa_paginas_filtradas_com_front_matter(wiki: FakeWiki, tmp_path: Path) -> None:
    res = await sync_docs(SOURCE, client(wiki), tmp_path)
    assert (res.total, res.changed, res.deleted) == (2, 2, 0)
    assert sorted(wiki.fetched) == [1, 2]  # Rascunhos ficou fora do include
    email = (tmp_path / "pt-br" / "Plataforma" / "Email.md").read_text()
    assert email.startswith("---\ntitle: \"Serviço de e-mail\"\nurl: \"https://wiki.exemplo.com/pt-br/Plataforma/Email\"")
    assert 'tags: ["email-backend"]' in email and "# Email" in email
    webhooks = (tmp_path / "pt-br" / "Plataforma" / "Webhooks.md").read_text()
    assert "# Webhooks" in webhooks and "**retry**" in webhooks  # HTML convertido para Markdown
    assert wiki.auth[0] == "Bearer tok"


async def test_sync_seguinte_baixa_so_o_que_mudou_e_apaga_removidas(wiki: FakeWiki, tmp_path: Path) -> None:
    first = await sync_docs(SOURCE, client(wiki), tmp_path)
    wiki.fetched.clear()
    same = await sync_docs(SOURCE, client(wiki), tmp_path)
    assert (same.changed, same.deleted, wiki.fetched) == (0, 0, [])
    assert same.revision == first.revision

    wiki.pages[1]["updatedAt"] = "2026-02-01T00:00:00Z"
    wiki.pages[1]["content"] = "# Email v2"
    del wiki.pages[2]
    res = await sync_docs(SOURCE, client(wiki), tmp_path)
    assert (res.total, res.changed, res.deleted) == (1, 1, 1)
    assert wiki.fetched == [1]
    assert not (tmp_path / "pt-br" / "Plataforma" / "Webhooks.md").exists()
    assert "Email v2" in (tmp_path / "pt-br" / "Plataforma" / "Email.md").read_text()
    assert res.revision != first.revision
    manifest = json.loads((tmp_path / MANIFEST).read_text())["pages"]
    assert set(manifest) == {"1"}


async def test_caminho_que_escaparia_da_pasta_e_ignorado(wiki: FakeWiki, tmp_path: Path) -> None:
    wiki.pages[4] = {"path": "Plataforma/../../etc/passwd", "title": "x", "updatedAt": "t", "content": "x",
                     "contentType": "markdown", "tags": []}
    await sync_docs(SOURCE, client(wiki), tmp_path / "dest")
    assert not (tmp_path / "etc").exists()
    assert 4 not in wiki.fetched


async def test_troca_de_caminhos_com_data_inalterada_rebaixa_as_paginas(wiki: FakeWiki, tmp_path: Path) -> None:
    await sync_docs(SOURCE, client(wiki), tmp_path)
    wiki.fetched.clear()
    wiki.pages[1]["path"], wiki.pages[2]["path"] = wiki.pages[2]["path"], wiki.pages[1]["path"]
    result = await sync_docs(SOURCE, client(wiki), tmp_path)
    assert result.changed == 2 and sorted(wiki.fetched) == [1, 2]
    assert "# Email" in (tmp_path / "pt-br/Plataforma/Webhooks.md").read_text()
    assert "# Webhooks" in (tmp_path / "pt-br/Plataforma/Email.md").read_text()


async def test_token_ausente_ou_recusado() -> None:
    with pytest.raises(WikiJsError, match="WIKIJS_TOKEN"):
        WikiJsClient("https://w", None)

    def forbidden(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Forbidden"}]})

    c = WikiJsClient("https://w", "x", http=httpx.AsyncClient(transport=httpx.MockTransport(forbidden)))
    with pytest.raises(WikiJsError, match="recusou o token"):
        await c.list_pages("pt-br")
