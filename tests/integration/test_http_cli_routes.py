"""Rotas que a CLI remota usa (paridade CLI ↔ API): o que só existia na CLI local ganhou rota.

Isolado como test_http_api.py: uvicorn real, schema code_index_test, pastas em tmp_path, LLM falso e Wiki.js falso
(sem rede, sem Voyage).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from test_docs_wiki import WIKI, FakeWiki, wiki  # noqa: F401 - fixture do Wiki.js falso
from test_http_api import ADMIN_EMAIL, USER_EMAIL, Server, _free_port, _setup_tokens, client

from code_index.interfaces.http.app import create_app, server_config
from code_index.services import open_service
from code_index.services.db import migrate_database
from code_index.settings import Settings, use_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def server(int_settings: Settings, fake_llm: Any, tmp_path: Any, wiki: FakeWiki) -> Iterator[Server]:  # noqa: F811
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    settings = int_settings.model_copy(update={
        "auth_secret": SecretStr("s" * 48), "public_url": url, "http_port": port, "admin_emails": [ADMIN_EMAIL],
        "google_client_id": "teste.apps.googleusercontent.com", "google_client_secret": SecretStr("x"),
        "google_allowed_emails": [USER_EMAIL], "wikijs_token": SecretStr("tok-wiki"),
        "wikijs_allowed_hosts": ["wiki.exemplo.com"],
    })
    use_settings(settings)
    tokens, ids = asyncio.run(_setup_tokens(settings))
    (tmp_path / "meu-repo").mkdir()
    (tmp_path / "meu-repo" / "app.py").write_text("def ola():\n    return 'mundo'\n")
    srv = uvicorn.Server(server_config(create_app(settings), "127.0.0.1", port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("servidor não subiu")
        time.sleep(0.05)
    try:
        yield Server(url=url, settings=settings, tokens=tokens, token_ids=ids, srv=srv, thread=thread)
    finally:
        srv.should_exit = True
        thread.join(timeout=15)


def _wait_job(c: httpx.Client, job_id: str, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    job: dict[str, Any] = c.get(f"/api/v1/sync/jobs/{job_id}").json()
    while job["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.3)
        job = c.get(f"/api/v1/sync/jobs/{job_id}").json()
    return job


def test_fonte_de_docs_e_vinculos_pela_forma_da_cli(server: Server, tmp_path: Any) -> None:
    admin, leitor, indexador = client(server, "admin-bot"), client(server, "leitor"), client(server, "indexador")
    body = {"name": "wiki", "url": WIKI, "include": ["Plataforma/**"],
            "links": [{"path": "Plataforma/Email", "repos": ["meu-repo"]}]}
    assert leitor.post("/api/v1/docs/sources", json=body).status_code == 403
    assert admin.post("/api/v1/docs/sources", json={**body, "name": "../fora"}).status_code == 422
    # o WIKIJS_TOKEN do servidor só vai para os hosts da allowlist (nem http:// fora de loopback)
    assert admin.post("/api/v1/docs/sources", json={**body, "url": "https://atacante.exemplo.net"}).status_code == 403
    assert admin.post("/api/v1/docs/sources", json={**body, "url": "http://wiki.exemplo.com"}).status_code == 400
    r = admin.post("/api/v1/docs/sources", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "docs__wiki" and r.json()["pages"] == 2 and r.json()["pages_in_locale"] == 3
    assert admin.post("/api/v1/docs/sources", json=body).status_code == 400  # já existe
    fontes = leitor.get("/api/v1/docs/sources").json()
    assert [f["repo"] for f in fontes] == ["docs__wiki"]
    assert fontes[0]["links"] == [{"path": "Plataforma/Email", "repos": ["meu-repo"]}]

    assert admin.post("/api/v1/repos", json={"source": str(tmp_path / "meu-repo"), "name": "meu-repo"}).status_code == 201
    r = admin.post("/api/v1/sync", json={"repos": ["meu-repo"], "discover": False})
    assert r.status_code == 202, r.text
    assert _wait_job(admin, r.json()["job_id"])["status"] == "done"  # vínculo só para repo de código conhecido
    # vínculo pela fonte + página sem idioma (como `code-index docs link wiki Plataforma/Email meu-repo`)
    ambos = {"source": "wiki", "page": "Plataforma/Email", "doc_repo": "docs__wiki",
             "doc_path": "pt-br/Plataforma/Email.md", "repo": "meu-repo"}
    assert admin.post("/api/v1/docs/links", json=ambos).status_code == 422
    assert admin.post("/api/v1/docs/links", json={"repo": "meu-repo"}).status_code == 422
    r = admin.post("/api/v1/docs/links", json={"source": "wiki", "page": "Plataforma/Email", "repo": "meu-repo",
                                               "reason": "o serviço de e-mail mora aqui", "origin": "manual"})
    assert r.status_code == 201, r.text
    link = r.json()
    assert link["page"] == "Plataforma/Email" and link["doc_path"] == "pt-br/Plataforma/Email.md"
    assert link["origin"] == "agent"  # token de agent grava sempre "agent", mesmo pedindo "manual"
    assert link["indexed"] is False
    assert admin.post("/api/v1/docs/links", json={"source": "nao-existe", "page": "X", "repo": "meu-repo",
                                                  "reason": "x"}).status_code == 400

    # `docs vinculos`: listagem dos gravados, com filtro por fonte e repo
    rows = leitor.get("/api/v1/docs/links", params={"source": "wiki"}).json()
    assert len(rows) == 1 and rows[0]["repo"] == "meu-repo" and rows[0]["created_at"]
    assert leitor.get("/api/v1/docs/links", params={"repo": "meu-repo"}).json() == rows
    assert leitor.get("/api/v1/docs/links", params={"source": "nao-existe"}).status_code == 400

    unlink = {"source": "wiki", "page": "Plataforma/Email", "repo": "meu-repo"}
    assert leitor.delete("/api/v1/docs/links", params=unlink).status_code == 403
    assert admin.delete("/api/v1/docs/links", params={"repo": "meu-repo"}).status_code == 400
    assert admin.delete("/api/v1/docs/links", params=unlink).json() == {"removed": True}
    assert admin.delete("/api/v1/docs/links", params=unlink).status_code == 404

    # ignorar / devolver à busca pela forma da CLI
    r = admin.post("/api/v1/docs/ignored", json={"source": "wiki", "page": "Plataforma/Front", "reason": "velha"})
    assert r.status_code == 200 and r.json()["page"] == "Plataforma/Front"
    ref = {"source": "wiki", "page": "Plataforma/Front"}
    assert admin.delete("/api/v1/docs/ignored", params=ref).json() == {"ignored": False}
    assert admin.delete("/api/v1/docs/ignored", params=ref).status_code == 404

    # aprovação em lote por confiança (`docs aprovar --confianca alta`)
    assert leitor.post("/api/v1/docs/suggestions/approve", json={"confidence": "alta"}).status_code == 403
    assert admin.post("/api/v1/docs/suggestions/approve", json={"confidence": "x"}).status_code == 422
    r = admin.post("/api/v1/docs/suggestions/approve", json={"confidence": "alta", "source": "wiki", "action": "add"})
    assert r.status_code == 200 and r.json() == {"approved": 0}

    # `index docs__wiki --page Plataforma/Email`: job com as páginas, executado pelo worker embutido
    paginas = {"repos": ["docs__wiki"], "pages": ["Plataforma/Email"]}
    assert indexador.post("/api/v1/sync", json={**paginas, "dry_run": True}).status_code == 400
    assert indexador.post("/api/v1/sync", json={**paginas, "repos": ["meu-repo"]}).status_code == 400
    assert indexador.post("/api/v1/sync", json={**paginas, "repos": ["docs__wiki", "meu-repo"]}).status_code == 400
    r = indexador.post("/api/v1/sync", json=paginas)
    assert r.status_code == 202, r.text
    job = _wait_job(indexador, r.json()["job_id"])
    assert job["params"]["doc_pages"] == ["Plataforma/Email"] and job["params"]["repos"] == ["docs__wiki"]
    assert job["status"] == "done", job
    assert list(job["result"]["indexed"]) == ["docs__wiki"]

    # `docs remove wiki --keep-files`
    assert leitor.delete("/api/v1/docs/sources/wiki").status_code == 403
    r = admin.delete("/api/v1/docs/sources/wiki", params={"keep_files": True})
    assert r.status_code == 200 and r.json() == {"name": "wiki", "removed": True}
    assert leitor.get("/api/v1/docs/sources").json() == []
    assert admin.delete("/api/v1/docs/sources/wiki").status_code == 400


def test_sync_all_full_e_vetores_dry_run(server: Server, tmp_path: Any) -> None:
    admin, leitor, indexador = client(server, "admin-bot"), client(server, "leitor"), client(server, "indexador")
    assert admin.post("/api/v1/repos", json={"source": str(tmp_path / "meu-repo"), "name": "meu-repo"}).status_code == 201
    # prévia com --all (sem limite de lote) e --full (sem custo)
    dry = indexador.post("/api/v1/sync", json={"all": True, "full": True, "discover": False, "dry_run": True})
    assert dry.status_code == 202 and dry.json()["selected"] == ["meu-repo"] and dry.json()["remaining"] == 0
    # só o que mudou (--changed): repo pendente (1ª indexação) fica de fora
    dry = indexador.post("/api/v1/sync", json={"only_changed": True, "discover": False, "dry_run": True})
    assert dry.status_code == 202 and dry.json()["selected"] == []
    # --full ignora os caches (caro): só admin enfileira
    r = indexador.post("/api/v1/sync", json={"repos": ["meu-repo"], "full": True, "discover": False})
    assert r.status_code == 403 and "full" in r.json()["detail"]
    # lote sem limite (all) também é só de admin: contornaria o teto do batch (custo na Voyage)
    r = indexador.post("/api/v1/sync", json={"all": True, "discover": False})
    assert r.status_code == 403 and "all" in r.json()["detail"]
    r = admin.post("/api/v1/sync", json={"all": True, "full": True, "discover": False})
    assert r.status_code == 202, r.text
    job = _wait_job(admin, r.json()["job_id"])
    assert job["params"]["all"] is True and job["params"]["batch"] is None and job["params"]["full"] is True
    assert job["status"] == "done" and list(job["result"]["indexed"]) == ["meu-repo"], job
    # indexado: diretório local entra sempre no "o que mudou" (revarrer é barato)
    dry = indexador.post("/api/v1/sync", json={"only_changed": True, "discover": False, "dry_run": True})
    assert dry.json()["selected"] == ["meu-repo"]

    # `vectors embed --dry-run`: conta na hora, sem custo e sem gastar o balde de sync
    assert leitor.post("/api/v1/vectors/embed", json={"dry_run": True}).status_code == 403
    r = indexador.post("/api/v1/vectors/embed", json={"dry_run": True, "kinds": ["summary"], "limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and {"to_embed", "estimate", "obsolete", "pending"} <= set(r.json())


async def _indexa(settings: Settings, path: Any) -> None:
    """Indexa direto pelo serviço (LLM falso), sem depender do worker: o que interessa aqui são as leituras."""
    async with open_service(settings) as svc:
        await svc.add_repo(str(path), name="meu-repo")
        res = await svc.sync(["meu-repo"], do_discover=False, progress=False)
        assert list(res.indexed) == ["meu-repo"], res.errors


def test_leituras_e_manutencao_sem_custo(server: Server, tmp_path: Any) -> None:
    leitor, indexador = client(server, "leitor"), client(server, "indexador")
    asyncio.run(_indexa(server.settings, tmp_path / "meu-repo"))

    # busca com -c/--candidates
    r = leitor.post("/api/v1/search", json={"query": "ola", "candidates": 5, "rerank": False})
    assert r.status_code == 200, r.text
    assert leitor.post("/api/v1/search", json={"query": "ola", "candidates": 0}).status_code == 422

    # `skips` sem repo (todos) e com repo
    assert leitor.get("/api/v1/skipped").status_code == 200
    assert leitor.get("/api/v1/skipped", params={"repo": "meu-repo", "limit": 10}).json() == []
    assert leitor.get("/api/v1/skipped", params={"repo": "nao-existe"}).status_code == 400
    assert leitor.get("/api/v1/repos/nao-existe/skipped").status_code == 404
    assert client(server).get("/api/v1/skipped").status_code == 401

    # `relations stats`
    st = leitor.get("/api/v1/relations/stats")
    assert st.status_code == 200 and set(st.json()) == {"rows", "effective"}

    # `enrich facts` e `enrich apply-summaries` (sem LLM e sem custo): index:write
    assert leitor.post("/api/v1/enrich/facts").status_code == 403
    assert client(server, "enriquecedor").post("/api/v1/enrich/facts").status_code == 403
    r = indexador.post("/api/v1/enrich/facts", json={"repos": ["meu-repo"], "force": True})
    assert r.status_code == 200 and set(r.json()) == {"repos", "updated"}, r.text
    assert indexador.post("/api/v1/enrich/facts").status_code == 200  # corpo opcional
    assert indexador.post("/api/v1/enrich/facts", json={"repos": ["nao-existe"]}).status_code == 400
    r = indexador.post("/api/v1/enrich/apply-summaries")
    assert r.status_code == 200 and {"chunks", "pruned"} <= set(r.json())

    # `tests scan` / `graph scan` com filtro de repos; wait=true devolve o resultado como a CLI
    r = indexador.post("/api/v1/scans/tests", params={"repo": ["meu-repo"], "wait": True})
    assert r.status_code == 200 and r.json()["kind"] == "tests" and "cases" in r.json()["result"], r.text
    r = indexador.post("/api/v1/scans/graph", params={"repo": ["meu-repo"], "force": True, "wait": True})
    assert r.status_code == 200 and r.json()["kind"] == "graph", r.text
    assert indexador.post("/api/v1/scans/graph", params={"repo": ["nao-existe"]}).status_code == 400
    r = indexador.post("/api/v1/scans/tests", params={"repo": ["meu-repo"]})
    assert r.status_code == 202 and r.json()["tests"]["last_run"]["repos"] == ["meu-repo"]


def test_db_migrate_idempotente(int_settings: Settings) -> None:
    use_settings(int_settings)
    asyncio.run(migrate_database(int_settings))   # aplica (ou já estava em dia pelo fixture)
    assert asyncio.run(migrate_database(int_settings)) == []
