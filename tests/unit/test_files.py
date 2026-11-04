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
