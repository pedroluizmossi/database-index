"""Plugins de fonte de documentação: o que cada um materializou vira documentos tipados."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_index.domain.contracts import ContractRoutes
from code_index.domain.documents import API_OPERATION, API_OVERVIEW, WIKI_PAGE
from code_index.domain.models import DocSource
from code_index.sources.openapi_s3 import OpenApiPlugin, _operation_attrs
from code_index.sources.registry import doc_plugin, doc_plugins
from code_index.sources.wikijs import WikiJsPlugin


def test_registro() -> None:
    assert {p.type for p in doc_plugins()} == {"wikijs", "openapi_s3"}
    assert isinstance(doc_plugin("wikijs"), WikiJsPlugin)
    with pytest.raises(ValueError, match="não suportado"):
        doc_plugin("confluence")


def test_wiki_documents(tmp_path: Path) -> None:
    (tmp_path / "pt-br" / "Plataforma").mkdir(parents=True)
    (tmp_path / "pt-br" / "Plataforma" / "Email.md").write_text(
        '---\ntitle: "E-mail"\nurl: "https://wiki/pt-br/Plataforma/Email"\npage_id: 7\n'
        'updated_at: "2026-01-01"\ntags: ["email"]\n---\n\ncorpo\n', encoding="utf-8")
    (tmp_path / ".manifest.json").write_text(json.dumps({"pages": {
        "7": {"updated_at": "2026-01-01", "file": "pt-br/Plataforma/Email.md"},
        "8": {"updated_at": "x", "file": "pt-br/sumiu.md"}}}), encoding="utf-8")
    source = DocSource(name="wiki", url="https://wiki")
    [doc] = WikiJsPlugin().documents(source, tmp_path)
    assert (doc.path, doc.type, doc.title, doc.tags, doc.updated_at) == (
        "pt-br/Plataforma/Email.md", WIKI_PAGE, "E-mail", ("email",), "2026-01-01")
    assert doc.attrs == {"page_id": 7, "folder": "pt-br/Plataforma"}


ROUTES = ContractRoutes((("queries", "query"), ("actions", "action"), ("signals", "signal")))


def test_openapi_documents(tmp_path: Path) -> None:
    (tmp_path / "vendas" / "pedidos").mkdir(parents=True)
    op = tmp_path / "vendas" / "pedidos" / "a1.md"
    op.write_text('---\ntitle: "POST /pedidos/queries/listarPedidos"\nurl: "https://s3/x"\n'
                  'tags: ["pedidos"]\n---\n# POST /pedidos/queries/listarPedidos\n', encoding="utf-8")
    entity = tmp_path / "vendas" / "pedidos" / "b2.md"
    entity.write_text('---\ntitle: "GET /pedidos/entities/item/{id}"\nurl: "https://s3/x"\ntags: []\n---\n',
                      encoding="utf-8")
    (tmp_path / "vendas" / "pedidos" / "__overview.md").write_text('---\ntitle: "Visão geral"\n---\n', encoding="utf-8")
    (tmp_path / ".openapi-manifest.json").write_text(json.dumps({"vendas_pedidos.swagger.yaml": {
        "etag": "e1", "files": ["vendas/pedidos/a1.md", "vendas/pedidos/b2.md", "vendas/pedidos/__overview.md"]}}))
    source = DocSource(name="openapi-vendas", url="https://s3", type="openapi_s3", locale="vendas")
    docs = {d.path: d for d in OpenApiPlugin().documents(source, tmp_path, routes=ROUTES)}
    assert docs["vendas/pedidos/a1.md"].type == API_OPERATION
    assert docs["vendas/pedidos/a1.md"].attrs == {"domain": "vendas", "service": "pedidos", "method": "POST",
                                                  "http_path": "/pedidos/queries/listarPedidos",
                                                  "kind": "query", "operation": "listarPedidos"}
    assert "operation" not in docs["vendas/pedidos/b2.md"].attrs  # entidade: sem operação
    assert docs["vendas/pedidos/__overview.md"].type == API_OVERVIEW
    assert docs["vendas/pedidos/__overview.md"].attrs == {"domain": "vendas", "service": "pedidos"}
    # sem rotas de contrato (nenhum perfil): a página fica só com método e caminho
    plain = {d.path: d for d in OpenApiPlugin().documents(source, tmp_path)}
    assert plain["vendas/pedidos/a1.md"].attrs == {"domain": "vendas", "service": "pedidos", "method": "POST",
                                                   "http_path": "/pedidos/queries/listarPedidos"}


def test_operation_attrs_so_do_proprio_servico() -> None:
    assert "operation" not in _operation_attrs("POST /outro/actions/x", "pedidos", ROUTES)
    assert _operation_attrs("POST /pedidos/signals/mudou", "pedidos", ROUTES)["kind"] == "signal"
    assert "operation" not in _operation_attrs("POST /pedidos/signals/mudou", "pedidos")


def test_rotas_de_contrato() -> None:
    assert not ContractRoutes() and ContractRoutes().operation("/a/queries/b", "a") is None
    assert ROUTES.operation("/Pedidos/queries/listar", "pedidos") == ("query", "listar")
    assert ROUTES.operation("/pedidos/queries/listar/x", "pedidos") is None
    both = ContractRoutes.combine([ContractRoutes((("q", "query"),)), ContractRoutes((("q", "action"), ("a", "action")))])
    assert both.segments == (("q", "query"), ("a", "action"))  # repetido: vale o do primeiro
    with pytest.raises(ValueError):
        ContractRoutes((("eventos", "event"),))  # só grupos chamáveis
    with pytest.raises(ValueError):
        ContractRoutes((("a|b", "query"),))
