"""Fonte Swagger com S3 e LLM simulados e banco isolado."""
from typing import Any
from pathlib import Path

import httpx
import pytest

from code_index.settings import Settings, use_settings

pytestmark = pytest.mark.integration


async def test_vinculo_por_nome_exato_antes_de_sufixos(int_settings: Settings, fake_llm: Any,
                                                      monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from code_index.sources import openapi_s3
    from code_index.services import open_service

    xml = '''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
      <Contents><Key>platform_blob_service.swagger.yaml</Key><ETag>1</ETag><Size>1</Size></Contents></ListBucketResult>'''
    swagger = "swagger: '2.0'\ninfo: {title: blob}\npaths: {/blob: {get: {responses: {}}}}\n"
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml if "list-type" in req.url.params else swagger)

    real = httpx.AsyncClient
    monkeypatch.setattr(openapi_s3.httpx, "AsyncClient", lambda *args, **kwargs: real(transport=httpx.MockTransport(handler)))
    use_settings(int_settings)
    async with open_service(int_settings) as svc:
        for name in ("snr-blob-service", "snr-blob-backend", "snr-blob-service-replication"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / "main.py").write_text("def exemplo():\n    return 1\n")
            await svc.add_repo(str(folder), name=name)
        name = await svc.register_openapi("platform", ["blob_service"])
        assert (await svc.sync([name], batch=None, do_discover=False, progress=False)).errors == {}
        assert len(await svc.linked_docs("snr-blob-service")) == 1
        assert await svc.linked_docs("snr-blob-backend") == []
        assert await svc.linked_docs("snr-blob-service-replication") == []


async def test_indexacao_seletiva_e_incremental(int_settings: Settings, fake_llm: Any, monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    from code_index.sources import openapi_s3
    from code_index.services import open_service

    downloaded: list[str] = []
    xml = '''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
      <Contents><Key>platform_scheduler.swagger.yaml</Key><ETag>1</ETag><Size>1</Size></Contents>
      <Contents><Key>platform_account.swagger.yaml</Key><ETag>1</ETag><Size>1</Size></Contents></ListBucketResult>'''
    swagger = ("swagger: '2.0'\ninfo: {title: scheduler, description: 'Serviço de agendamentos.'}\n"
               "paths: {/scheduler/jobs: {get: {description: agendamento, deprecated: true, "
               "x-senior-visibility: internal, responses: {}}}}\n")

    def handler(req: httpx.Request) -> httpx.Response:
        if "list-type" in req.url.params:
            return httpx.Response(200, text=xml)
        downloaded.append(req.url.path)
        return httpx.Response(200, text=swagger)

    real = httpx.AsyncClient
    monkeypatch.setattr(openapi_s3.httpx, "AsyncClient", lambda *args, **kwargs: real(transport=httpx.MockTransport(handler)))
    use_settings(int_settings)
    async with open_service(int_settings) as svc:
        for name in ("snr-scheduler-service", "snr-scheduler-frontend", "snr-platform"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / "main.py").write_text("def exemplo():\n    return 1\n")
            await svc.add_repo(str(folder), name=name)
        assert (await svc.sync(["snr-scheduler-service", "snr-scheduler-frontend", "snr-platform"],
                               batch=None, do_discover=False, progress=False)).errors == {}
        preview = await svc.openapi_preview("platform", ["scheduler"])
        assert preview["services"] == ["scheduler"] and not downloaded
        name = await svc.register_openapi("platform", ["scheduler"])
        result = await svc.sync([name], batch=None, do_discover=False, progress=False,
                                doc_pages=frozenset({"scheduler"}))
        assert result.errors == {}
        assert len(downloaded) == 1 and downloaded[0].endswith("platform_scheduler.swagger.yaml")
        status = await svc.openapi_status()
        assert status[0]["domain"] == "platform"
        assert status[0]["selected_services"] == ["scheduler"]
        assert status[0]["services"] == [{"service": "scheduler", "operations": 1}]
        # O filtro é aplicado antes da paginação e preserva o comportamento anterior por padrão.
        assert len(await svc.docs_overview()) == 2
        assert await svc.docs_overview(docs_only=True) == []
        assert len(await svc.docs_overview(limit=1)) == 1
        assert len(await svc.docs_overview(limit=1, offset=1)) == 1
        assert await svc.docs_overview(limit=1, offset=2) == []
        linked = await svc.linked_docs("snr-scheduler-service")
        assert len(linked) == 2 and all(d["origins"] == ["service"] for d in linked)
        graph = await svc.xray()
        assert graph["available"]
        assert len([p for p in graph["pages"] if p["repo"] == name]) == 2
        assert len([l for l in graph["links"] if l["doc_repo"] == name and l["repo"] == "snr-scheduler-service" and l["origin"] == "service"]) == 2
        assert not any(l["doc_repo"] == name and l["repo"] == "snr-platform" for l in graph["links"])
        assert await svc.linked_docs("snr-scheduler-frontend") == []
        assert await svc.linked_docs("snr-platform") == []
        filtered = await svc.search("scheduler jobs agendamento", repos=["snr-scheduler-service"],
                                    kind="doc", rerank=False)
        assert any(h.repo == name and "/scheduler/jobs" in h.code for h in filtered)
        assert not await svc.search("scheduler jobs agendamento", repos=["snr-platform"], kind="doc", rerank=False)
        hits = await svc.search("scheduler jobs agendamento", kind="doc", rerank=False)
        assert any(h.repo == name and "scheduler/jobs" in h.code for h in hits)
        assert any("deprecated: true" in h.code and "x-senior-visibility: internal" in h.code for h in hits)
        result = await svc.sync([name], batch=None, do_discover=False, progress=False,
                                doc_pages=frozenset({"scheduler"}))
        assert result.errors == {} and len(downloaded) == 1
        # Outro backend com o mesmo nome normalizado deixa a correspondência ambígua.
        folder = tmp_path / "snr-scheduler-backend"
        folder.mkdir()
        (folder / "main.py").write_text("def outro():\n    return 2\n")
        await svc.add_repo(str(folder), name="snr-scheduler-backend")
        assert (await svc.sync(["snr-scheduler-backend"], batch=None, do_discover=False, progress=False)).errors == {}
        assert await svc.linked_docs("snr-scheduler-service") == []
        assert await svc.linked_docs("snr-scheduler-backend") == []


async def test_vinculo_pelo_sdl_do_repo(int_settings: Settings, fake_llm: Any,
                                        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Repo com nome sem relação com o serviço liga pela declaração `domain`/`service` do .sdl."""
    from code_index.sources import openapi_s3
    from code_index.services import open_service

    xml = '''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
      <Contents><Key>platform_blob_service.swagger.yaml</Key><ETag>1</ETag><Size>1</Size></Contents></ListBucketResult>'''
    swagger = "swagger: '2.0'\ninfo: {title: blob}\npaths: {/blob: {get: {responses: {}}}}\n"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml if "list-type" in req.url.params else swagger)

    real = httpx.AsyncClient
    monkeypatch.setattr(openapi_s3.httpx, "AsyncClient", lambda *args, **kwargs: real(transport=httpx.MockTransport(handler)))
    use_settings(int_settings)
    async with open_service(int_settings) as svc:
        folder = tmp_path / "armazenamento-arquivos"
        folder.mkdir()
        (folder / "blob.sdl").write_text('domain platform ("p")\nservice blob_service ("Blob") {\n'
                                         '  endpoints { query healthcheck { } }\n}\n')
        (folder / "main.py").write_text("def exemplo():\n    return 1\n")
        await svc.add_repo(str(folder), name="armazenamento-arquivos")
        name = await svc.register_openapi("platform", ["blob_service"])
        res = await svc.sync(["armazenamento-arquivos", name], batch=None, do_discover=False, progress=False)
        assert res.errors == {}
        # o nome não casa, mas o sync coleta os fatos do .sdl e recalcula as relações sozinho
        linked = await svc.linked_docs("armazenamento-arquivos")
        assert len(linked) == 1 and linked[0]["origins"] == ["sdl"]
