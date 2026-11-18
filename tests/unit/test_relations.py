"""Chaves de entidade, origens e relações (domain/relations.py)."""

from __future__ import annotations

import pytest

from code_index.domain.relations import (
    DOCUMENTS, STORED, Relation, code_key, contestable_origins, doc_key, key_kind, op_key, origin_order, origin_rank,
    origins_payload, repo_key, sort_origins, split_code_key, split_doc_key, split_repo_key,
)


def test_chaves_ida_e_volta() -> None:
    assert repo_key("o__x") == "repo:o__x" and split_repo_key("repo:o__x") == "o__x"
    assert doc_key("docs__wiki", "pt-br/A/B.md") == "doc:docs__wiki/pt-br/A/B.md"
    assert split_doc_key("doc:docs__wiki/pt-br/A/B.md") == ("docs__wiki", "pt-br/A/B.md")
    assert op_key("Platform", "Email", "listBlacklistedEmails") == "op:platform/email/listBlacklistedEmails"
    key = code_key("o__x", "src/a:b/H.java", 17)
    assert split_code_key(key) == ("o__x", "src/a:b/H.java", 17)
    assert [key_kind(k) for k in ("repo:a", "doc:b/c", "op:d/e/f", key)] == ["repo", "doc", "op", "code"]


def test_origens() -> None:
    assert origin_order()[:3] == ("manual", "agent", "llm")
    assert contestable_origins() == {"tag", "url", "mention"} and STORED == {"manual", "agent", "llm"}
    assert sort_origins(["mention", "manual", "explicit", "mention"]) == ["manual", "explicit", "mention"]
    assert origin_rank("desconhecida") == len(origin_order())
    payload = origins_payload()
    assert [o["order"] for o in payload] == list(range(len(payload)))
    assert all({"name", "label", "description", "contestable", "stored"} <= set(o) for o in payload)


def test_relation_valida_tipo_e_origem() -> None:
    r = Relation("doc:d/p.md", "repo:x", DOCUMENTS, "mention")
    assert r.contestable and not Relation("doc:d/p.md", "repo:x", DOCUMENTS, "manual").contestable
    with pytest.raises(ValueError, match="tipo"):
        Relation("a", "b", "parecido_com", "manual")
    with pytest.raises(ValueError, match="origem"):
        Relation("a", "b", DOCUMENTS, "Chute!")
    # origem bem formada mas desconhecida (ex.: de um perfil desligado): legível, no fim da ordem, não contestável
    old = Relation("a", "b", DOCUMENTS, "chute")
    assert not old.contestable and origin_rank("chute") == len(origin_order())
