"""Modelos do domínio: validação de Repo/Org e propriedades de estado."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from code_index.domain.file_patterns import DEFAULT_EXCLUDE, DEFAULT_INCLUDE
from code_index.domain.models import NAME_RE, Org, Repo, RepoState, validate_name

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("name", ["acme__proj", "ok-name_1.2", "A"])
def test_nomes_validos(name: str) -> None:
    assert validate_name(name) == name
    assert NAME_RE.match(name)


@pytest.mark.parametrize("name", ["", "com espaco", "org/repo", "acento-é"])
def test_nomes_invalidos(name: str) -> None:
    with pytest.raises(ValueError, match="nome de repositório inválido"):
        validate_name(name)


def test_repo_exige_exatamente_url_ou_path() -> None:
    Repo(name="a", url="https://github.com/o/a.git")
    Repo(name="b", path="/tmp/b")
    with pytest.raises(ValueError):
        Repo(name="c")
    with pytest.raises(ValueError):
        Repo(name="d", url="https://github.com/o/d.git", path="/tmp/d")


def test_repo_com_nome_invalido_nao_e_criado() -> None:
    with pytest.raises(ValueError, match="nome de repositório inválido"):
        Repo(name="org/repo", url="https://github.com/o/r.git")


def test_local_path_de_clone_fica_em_repos_dir(tmp_path: Path) -> None:
    repo = Repo(name="acme__proj", url="https://github.com/acme/proj.git")

    assert repo.local_path(tmp_path / "clones") == tmp_path / "clones" / "acme__proj"


def test_local_path_de_diretorio_local_e_o_proprio_diretorio(tmp_path: Path) -> None:
    repo = Repo(name="meu", path=str(tmp_path))

    assert repo.local_path(tmp_path / "clones") == tmp_path.resolve()


def test_all_excludes_soma_extra_exclude_ao_padrao() -> None:
    repo = Repo(name="a", path="/tmp/a", extra_exclude=("**/out",))

    assert repo.include == tuple(DEFAULT_INCLUDE)
    assert repo.all_excludes == list(DEFAULT_EXCLUDE) + ["**/out"]


def test_org_repo_name_e_to_repo_propagam_filtros() -> None:
    org = Org(name="acme", extra_exclude=("**/gen",))

    assert org.repo_name("proj") == "acme__proj"
    repo = org.to_repo("acme__proj", "https://github.com/acme/proj.git")
    assert repo.url == "https://github.com/acme/proj.git"
    assert repo.extra_exclude == ("**/gen",)


@pytest.mark.parametrize(
    ("language", "rank"),
    [("Python", 0), ("go", 1), ("rust", 2), (None, 2)],
)
def test_language_rank(language: str | None, rank: int) -> None:
    org = Org(name="acme", languages=("python", "go"))

    assert org.language_rank(language) == rank


def _state(**kw) -> RepoState:
    base = {
        "name": "acme__p", "org": "acme", "url": "https://github.com/acme/p.git", "path": None,
        "default_branch": "main", "pushed_at": None, "size_kb": 1, "ignored": False,
        "indexed_commit": None, "revision": None, "indexed_pushed_at": None, "indexed_at": None,
        "error": None, "updated_at": T0,
    }
    base.update(kw)
    return RepoState(**base)


def test_status_do_repo() -> None:
    assert _state().status == "pending"
    assert _state(revision="r").status == "indexed"
    assert _state(revision="r", error="x").status == "error"
    assert _state(revision="r", ignored=True, error="x").status == "ignored"


def test_changed_upstream_so_para_indexado_com_push_mais_novo() -> None:
    assert not _state().changed_upstream
    assert not _state(revision="r").changed_upstream  # sem data de push: nada a comparar
    assert not _state(revision="r", pushed_at=T0, indexed_pushed_at=T0).changed_upstream
    assert _state(revision="r", pushed_at=T0 + timedelta(days=1), indexed_pushed_at=T0).changed_upstream
    assert _state(revision="r", pushed_at=T0, indexed_pushed_at=None).changed_upstream
