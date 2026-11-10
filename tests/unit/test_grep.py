"""infra/grep: ripgrep sobre diretórios temporários (sem banco, sem rede)."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_index.domain.file_patterns import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from code_index.infra.grep import Grep, GrepTarget

pytestmark = pytest.mark.skipif(not Grep().available, reason="ripgrep não instalado")


@pytest.fixture
def repo(tmp_path: Path) -> GrepTarget:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "Pagamento.java").write_text(
        'class Pagamento {\n  void cobrar() { throw new IllegalStateException("Saldo insuficiente"); }\n'
        '  String apiKey = "a8f9d0s9f8d0s9f8";\n}\n'
    )
    (root / "src" / "app.py").write_text("print('saldo insuficiente')\n" * 3)
    (root / "tests" / "test_pagamento.py").write_text("assert 'Saldo insuficiente'\n")
    (root / ".env").write_text("SALDO=insuficiente\n")
    return GrepTarget("org__repo", root.resolve(), tuple(DEFAULT_INCLUDE), tuple(DEFAULT_EXCLUDE))


async def test_texto_exato_respeita_filtros_do_indice(repo: GrepTarget) -> None:
    res = await Grep().search([repo], "Saldo insuficiente")
    paths = {m.path for m in res.matches}
    assert paths == {"src/Pagamento.java", "src/app.py"}  # sem tests/ e sem .env
    m = next(m for m in res.matches if m.path == "src/Pagamento.java")
    assert (m.repo, m.line) == ("org__repo", 2) and "Saldo insuficiente" in m.text


async def test_case_sensitive_e_regex(repo: GrepTarget) -> None:
    res = await Grep().search([repo], "Saldo insuficiente", case_sensitive=True)
    assert {m.path for m in res.matches} == {"src/Pagamento.java"}
    res = await Grep().search([repo], r"void \w+\(\)", regex=True)
    assert [m.path for m in res.matches] == ["src/Pagamento.java"]


async def _mask(path: Path) -> list[str]:
    return path.read_text().replace("a8f9d0s9f8d0s9f8", "***").split("\n")


async def test_mascara_e_limite(repo: GrepTarget) -> None:
    res = await Grep().search([repo], "apiKey", mask=_mask)
    assert res.matches and all("a8f9d0" not in m.text and "***" in m.text for m in res.matches)
    res = await Grep().search([repo], "saldo", max_results=2)
    assert len(res.matches) == 2 and res.truncated


@pytest.mark.parametrize(("pattern", "regex"), [
    ("apiKey = \"a8", False),           # prefixo do segredo como texto exato
    (r"apiKey = \"[a-m]", True),       # busca binária caractere a caractere
    ("a8f9d0s9f8d0s9f8", False),
])
async def test_padrao_que_so_casa_no_segredo_nao_volta(repo: GrepTarget, pattern: str, regex: bool) -> None:
    # Se voltasse, mesmo com o texto mascarado, o "casou / não casou" revelaria o segredo aos poucos.
    res = await Grep().search([repo], pattern, regex=regex, case_sensitive=True, mask=_mask)
    assert res.matches == []


async def test_linha_gigante_nao_derruba_o_grep(repo: GrepTarget) -> None:
    # Arquivo abaixo do --max-filesize, mas com 250 mil ocorrências numa linha: o evento JSON do rg
    # passa do limite do leitor. A linha é pulada e as outras voltam.
    (repo.root / "src" / "dados.py").write_text("z " * 250_000 + "\n")
    (repo.root / "src" / "outro.py").write_text("z = 1\n")
    res = await Grep().search([repo], "z", case_sensitive=True)
    assert {m.path for m in res.matches} == {"src/outro.py"}


async def test_filtro_de_caminho(repo: GrepTarget) -> None:
    res = await Grep().search([repo], "saldo", path_glob="**/*.py")
    assert {m.path for m in res.matches} == {"src/app.py"}


async def test_padrao_invalido_e_vazio(repo: GrepTarget) -> None:
    with pytest.raises(ValueError, match="padrão inválido"):
        await Grep().search([repo], "(sem fechar", regex=True)
    with pytest.raises(ValueError):
        await Grep().search([repo], "")


async def test_padrao_comecando_com_hifen_nao_vira_opcao(repo: GrepTarget) -> None:
    res = await Grep().search([repo], "--files")
    assert res.matches == []  # tratado como texto, não como flag do rg
