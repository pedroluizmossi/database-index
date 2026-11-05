"""Ponta a ponta da documentação externa: Wiki.js falso → sync incremental → páginas, apelidos e
menções → busca com vínculos, filtro kind e URL. Postgres real, embeddings falsos, sem rede.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from code_index.settings import Settings, use_settings

pytestmark = pytest.mark.integration

WIKI = "https://wiki.exemplo.com"


class FakeWiki:
    """Wiki.js 2.x mínimo: pages.list e pages.single via GraphQL."""

    def __init__(self) -> None:
        self.pages: dict[int, dict[str, Any]] = {
            1: {"path": "Plataforma/Email", "title": "Serviço de e-mail", "tags": [],
                "content": "# Envio de e-mail\n\nO snr-email-backend faz o envio transacional de mensagens."},
            2: {"path": "Plataforma/Front", "title": "Tela de e-mail", "tags": ["email-frontend"],
                "content": "# Tela\n\nComponentes da tela de configuração do remetente."},
            3: {"path": "Geral/Glossario", "title": "Glossário", "tags": [],
                "content": "# Glossário\n\nemail: mensagem eletrônica. Sem repo específico citado."},
        }
        self.updated = {pid: "2026-01-01T00:00:00Z" for pid in self.pages}
        self.fetched: list[int] = []
        self.deny_content = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok-wiki"
        body = json.loads(request.content)
        if "list(" in body["query"]:
            items = [{"id": pid, "path": p["path"], "locale": "pt-br", "title": p["title"],
                      "updatedAt": self.updated[pid], "tags": p["tags"], "isPublished": True, "isPrivate": False}
                     for pid, p in self.pages.items()]
            return httpx.Response(200, json={"data": {"pages": {"list": items}}})
        pid = body["variables"]["id"]
        if self.deny_content:
            return httpx.Response(200, json={"errors": [{"message": "Forbidden"}]})
        self.fetched.append(pid)
        p = self.pages[pid]
        page = {"id": pid, "path": p["path"], "locale": "pt-br", "title": p["title"], "description": "",
                "content": p["content"], "contentType": "markdown", "updatedAt": self.updated[pid],
                "tags": [{"tag": t} for t in p["tags"]]}
        return httpx.Response(200, json={"data": {"pages": {"single": page}}})


@pytest.fixture
def wiki(monkeypatch: pytest.MonkeyPatch) -> FakeWiki:
    fake = FakeWiki()
    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(fake.handler))

    from code_index.sources import wikijs
    monkeypatch.setattr(wikijs.httpx, "AsyncClient", client)
    return fake


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


async def test_wiki_ligada_aos_repos(int_settings: Settings, tmp_path: Path, fake_llm: Any, wiki: FakeWiki) -> None:
    settings = int_settings.model_copy(update={"wikijs_token": SecretStr("tok-wiki")})
    use_settings(settings)
    from code_index.services import open_service

    backend = write(tmp_path / "snr-email-backend", {
        "src/sender.py": "def send_email(message):\n    \"\"\"Envio transacional de mensagens.\"\"\"\n    return message\n",
        "README.md": "# Backend de e-mail\n\nEnvio transacional.\n",
    })
    front = write(tmp_path / "front", {
        "package.json": '{"name": "@minha-org/email-frontend", "version": "1.0.0"}',
        "src/remetente.ts": "export function configurarRemetente(nome: string) { return nome; }\n",
    })
    outro = write(tmp_path / "outro", {"main.go": "package main\n\nfunc glossario() {}\n"})

    async with open_service(settings) as svc:
        await svc.add_repo(str(backend), name="snr-email-backend")
        await svc.add_repo(str(front), name="email-front")
        await svc.add_repo(str(outro), name="outro")
        added = await svc.add_docs("wiki", url=WIKI, locale="pt-br", include=(), exclude=(),
                                   links=[("Geral/**", ["outro"])])
        assert added == {"name": "docs__wiki", "pages": 3, "pages_in_locale": 3}
        assert wiki.fetched == [1]  # valida read:source sem gravar ou indexar conteúdo
        wiki.fetched.clear()

        # Prévia: lista as páginas por raiz, sem baixar conteúdo.
        prev = await svc.docs_preview("docs__wiki", limit=2)
        assert prev["total"] == 3 and prev["truncated"] is True and len(prev["pages"]) == 2
        assert prev["roots"] == [{"root": "Geral", "pages": 1}, {"root": "Plataforma", "pages": 2}]
        assert prev["pages"][0]["url"] == f"{WIKI}/pt-br/Geral/Glossario"
        assert wiki.fetched == []

        result = await svc.sync(None, batch=None, do_discover=False, progress=False)
        assert result.errors == {}
        assert "docs__wiki" in result.indexed
        assert sorted(wiki.fetched) == [1, 2, 3]
        kinds = {r.name: r.kind for r in await svc.list_repos()}
        assert kinds["docs__wiki"] == "docs" and kinds["outro"] == "code"

        # Vínculos de cada origem: menção forte, tag (apelido do package.json) e regra explícita.
        email = await svc.linked_docs("snr-email-backend")
        assert [(d["title"], d["origins"]) for d in email] == [("Serviço de e-mail", ["mention"])]
        assert email[0]["url"] == f"{WIKI}/pt-br/Plataforma/Email"
        assert [(d["title"], d["origins"]) for d in await svc.linked_docs("email-front")] == [("Tela de e-mail", ["tag"])]
        assert [(d["title"], d["origins"]) for d in await svc.linked_docs("outro")] == [("Glossário", ["explicit"])]

        # Filtro por repo traz a página ligada, com URL, título e repos ligados.
        hits = await svc.search("envio transacional de mensagens", repos=["snr-email-backend"], rerank=False, top_k=10)
        wiki_hits = [h for h in hits if h.repo == "docs__wiki"]
        assert wiki_hits and {h.path for h in wiki_hits} == {"pt-br/Plataforma/Email.md"}
        page = wiki_hits[0]
        assert (page.kind, page.url, page.title, page.linked_repos) == (
            "doc", f"{WIKI}/pt-br/Plataforma/Email", "Serviço de e-mail", ["snr-email-backend"])
        assert "title:" not in page.code  # front-matter fica fora do trecho
        assert {h.repo for h in hits} <= {"snr-email-backend", "docs__wiki"}

        sem_docs = await svc.search("envio transacional de mensagens", repos=["snr-email-backend"], rerank=False,
                                    include_linked_docs=False)
        assert sem_docs and {h.repo for h in sem_docs} == {"snr-email-backend"}

        # kind: README conta como documentação; código fica de fora de "doc" e vice-versa.
        docs_only = await svc.search("envio transacional", kind="doc", rerank=False, top_k=20)
        assert docs_only and all(h.kind == "doc" for h in docs_only)
        assert ("snr-email-backend", "README.md") in {(h.repo, h.path) for h in docs_only}
        code_only = await svc.search("envio transacional", kind="code", rerank=False, top_k=20)
        assert code_only and all(h.kind == "code" and h.repo != "docs__wiki" for h in code_only)
        with pytest.raises(ValueError, match="kind inválido"):
            await svc.search("x", kind="wiki")

        # Incremental: nada mudou → nada é baixado. Uma página alterada e uma removida.
        wiki.fetched.clear()
        await svc.sync(["docs__wiki"], batch=None, do_discover=False, progress=False)
        assert wiki.fetched == []

        wiki.updated[2] = "2026-02-01T00:00:00Z"
        wiki.pages[2]["content"] = "# Tela\n\nNova tela de assinatura do remetente."
        del wiki.pages[3]
        await svc.sync(["docs__wiki"], batch=None, do_discover=False, progress=False)
        assert wiki.fetched == [2]
        assert await svc.linked_docs("outro") == []
        hits = await svc.search("assinatura do remetente", kind="doc", rerank=False)
        assert hits and hits[0].title == "Tela de e-mail" and hits[0].linked_repos == ["email-front"]

        # Vínculo gravado (manual): vale sem reindexar; a consulta lê a linha na hora.
        linked = await svc.link_doc("wiki", "Plataforma/Email", "email-front", reason="tela do remetente usa o backend")
        assert linked == {"page": "Plataforma/Email", "doc_repo": "docs__wiki", "doc_path": "pt-br/Plataforma/Email.md",
                          "repo": "email-front", "origin": "manual", "indexed": True}
        gravados = await svc.doc_links(source="wiki", repo="email-front")
        assert [(g["doc_path"], g["origin"], g["reason"]) for g in gravados] == [
            ("pt-br/Plataforma/Email.md", "manual", "tela do remetente usa o backend")]
        assert ("Serviço de e-mail", ["manual"]) in [(d["title"], d["origins"]) for d in await svc.linked_docs("email-front")]

        # Agent sem motivo é recusado; remover devolve True e depois False.
        with pytest.raises(ValueError, match="motivo"):
            await svc.link_doc("wiki", "Plataforma/Email", "outro", origin="agent")
        assert await svc.unlink_doc("wiki", "Plataforma/Email", "email-front") is True
        assert all(d["title"] != "Serviço de e-mail" for d in await svc.linked_docs("email-front"))
        assert await svc.unlink_doc("wiki", "Plataforma/Email", "email-front") is False

        # Some quando a página sai da wiki, sem apagar a linha gravada.
        await svc.link_doc("wiki", "Plataforma/Email", "email-front", reason="ainda válido")
        del wiki.pages[1]
        await svc.sync(["docs__wiki"], batch=None, do_discover=False, progress=False)
        assert all(d["title"] != "Serviço de e-mail" for d in await svc.linked_docs("email-front"))
        assert await svc.doc_links(source="wiki", repo="email-front") != []


async def test_cadastro_valida_conteudo_e_nao_salva_se_faltar_permissao(
    int_settings: Settings, wiki: FakeWiki,
) -> None:
    from code_index.services import open_service
    from code_index.sources.wikijs import WikiJsError

    settings = int_settings.model_copy(update={"wikijs_token": SecretStr("tok-wiki")})
    wiki.deny_content = True
    async with open_service(settings) as svc:
        with pytest.raises(WikiJsError, match="read:source"):
            await svc.add_docs("wiki", url=WIKI)
        assert svc.list_docs() == []
        wiki.pages.clear()
        with pytest.raises(WikiJsError, match="nenhuma página acessível"):
            await svc.add_docs("wiki", url=WIKI)
        assert svc.list_docs() == []


async def test_colisoes_e_remocao_preservam_repositorios(
    int_settings: Settings, tmp_path: Path, wiki: FakeWiki,
) -> None:
    from code_index.domain.models import DiscoveredRepo, DocSource
    from code_index.indexing.orchestrator import configured_repos
    from code_index.services import open_service

    settings = int_settings.model_copy(update={"wikijs_token": SecretStr("tok-wiki")})
    async with open_service(settings) as svc:
        repo = await svc.add_repo(str(tmp_path), name="docs__wiki")
        with pytest.raises(ValueError, match="conflita"):
            await svc.add_docs("wiki", url=WIKI)
        svc._config.save(docs=[DocSource(name="wiki", url=WIKI)])
        with pytest.raises(ValueError, match="conflita"):
            configured_repos(svc._config)
        with pytest.raises(ValueError, match="conflita"):
            await svc.remove_docs("wiki")
        assert svc._config.load_repos() == [repo] and svc.list_docs()

        svc._config.save(repos=[])
        with pytest.raises(ValueError, match="reservado"):
            await svc.add_repo(str(tmp_path), name="docs__wiki")
        await svc._repos.reconcile_org("docs", [DiscoveredRepo(
            name="docs__wiki", url="https://github.com/docs/wiki.git", default_branch="main",
            pushed_at=None, size_kb=1, language=None,
        )])
        with pytest.raises(ValueError, match="conflita"):
            await svc._repos.reconcile_manual([DocSource(name="wiki", url=WIKI).to_repo()])
        with pytest.raises(ValueError, match="conflita"):
            await svc.remove_docs("wiki", keep_files=True)
        assert (await svc._repos.all_rows())["docs__wiki"].org == "docs"
        await svc._repos.delete(["docs__wiki"])
        clone = settings.repos_dir / "docs__wiki"
        write(clone, {".git/config": "clone", "keep.txt": "dados"})
        with pytest.raises(ValueError, match="não identificada"):
            await svc.remove_docs("wiki")
        assert (clone / "keep.txt").read_text() == "dados" and svc.list_docs()
        with pytest.raises(RuntimeError, match="não identificada"):
            await svc._indexer._fetch_docs(DocSource(name="wiki", url=WIKI).to_repo(),
                                            Mock(), True)
        await svc.remove_docs("wiki", keep_files=True)
        assert (clone / "keep.txt").read_text() == "dados"
        with pytest.raises(ValueError, match="pasta já existe"):
            await svc.add_docs("wiki", url=WIKI)


async def test_indexar_so_paginas_indicadas(int_settings: Settings, fake_llm: Any, wiki: FakeWiki) -> None:
    settings = int_settings.model_copy(update={"wikijs_token": SecretStr("tok-wiki")})
    use_settings(settings)
    from code_index.services import open_service

    folder = settings.repos_dir / "docs__wiki" / "pt-br"
    async with open_service(settings) as svc:
        await svc.add_docs("wiki", url=WIKI, locale="pt-br", include=("Plataforma/**",))
        wiki.fetched.clear()  # add_docs lê uma página para validar o acesso ao conteúdo

        # Fora da seleção: recusado antes de baixar qualquer coisa.
        with pytest.raises(ValueError, match="fora da seleção"):
            await svc.index_doc_pages("docs__wiki", ["Geral/Glossario"])
        assert wiki.fetched == []

        # Uma página: só ela é baixada e embedada.
        await svc.index_doc_pages("docs__wiki", ["Plataforma/Email"])
        assert wiki.fetched == [1]
        assert (folder / "Plataforma" / "Email.md").is_file()
        assert not (folder / "Plataforma" / "Webhooks.md").exists() and not (folder / "Plataforma" / "Front.md").exists()
        assert any("Serviço de e-mail" in t for t in fake_llm.embedded)
        assert not any("Tela de e-mail" in t for t in fake_llm.embedded)

        # Outra página depois: a primeira continua no disco e não é reprocessada.
        fake_llm.embedded.clear()
        wiki.fetched.clear()
        await svc.index_doc_pages("docs__wiki", ["Plataforma/Front"])
        assert wiki.fetched == [2]
        assert (folder / "Plataforma" / "Email.md").is_file() and (folder / "Plataforma" / "Front.md").is_file()
        assert any("Tela de e-mail" in t for t in fake_llm.embedded)
        assert not any("Serviço de e-mail" in t for t in fake_llm.embedded)


async def test_visao_geral_e_vinculos_por_chave(int_settings: Settings, tmp_path: Path, fake_llm: Any, wiki: FakeWiki) -> None:
    settings = int_settings.model_copy(update={"wikijs_token": SecretStr("tok-wiki")})
    use_settings(settings)
    from code_index.services import open_service

    front = write(tmp_path / "front", {"src/a.ts": "export const a = 1;\n"})
    async with open_service(settings) as svc:
        await svc.add_repo(str(front), name="email-front")
        await svc.add_docs("wiki", url=WIKI, locale="pt-br", include=("Plataforma/**",))
        wiki.fetched.clear()
        await svc.index_doc_pages("docs__wiki", ["Plataforma/Email"])

        overview = await svc.docs_overview()
        assert [(o["doc_repo"], o["doc_path"], o["page"], o["root"]) for o in overview] == [
            ("docs__wiki", "pt-br/Plataforma/Email.md", "Plataforma/Email", "Plataforma")]
        assert overview[0]["links"] == []

        # Gravado pela chave da página (como a tela envia).
        res = await svc.link_doc_key("docs__wiki", "pt-br/Plataforma/Email.md", "email-front",
                                     reason="tela usa o backend", origin="manual")
        assert res["indexed"] is True
        assert (await svc.docs_overview())[0]["links"] == [
            {"repo": "email-front", "origins": ["manual"], "reason": "tela usa o backend", "removable": True}]

        # Chaves fora do padrão são recusadas; agent sem motivo também.
        with pytest.raises(ValueError, match="caminho de página inválido"):
            await svc.link_doc_key("docs__wiki", "../segredo.md", "email-front", origin="manual")
        with pytest.raises(ValueError, match="motivo"):
            await svc.link_doc_key("docs__wiki", "pt-br/Plataforma/Email.md", "email-front", origin="agent")

        assert await svc.unlink_doc_key("docs__wiki", "pt-br/Plataforma/Email.md", "email-front") is True
        assert (await svc.docs_overview())[0]["links"] == []
        assert await svc.unlink_doc_key("docs__wiki", "pt-br/Plataforma/Email.md", "email-front") is False
