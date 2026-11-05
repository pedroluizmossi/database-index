"""Tarefa páginas da wiki → repos: texto da tarefa e validação da entrega."""

from __future__ import annotations

from code_index.domain.doc_task import page_excerpt, page_folder, render_doc_task, search_text, validate_doc_links

PAGES = ["pt-br/Plataforma/Email.md", "pt-br/Plataforma/Email/validacao.md"]
KNOWN = ["o__snr-email-backend", "o__snr-email-frontend"]


def test_helpers() -> None:
    assert page_folder("pt-br/Plataforma/Email/validacao.md") == "pt-br/Plataforma/Email"
    assert search_text("Validação", "pt-br/Plataforma/Email/validacao-email.md") == "Validação Plataforma Email validacao email"
    text = "---\ntitle: x\n---\n\n# Título\n\n\n\nCorpo"
    assert page_excerpt(text) == "# Título\n\nCorpo"
    assert page_excerpt("a\n" * 2000, max_chars=50).endswith("[... página cortada]")


def _ok() -> dict:
    return {"pages": [
        {"path": PAGES[0], "repos": [{"repo": "snr-email-backend", "basis": "pagina_cita", "confidence": "alta",
                                      "why": "descreve o serviço de e-mail"}]},
        {"path": PAGES[1], "repos": [], "nota": "procedimento genérico"},
    ]}


def test_validate_ok() -> None:
    out, errors = validate_doc_links(_ok(), pages=PAGES, known_repos=KNOWN)
    assert errors == [] and out is not None
    assert out[0]["repos"] == [{"repo": "o__snr-email-backend", "basis": "pagina_cita", "confidence": "alta",
                                "why": "descreve o serviço de e-mail"}]
    assert out[1] == {"path": PAGES[1], "repos": [], "verdicts": [], "nota": "procedimento genérico"}


def test_validate_erros() -> None:
    raw = {"pages": [
        {"path": PAGES[0], "repos": [{"repo": "inventado", "basis": "pagina_cita", "confidence": "alta", "why": "x"},
                                     {"repo": "snr-email-frontend", "basis": "acho", "confidence": "alta", "why": "x"},
                                     {"repo": "snr-email-frontend", "basis": "assunto", "confidence": "total", "why": "x"}]},
        {"path": "pt-br/outra.md", "repos": []},
    ]}
    out, errors = validate_doc_links(raw, pages=PAGES, known_repos=KNOWN)
    text = "\n".join(errors)
    assert out is None
    for expected in ('"inventado" não é um repo', ".basis", ".confidence", "não é uma página deste lote",
                     f"faltam páginas: {PAGES[1]}"):
        assert expected in text
    assert validate_doc_links({"pages": [], "extra": 1}, pages=PAGES, known_repos=KNOWN)[0] is None


def test_render() -> None:
    text = render_doc_task(task_id="t1", doc_repo="docs__wiki", folder="pt-br/Plataforma", remaining_pages=10,
                           pages=[{"path": PAGES[0], "title": "E-mail", "tags": "email", "excerpt": "Serviço de e-mail."}],
                           candidates=[{"repo": "o__snr-email-backend", "summary": "Envia e-mails. Outra frase.", "tags": ["e-mail"]}])
    assert text.index("INSTRUÇÕES") < text.index(" DADOS ") < text.index("PÁGINA 1")
    assert "enrich submit t1" in text and "o__snr-email-backend [e-mail]: Envia e-mails" in text
    assert "Outra frase" not in text


EXISTING = {PAGES[0]: {"o__snr-email-frontend": ["mention"], "o__snr-email-backend": ["manual"]}}


def test_validate_vereditos_dos_vinculos_atuais() -> None:
    base = {"pages": [{"path": PAGES[0], "repos": []}, {"path": PAGES[1], "repos": []}]}
    _, errors = validate_doc_links(base, pages=PAGES, known_repos=KNOWN, existing=EXISTING)
    assert any("falta veredito para o__snr-email-frontend" in e for e in errors)

    ok = {"pages": [{"path": PAGES[0], "repos": [], "vinculos_atuais": [
        {"repo": "snr-email-frontend", "veredito": "remover", "confidence": "alta", "why": "citação de passagem"}]},
        {"path": PAGES[1], "repos": []}]}
    out, errors = validate_doc_links(ok, pages=PAGES, known_repos=KNOWN, existing=EXISTING)
    assert errors == [] and out is not None
    assert out[0]["verdicts"] == [{"repo": "o__snr-email-frontend", "verdict": "remover", "confidence": "alta",
                                   "why": "citação de passagem"}]

    bad = {"pages": [{"path": PAGES[0], "vinculos_atuais": [
        {"repo": "snr-email-frontend", "veredito": "manter", "confidence": "alta", "why": "x"},
        {"repo": "snr-email-backend", "veredito": "remover", "confidence": "alta", "why": "x"}],  # gravado à mão
        "repos": [{"repo": "snr-email-frontend", "basis": "assunto", "confidence": "alta", "why": "x"}]},  # já ligado
        {"path": PAGES[1], "repos": []}]}
    _, errors = validate_doc_links(bad, pages=PAGES, known_repos=KNOWN, existing=EXISTING)
    text = "\n".join(errors)
    assert "o__snr-email-backend" not in text or "não é um vínculo [contestável]" in text
    assert '"snr-email-backend" não é um vínculo [contestável]' in text
    assert "já está ligado" in text


def test_render_mostra_vinculos_atuais() -> None:
    text = render_doc_task(task_id="t1", doc_repo="docs__wiki", folder="pt-br", remaining_pages=0,
                           pages=[{"path": PAGES[0], "title": "E-mail", "excerpt": "x", "links": EXISTING[PAGES[0]]},
                                  {"path": PAGES[1], "title": "Outra", "excerpt": "y", "links": {}}],
                           candidates=[])
    assert "o__snr-email-frontend (termo do texto igual a um apelido forte do repo) [contestável]" in text
    assert "o__snr-email-backend (gravado por uma pessoa) [só contexto]" in text
    assert "VÍNCULOS ATUAIS: nenhum" in text
