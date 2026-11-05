import httpx
import pytest

from code_index.sources.openapi_s3 import SwaggerObject, list_objects, render_operations, select_objects


def item(domain: str, service: str) -> SwaggerObject:
    return SwaggerObject(f"{domain}_{service}.swagger.yaml", domain, service, "e", "date", 10)


@pytest.mark.asyncio
async def test_listagem_paginada() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, text='''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
                <IsTruncated>true</IsTruncated><NextContinuationToken>next</NextContinuationToken>
                <Contents><Key>platform_scheduler.swagger.yaml</Key><Size>10</Size></Contents></ListBucketResult>''')
        return httpx.Response(200, text='''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
            <IsTruncated>false</IsTruncated><Contents><Key>agro_agro_events.swagger.yaml</Key><Size>20</Size></Contents>
            </ListBucketResult>''')

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        objects = await list_objects(client)
    assert [(o.domain, o.service) for o in objects] == [("platform", "scheduler"), ("agro", "agro_events")]
    assert calls[1].url.params["continuation-token"] == "next"


def test_selecao_dominio_e_servico() -> None:
    objects = [item("platform", "scheduler"), item("platform", "account"), item("agro", "events")]
    assert select_objects(objects, "platform") == objects[:2]
    assert select_objects(objects, "platform", ["scheduler"]) == objects[:1]
    with pytest.raises(ValueError, match="serviços não encontrados"):
        select_objects(objects, "platform", ["missing"])
    with pytest.raises(ValueError, match="selecione"):
        select_objects(objects, "platform", [])


def test_render_operacoes() -> None:
    source = '''swagger: '2.0'
info: {title: scheduler, description: Tarefas}
basePath: /bridge/platform
paths:
  /scheduler/queries/test:
    post:
      description: Executa teste
      responses:
        '200': {description: ok}
'''
    rendered = render_operations(source, item("platform", "scheduler"))
    assert len(rendered) == 1
    assert rendered[0][0] == "POST /scheduler/queries/test"
    assert "Executa teste" in rendered[0][1]
    assert "platform_scheduler.swagger.yaml" in rendered[0][1]


def test_tab_apos_chave_vazia_nao_perde_conteudo() -> None:
    from code_index.sources.openapi_s3 import _load_yaml, render_overview

    source = "swagger: '2.0'\ninfo:\n  title: teste\n  description: |\n    Valor com\ttab.\n" \
             "paths:\n  /health:\n    get:\n      description: estado\n      responses: {}\n" \
             "definitions:\n  Health:\n    properties:\n      healthCheckStatus:\t\t  \n" \
             "      reason:\n        type: string\t\t  \n"
    doc = _load_yaml(source, "platform_cache_manager.swagger.yaml")
    assert doc["definitions"]["Health"]["properties"]["healthCheckStatus"] is None
    assert doc["definitions"]["Health"]["properties"]["reason"]["type"] == "string"
    assert doc["info"]["description"] == "Valor com\ttab.\n"
    assert "estado" in render_operations(source, item("platform", "cache_manager"))[0][1]
    assert "Valor com\ttab." in (render_overview(source, item("platform", "cache_manager")) or "")


@pytest.mark.parametrize("spaces", [2, 5])
def test_tab_na_indentacao_de_respostas_preserva_estrutura(spaces: int) -> None:
    from code_index.sources.openapi_s3 import _load_yaml

    prefix = "  \t" + " " * spaces
    source = ("paths:\n  /teste:\n    get:\n      responses:\n        200:\n"
              "          description: Ok\n" + prefix + "401:\n" + prefix + "  description: Negado\n"
              + prefix + "default:\n" + prefix + "  description: Erro\n")
    doc = _load_yaml(source, "exemplo.yaml")
    assert doc["paths"]["/teste"]["get"]["responses"] == {
        200: {"description": "Ok"}, 401: {"description": "Negado"}, "default": {"description": "Erro"}}


def test_yaml_invalido_informa_arquivo_sem_corrigir_indentacao() -> None:
    from code_index.sources.openapi_s3 import _load_yaml

    with pytest.raises(ValueError, match=r"platform_teste\.swagger\.yaml \(linha 2, coluna 1\)"):
        _load_yaml("paths:\n\t/teste: {}\n", "platform_teste.swagger.yaml")


def test_descricao_integral_fica_na_visao_geral() -> None:
    from code_index.sources.openapi_s3 import render_overview
    source = '''swagger: '2.0'
info:
  title: teste
  description: |
    Primeira linha.
    Detalhes que não pertencem a cada operação.
paths:
  /teste:
    get: {description: uma operação, responses: {}}
'''
    operation = render_operations(source, item("platform", "scheduler"))[0][1]
    overview = render_overview(source, item("platform", "scheduler"))
    assert "Primeira linha." in operation
    assert "Detalhes que não pertencem" not in operation
    assert overview and "Detalhes que não pertencem" in overview
    assert '"platform"' not in operation.split("---", 2)[1]


def test_referencias_locais_e_parametros_do_caminho() -> None:
    source = '''swagger: '2.0'
info: {title: scheduler}
paths:
  /jobs:
    parameters: [{name: tenant, in: query, type: string}]
    get:
      responses:
        '200':
          description: ok
          schema: {$ref: '#/definitions/Job'}
definitions:
  Job:
    type: object
    properties: {name: {type: string}}
'''
    text = render_operations(source, item("platform", "scheduler"))[0][1]
    assert "path_parameters" in text
    assert "#/definitions/Job" in text and "name:" in text


def test_preserva_campos_e_resolve_mais_de_vinte_definicoes() -> None:
    import yaml

    definitions = {f"D{i}": {"type": "object", "properties": {"next": {"$ref": f"#/definitions/D{i+1}"}}}
                   for i in range(24)}
    definitions["D24"] = {"type": "string"}
    data = {"swagger": "2.0", "info": {"title": "teste"},
            "paths": {"/teste": {"get": {"deprecated": True, "x-senior-event": "evento",
                "x-senior-visibility": "internal", "consumes": ["text/plain"],
                "parameters": [{"$ref": "#/parameters/A~1B"}],
                "responses": {"200": {"schema": {"$ref": "#/definitions/D0"}}}}}},
            "parameters": {"A/B": {"name": "id", "in": "query", "type": "string"}},
            "definitions": definitions}
    text = render_operations(yaml.safe_dump(data), item("platform", "scheduler"))[0][1]
    assert "deprecated: true" in text and "x-senior-event: evento" in text
    assert "x-senior-visibility: internal" in text and "text/plain" in text
    assert "#/definitions/D24" in text and "#/parameters/A~1B" in text and "name: id" in text
    assert "Referências não resolvidas" not in text


def test_ciclos_e_referencias_nao_resolvidas_sao_explicitos() -> None:
    source = '''swagger: '2.0'
info: {title: teste}
paths:
  /teste:
    get:
      responses:
        '200': {schema: {$ref: '#/definitions/A'}}
        '400': {schema: {$ref: '#/definitions/Ausente'}}
        '500': {schema: {$ref: 'https://outro.exemplo/schema.yaml#/A'}}
definitions:
  A: {properties: {b: {$ref: '#/definitions/B'}}}
  B: {properties: {a: {$ref: '#/definitions/A'}}}
'''
    text = render_operations(source, item("platform", "scheduler"))[0][1]
    assert "#/definitions/A" in text and "#/definitions/B" in text
    assert "Referências não resolvidas" in text
    assert "caminho não encontrado" in text and "referência externa não permitida" in text


@pytest.mark.asyncio
async def test_referencias_externas_limitadas_ao_bucket() -> None:
    from code_index.settings import Settings
    from code_index.sources.openapi_s3 import _external_documents

    entry = item("platform", "scheduler")
    schema_url = entry.bucket_url + "/common.yaml"
    nested_url = entry.bucket_url + "/more.yaml"
    source = '''swagger: '2.0'
info: {title: teste}
paths:
  /teste:
    get:
      responses:
        '200': {schema: {$ref: 'common.yaml#/definitions/A'}}
        '400': {schema: {$ref: 'https://externo.invalid/roubo.yaml#/A'}}
'''
    visited = []

    def respond(req: httpx.Request) -> httpx.Response:
        visited.append(str(req.url))
        if str(req.url) == schema_url:
            return httpx.Response(200, text="definitions: {A: {$ref: 'more.yaml#/definitions/B'}}")
        if str(req.url) == nested_url:
            return httpx.Response(200, text="definitions: {B: {type: string}}")
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        docs, has_external = await _external_documents(client, source, entry, Settings(_env_file=None))
    assert has_external and set(visited) == {schema_url, nested_url}
    text = render_operations(source, entry, docs)[0][1]
    assert 'more.yaml#/definitions/B' in text and 'type: string' in text
    assert 'referência externa não permitida' in text


@pytest.mark.asyncio
async def test_sync_parcial_preserva_outros_servicos(tmp_path, monkeypatch) -> None:
    import json
    from code_index.domain.models import DocSource
    from code_index.sources import openapi_s3

    source = DocSource(name="openapi-platform", url="https://example.test", type="openapi_s3", locale="platform")
    old_file = tmp_path / "platform/account/old.md"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("antigo")
    (tmp_path / ".openapi-manifest.json").write_text(json.dumps({
        "platform_account.swagger.yaml": {"etag": "old", "files": ["platform/account/old.md"]}}))
    swagger = "swagger: '2.0'\ninfo: {title: x}\npaths: {/test: {get: {responses: {}}}}\n"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text='''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
                <IsTruncated>false</IsTruncated><Contents><Key>platform_scheduler.swagger.yaml</Key>
                <ETag>new</ETag><Size>10</Size></Contents></ListBucketResult>''')
        return httpx.Response(200, text=swagger)

    original = httpx.AsyncClient
    monkeypatch.setattr(openapi_s3.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond)))
    from code_index.settings import Settings
    await openapi_s3.sync_source(source, tmp_path, Settings(_env_file=None), frozenset({"scheduler"}))
    assert old_file.read_text() == "antigo"
    assert len(list((tmp_path / "platform/scheduler").glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_sync_regera_manifesto_antigo_mesmo_com_etag_igual(tmp_path, monkeypatch) -> None:
    import json
    from code_index.domain.models import DocSource
    from code_index.settings import Settings
    from code_index.sources import openapi_s3

    name = "platform_scheduler.swagger.yaml"
    previous = tmp_path / "platform/scheduler/old.md"
    previous.parent.mkdir(parents=True)
    previous.write_text("antigo")
    (tmp_path / ".openapi-manifest.json").write_text(json.dumps({name: {"etag": "same", "files": ["platform/scheduler/old.md"]}}))
    calls = []
    xml = f'''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
      <Contents><Key>{name}</Key><ETag>same</ETag><Size>10</Size></Contents></ListBucketResult>'''
    swagger = "swagger: '2.0'\ninfo: {title: x}\npaths: {/test: {get: {deprecated: true, responses: {}}}}\n"

    def respond(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, text=xml if "list-type" in req.url.params else swagger)

    real = httpx.AsyncClient
    monkeypatch.setattr(openapi_s3.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond)))
    source = DocSource(name="openapi-platform", url="https://example.test", type="openapi_s3", locale="platform")
    settings = Settings(_env_file=None)
    await openapi_s3.sync_source(source, tmp_path, settings)
    files = list((tmp_path / "platform/scheduler").glob("*.md"))
    assert len(files) == 1 and "deprecated: true" in files[0].read_text()
    assert not previous.exists() and len(calls) == 2
    await openapi_s3.sync_source(source, tmp_path, settings)
    assert len(calls) == 3  # segundo sync só lista, sem baixar o Swagger


@pytest.mark.asyncio
async def test_sync_detecta_alteracao_na_referencia_externa(tmp_path, monkeypatch) -> None:
    from code_index.domain.models import DocSource
    from code_index.settings import Settings
    from code_index.sources import openapi_s3

    bucket = "https://s3.amazonaws.com/bucket"
    source = DocSource(name="openapi-platform", url=bucket, type="openapi_s3", locale="platform")
    xml = '''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
      <Contents><Key>platform_scheduler.swagger.yaml</Key><ETag>same</ETag><Size>10</Size></Contents></ListBucketResult>'''
    swagger = '''swagger: '2.0'
info: {title: teste}
paths:
  /teste:
    get:
      responses:
        '200': {schema: {$ref: 'common.yaml#/definitions/A'}}
'''
    external = {"value": "primeira"}

    def respond(req: httpx.Request) -> httpx.Response:
        if "list-type" in req.url.params:
            return httpx.Response(200, text=xml)
        if req.url.path.endswith("common.yaml"):
            return httpx.Response(200, text=f"definitions: {{A: {{description: {external['value']}}}}}")
        return httpx.Response(200, text=swagger)

    real = httpx.AsyncClient
    monkeypatch.setattr(openapi_s3.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond)))
    settings = Settings(_env_file=None)
    before = await openapi_s3.sync_source(source, tmp_path, settings)
    external["value"] = "segunda"
    after = await openapi_s3.sync_source(source, tmp_path, settings)
    assert before != after
    assert "segunda" in next((tmp_path / "platform/scheduler").glob("*.md")).read_text()
