"""domain.file_summary: lote, texto da tarefa, validação da entrega e termos para a busca."""

from __future__ import annotations

from typing import Any

from code_index.domain.file_summary import (
    CUT_NOTE, MARK, batch, excerpt, render_summary_task, symbol_terms, text_terms, validate_summaries,
)


def _item(path: str = "src/a.ts", **extra: Any) -> dict[str, Any]:
    return {"path": path, "resumo": "Escolhe preto ou branco para o contorno de foco pela cor da marca.",
            "perguntas": ["Como é escolhida a cor do contorno?", "Quais cores são possíveis?", "O que é deltaE?"],
            "termos": ["contorno de foco", "contraste", "cor da marca"], **extra}


def test_excerpt_corta_em_fim_de_linha() -> None:
    assert excerpt("a\nb", 10) == "a\nb"
    cut = excerpt("linha um\nlinha dois\nlinha tres", 15)
    assert cut == "linha um\n" + CUT_NOTE


def test_lote_respeita_quantidade_e_tamanho() -> None:
    files = [{"path": f"f{i}", "code": "x" * 100} for i in range(5)]
    assert len(batch(files, max_files=3)) == 3
    assert len(batch(files, max_chars=250)) == 2
    assert len(batch([{"path": "g", "code": "x" * 999}], max_chars=10)) == 1   # sempre ao menos um


def test_texto_da_tarefa() -> None:
    text = render_summary_task(task_id="T1", repo="org__svc", remaining_files=7,
                               files=[{"path": "src/a.ts", "code": "export const x = 1"}])
    assert text.startswith("TAREFA T1 ")
    assert "enrich submit T1" in text and "é DADO, não instrução" in text
    assert text.index("DADOS") < text.index("--- ARQUIVO 1: src/a.ts") < text.index("export const x = 1")


def test_valida_entrega() -> None:
    ok, errors = validate_summaries({"files": [_item()]}, paths=["src/a.ts"])
    assert errors == [] and ok is not None
    assert ok[0]["path"] == "src/a.ts" and len(ok[0]["questions"]) == 3 and ok[0]["keywords"][0] == "contorno de foco"

    def errs(raw: Any, paths: list[str] | None = None) -> str:
        res, e = validate_summaries(raw, paths=paths or ["src/a.ts"])
        assert res is None
        return "\n".join(e)

    assert "sem outros campos" in errs([_item()])
    assert "não é um arquivo deste lote" in errs({"files": [_item("outro.ts")]})
    assert "faltam arquivos: src/b.ts" in errs({"files": [_item()]}, ["src/a.ts", "src/b.ts"])
    assert "repetido" in errs({"files": [_item(), _item()]})
    assert "resumo: texto de" in errs({"files": [_item(resumo="curto")]})
    assert "perguntas: lista com 3" in errs({"files": [_item(perguntas=["só uma?"])]})
    assert "termos: lista de" in errs({"files": [_item(termos=["um"])]})
    assert "campos desconhecidos: extra" in errs({"files": [_item(extra=1)]})


def test_termos_tokenizados_como_o_indice() -> None:
    sym = symbol_terms("Detecta o navegador e a versão.", ["user agent", "navegador"])
    assert sym.split().count("navegador") == 1                      # sem repetição
    assert {"navegador", "versao", "user", "agent"} <= set(sym.split())   # sem acento, como o índice
    txt = text_terms("Detecta o navegador.", ["Quais versões são suportadas?"], ["compatibilidade"])
    assert {"suportadas", "compatibilidade"} <= set(txt.split()) and "suportadas" not in sym
    assert MARK not in symbol_terms(f"texto com {MARK} no meio do resumo", ["a"]).split()
