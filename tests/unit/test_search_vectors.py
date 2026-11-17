"""domain.search_vectors: textos das perguntas e do cartão, e o plano do que gerar/apagar."""

from __future__ import annotations

from code_index.domain.search_vectors import (
    Stored, VectorKey, card_text, card_texts, estimate_tokens, plan, question_texts, summary_text, summary_texts,
    text_hash,
)

CARD = {
    "kind": "service",
    "summary": "Autenticação dos usuários: login, multifator e emissão de tokens.",
    "capabilities": ["Validar código OTP", "Renovar access token"],
    "domain_tags": ["auth", "oauth"],
    "glossary": ["blacklist = blocklist"],
    "entrypoints": ["src/Main.java"],
    "related_repos": ["org__outro"],
}


def test_perguntas_ignoram_vazias_e_numeram_na_ordem() -> None:
    items = question_texts("org__svc", "a/X.java", ["  Como valida o OTP? ", "", None, "Onde renova o token?"])
    assert [(i.key, i.text) for i in items] == [
        (VectorKey("question", "org__svc", "a/X.java", 0), "Como valida o OTP?"),
        (VectorKey("question", "org__svc", "a/X.java", 1), "Onde renova o token?"),
    ]


def test_texto_do_cartao_tem_o_assunto_e_nao_os_caminhos() -> None:
    text = card_text("org__auth-backend", CARD)
    assert text.splitlines()[0] == "Repositório auth-backend (service)"
    assert "Validar código OTP; Renovar access token" in text
    assert "auth, oauth" in text and "blacklist = blocklist" in text
    assert "src/Main.java" not in text and "org__outro" not in text
    assert card_texts("org__x", None) == [] and card_texts("org__x", {"kind": "lib"}) == []
    (item,) = card_texts("org__auth-backend", CARD)
    assert item.key == VectorKey("repo_card", "org__auth-backend")


def test_plano_separa_em_dia_pendente_e_obsoleto() -> None:
    q0, q1 = question_texts("r", "a.py", ["pergunta um", "pergunta dois"])
    (card,) = card_texts("r", CARD)
    stored = {
        q0.key: Stored(q0.hash, "m1"),                          # em dia
        q1.key: Stored(text_hash("texto antigo"), "m1"),         # o resumo mudou
        card.key: Stored(card.hash, "m0"),                       # outro modelo
        VectorKey("question", "r", "saiu.py", 0): Stored("x", "m1"),   # o resumo saiu
    }
    p = plan([q0, q1, card, q0], stored, "m1")                 # repetido conta uma vez
    assert [i.key for i in p.embed] == [q1.key, card.key]
    assert p.delete == [VectorKey("question", "r", "saiu.py", 0)]
    assert p.counts_dict() == {
        "question": {"sources": 2, "embedded": 1, "pending": 1, "obsolete": 1},
        "summary": {"sources": 0, "embedded": 0, "pending": 0, "obsolete": 0},
        "repo_card": {"sources": 1, "embedded": 0, "pending": 1, "obsolete": 0},
    }


def test_texto_do_resumo_tem_caminho_resumo_e_termos_sem_codigo() -> None:
    (item,) = summary_texts("r", "src/auth/Login.java", "  Valida a senha e bloqueia após 5 falhas. ",
                            ["login", "", 3, "bloqueio"])
    assert item.key == VectorKey("summary", "r", "src/auth/Login.java", 0)
    assert item.text == "Arquivo src/auth/Login.java\nValida a senha e bloqueia após 5 falhas.\nTermos: login, bloqueio"
    assert summary_texts("r", "a.py", "   ") == [] and summary_texts("r", "a.py", None) == []
    assert summary_text("a.py", "x" * 5000).startswith("Arquivo a.py") and len(summary_text("a.py", "x" * 5000)) == 2000
    # resumo e perguntas do mesmo arquivo são vetores distintos (chaves diferentes) e mudam de forma independente
    (q,) = question_texts("r", "src/auth/Login.java", ["como bloquear?"])
    p = plan([item, q], {q.key: Stored(q.hash, "m")}, "m")
    assert [i.key.kind for i in p.embed] == ["summary"]
    assert estimate_tokens(0) == 0 and estimate_tokens(9) == 3


def test_plano_vazio() -> None:
    p = plan([], {}, "m")
    assert p.pending == 0 and p.delete == []
    assert all(c["sources"] == 0 for c in p.counts_dict().values())
