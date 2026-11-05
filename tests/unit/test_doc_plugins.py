"""Plugins de fonte de documentação: o que cada um materializou vira documentos tipados."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_openapi_documents(tmp_path: Path) -> None:
    (tmp_path / "platform" / "email").mkdir(parents=True)
    op = tmp_path / "platform" / "email" / "a1.md"
    op.write_text('---\ntitle: "POST /email/queries/listBlacklistedEmails"\nurl: "https://s3/x"\n'
                  'tags: ["email"]\n---\n# POST /email/queries/listBlacklistedEmails\n', encoding="utf-8")
    entity = tmp_path / "platform" / "email" / "b2.md"
    entity.write_text('---\ntitle: "GET /email/entities/log/{id}"\nurl: "https://s3/x"\ntags: []\n---\n', encoding="utf-8")
    (tmp_path / "platform" / "email" / "__overview.md").write_text('---\ntitle: "Visão geral"\n---\n', encoding="utf-8")
    (tmp_path / ".openapi-manifest.json").write_text(json.dumps({"platform_email.swagger.yaml": {
        "etag": "e1", "files": ["platform/email/a1.md", "platform/email/b2.md", "platform/email/__overview.md"]}}))
    source = DocSource(name="openapi-platform", url="https://s3", type="openapi_s3", locale="platform")
    docs = {d.path: d for d in OpenApiPlugin().documents(source, tmp_path)}
    assert docs["platform/email/a1.md"].type == API_OPERATION
    assert docs["platform/email/a1.md"].attrs == {"domain": "platform", "service": "email", "method": "POST",
                                                  "http_path": "/email/queries/listBlacklistedEmails",
                                                  "kind": "query", "operation": "listBlacklistedEmails"}
    assert "operation" not in docs["platform/email/b2.md"].attrs  # entidade: sem operação
    assert docs["platform/email/__overview.md"].type == API_OVERVIEW
    assert docs["platform/email/__overview.md"].attrs == {"domain": "platform", "service": "email"}


def test_operation_attrs_so_do_proprio_servico() -> None:
    assert "operation" not in _operation_attrs("POST /outro/actions/x", "email")
    assert _operation_attrs("POST /email/signals/changed", "email")["kind"] == "signal"
