"""Estimativa de arquivos pela árvore do GitHub: mesmos filtros do sync, sem clonar."""

from __future__ import annotations

from code_index.domain.models import Repo
from code_index.indexing.estimate import count_indexable
from code_index.infra.github import github_slug


def test_conta_com_os_filtros_do_sync() -> None:
    repo = Repo(name="x", url="https://github.com/a/b")
    files = [
        ("src/main/java/A.java", 10),
        ("src/test/java/ATest.java", 10),     # diretório de teste podado
        ("node_modules/x/a.js", 5),           # ruído
        ("README.md", 3),
        ("a/b/c.py", 10),
        ("big.py", 10**7),                    # acima do limite de tamanho
        ("img.png", 5),                       # fora do include
        (".github/w.yml", 3),                 # pasta oculta
    ]
    assert count_indexable(files, repo, max_bytes=512 * 1024) == 3


def test_exclude_extra_do_repo() -> None:
    repo = Repo(name="x", url="https://github.com/a/b", extra_exclude=("**/wp-includes",))
    assert count_indexable([("wp-includes/a.php", 1), ("index.php", 1)], repo, max_bytes=100) == 1


def test_slug_so_do_github() -> None:
    assert github_slug("https://github.com/acme/api.git") == "acme/api"
    assert github_slug("https://github.com/acme/my.repo") == "acme/my.repo"
    assert github_slug("https://gitlab.com/acme/api.git") is None
    assert github_slug(None) is None
