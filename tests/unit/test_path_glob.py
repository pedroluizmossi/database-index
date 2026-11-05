"""domain.path_glob: a mesma semântica de glob na busca (SQL) e no grep."""

from __future__ import annotations

import pytest

from code_index.domain.path_glob import glob_matcher, glob_regex


@pytest.mark.parametrize(
    ("glob", "path", "casa"),
    [
        ("src/**/*.ts", "src/main.ts", True),          # ** pode ser vazio
        ("src/**/*.ts", "src/app/a/b.ts", True),
        ("src/*.ts", "src/main.ts", True),
        ("src/*.ts", "src/app/app.module.ts", False),  # * não atravessa pastas
        ("*.ts", "a/b/c.ts", True),                    # sem '/': nome em qualquer pasta
        ("**/old/**", "old/x.py", True),
        ("**/old/**", "a/old/x.py", True),
        ("**/old/**", "gold/x.py", False),
        ("src/app", "src/app/x.ts", True),             # pasta inclui o conteúdo
        ("src/app", "src/apps/x.ts", False),
        ("file?.txt", "file1.txt", True),
        ("file?.txt", "file12.txt", False),
        ("test_*.py", "tests/test_a.py", True),        # _ e % são literais
        ("100%*", "100%x", True),
        ("a.b", "aXb", False),                         # ponto literal
        ("/src/*.py", "src/a.py", True),               # barra inicial ignorada
    ],
)
def test_semantica(glob: str, path: str, casa: bool) -> None:
    assert glob_matcher(glob)(path) is casa


def test_sem_glob_casa_tudo_e_glob_vazio_e_erro() -> None:
    assert glob_matcher(None)("qualquer/coisa")
    with pytest.raises(ValueError):
        glob_regex("  ")
