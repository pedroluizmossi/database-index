"""domain/doc_content: diagramas e imagens em base64 saem da documentação; o resto não muda."""

from __future__ import annotations

from code_index.domain.doc_content import strip_embedded_blobs

BLOB = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHN0eWxlPSJiYWNrZ3JvdW5k" * 40


def test_bloco_diagram_sai_e_numeracao_de_linhas_fica() -> None:
    text = f"# Arquitetura\n\nAntes.\n```diagram\n{BLOB}\n{BLOB}\n```\nDepois.\n"
    out = strip_embedded_blobs(text)
    assert "PHN2Zy" not in out
    assert out.count("\n") == text.count("\n")
    assert out.split("\n")[0] == "# Arquitetura" and "Antes." in out and "Depois." in out


def test_cerca_sem_linguagem_com_base64_longo_sai() -> None:
    text = f"Texto\n```\n{BLOB}\n```\nFim"
    out = strip_embedded_blobs(text)
    assert "PHN2Zy" not in out and out.count("\n") == text.count("\n") and out.endswith("Fim")


def test_codigo_normal_e_texto_permanecem() -> None:
    text = "```python\ndef f(x):\n    return x + 1\n```\n\nVeja `retry` e a URL https://exemplo.com/a/b?c=d.\n"
    assert strip_embedded_blobs(text) == text


def test_data_uri_e_linha_base64_solta_saem_sem_apagar_linhas() -> None:
    text = f"![img](data:image/png;base64,{BLOB})\nPalavra normal\n{BLOB}\nFim\n"
    out = strip_embedded_blobs(text)
    assert "PHN2Zy" not in out and "![img](" in out
    assert out.count("\n") == text.count("\n")
    assert "Palavra normal" in out and "Fim" in out


def test_texto_depois_de_data_uri_nao_e_engolido() -> None:
    out = strip_embedded_blobs("data:image/png;base64,QUJD\n\nConfiguração de email")
    assert out.endswith("Configuração de email")
