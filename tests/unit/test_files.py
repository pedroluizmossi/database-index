"""retrieval.files.read_file: numeração, path traversal e mascaramento de segredos."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_index.retrieval.files import read_file


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text('a = 1\nAPI_KEY = "a8f9d0s9f8d0s9f8"\nb = 2\n')
    (root / "config.yaml").write_text("db:\n  password: Sup3rS3cret!\n  user: app\n")
    (tmp_path / "fora.txt").write_text("segredo fora da raiz")
    return root


def test_numera_linhas_e_respeita_intervalo(repo: Path) -> None:
    assert read_file(repo, "r", "src/app.py", 3, 3) == "    3  b = 2"


def test_mascara_segredos_no_codigo_e_na_config(repo: Path) -> None:
    assert 'API_KEY = "***"' in read_file(repo, "r", "src/app.py")
    out = read_file(repo, "r", "config.yaml")
    assert "password: ***" in out and "Sup3rS3cret" not in out
    assert "user: app" in out


@pytest.mark.parametrize("path", ["../fora.txt", "src/../../fora.txt", "/etc/passwd", "nao/existe.py", "src"])
def test_recusa_caminhos_fora_da_raiz_ou_inexistentes(repo: Path, path: str) -> None:
    with pytest.raises(ValueError, match="arquivo não encontrado"):
        read_file(repo, "r", path)


@pytest.mark.parametrize("path", [".npmrc", ".git/config", "src/.env"])
def test_recusa_arquivos_ocultos(repo: Path, path: str) -> None:
    (repo / ".git").mkdir()
    for p in (".npmrc", ".git/config", "src/.env"):
        (repo / p).write_text("//h/:_authToken=12345678-aaaa-bbbb-cccc-1234567890ab\n")
    with pytest.raises(ValueError, match="arquivo não encontrado"):
        read_file(repo, "r", path)


def test_symlink_para_oculto_e_recusado(repo: Path) -> None:
    (repo / ".env").write_text("SECRET=x\n")
    (repo / "link.txt").symlink_to(repo / ".env")
    with pytest.raises(ValueError, match="arquivo não encontrado"):
        read_file(repo, "r", "link.txt")


def test_intervalo_fora_do_arquivo_e_erro_com_total(repo: Path) -> None:
    with pytest.raises(ValueError, match="tem 3 linhas"):
        read_file(repo, "r", "src/app.py", 99)
    with pytest.raises(ValueError, match="intervalo inválido"):
        read_file(repo, "r", "src/app.py", 3, 2)
    assert read_file(repo, "r", "src/app.py", 2, 99).splitlines()[-1] == "    3  b = 2"


def test_arquivo_grande_demais_e_recusado(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import code_index.retrieval.files as files

    monkeypatch.setattr(files, "MAX_BYTES", 10)
    (repo / "grande.txt").write_text("x" * 11)
    with pytest.raises(ValueError, match="grande demais para leitura"):
        read_file(repo, "r", "grande.txt")
    (repo / "ok.txt").write_text("x" * 10)  # no limite, ainda lê
    assert read_file(repo, "r", "ok.txt") == "    1  " + "x" * 10


def test_arquivo_grande_vem_em_partes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import code_index.retrieval.files as files

    monkeypatch.setattr(files, "MAX_LINES", 2)
    (repo / "big.txt").write_text("\n".join(f"l{i}" for i in range(1, 6)))
    out = read_file(repo, "r", "big.txt").splitlines()
    assert out[:2] == ["    1  l1", "    2  l2"]
    assert "linhas 1-2 de 5" in out[-1] and "start_line=3" in out[-1]
    assert read_file(repo, "r", "big.txt", 4).splitlines() == ["    4  l4", "    5  l5"]


def test_mascara_token_npm_em_arquivo_visivel(repo: Path) -> None:
    (repo / "npmrc.example").write_text("//h/:_authToken=12345678-aaaa-bbbb-cccc-1234567890ab\n")
    assert read_file(repo, "r", "npmrc.example") == "    1  //h/:_authToken=***"


def test_linha_da_declaracao_anotada(repo: Path) -> None:
    from code_index.retrieval.files import declaration_line

    (repo / "A.java").write_text("package x;\n\n@Slf4j\n@Service(\"a\")\npublic class CaptchaValidator {\n}\n")
    assert declaration_line(repo, "A.java", 3, "CaptchaValidator") == (5, "public class CaptchaValidator {")
    assert declaration_line(repo, "A.java", 3, "OutraClasse") is None
    assert declaration_line(repo, "nao/existe.java", 3, "X") is None
