"""Settings: caminhos relativos, aliases de variáveis de ambiente e ciclo do Settings do processo."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from code_index import settings as settings_module
from code_index.settings import Settings, bootstrap, get_settings, use_settings


@pytest.fixture(autouse=True)
def _reset_process_settings(monkeypatch: pytest.MonkeyPatch):
    """Restaura o Settings do processo depois de cada teste (use_settings/get_settings mexem nele)."""
    monkeypatch.setattr(settings_module, "_current", None)
    yield


def test_defaults_sao_os_valores_do_projeto(make_settings, tmp_path: Path) -> None:
    s = make_settings()

    assert s.pg_schema == "code_index"
    assert s.chunks_table == "code_chunks"
    assert s.chunks_table_qualified == '"code_index"."code_chunks"'
    assert s.embed_model == "voyage/voyage-code-4"
    assert s.embed_dimensions is None
    assert s.embed_max_inflight == 4
    assert s.rerank_model == "voyage/rerank-2.5"
    assert s.rerank_candidates == 40
    assert s.default_batch == 10
    assert s.max_file_bytes == 512 * 1024
    assert s.github_token is None
    assert s.database_url == "postgres://cocoindex:cocoindex@localhost:5432/cocoindex"


def test_caminhos_relativos_sao_resolvidos_a_partir_de_base_dir(make_settings, tmp_path: Path) -> None:
    base = tmp_path / "projeto"
    base.mkdir()
    s = make_settings(base_dir=base, repos_file="cfg/repos.toml", repos_dir="clones", cocoindex_db=".db")

    assert s.base_dir == base.resolve()
    assert s.repos_file == (base / "cfg" / "repos.toml").resolve()
    assert s.repos_dir == (base / "clones").resolve()
    assert s.cocoindex_db == (base / ".db").resolve()
    assert s.lock_file == (base / ".code-index.lock").resolve()


def test_caminho_absoluto_nao_e_alterado(make_settings, tmp_path: Path) -> None:
    absolute = tmp_path / "outro" / "clones"
    s = make_settings(repos_dir=absolute)

    assert s.repos_dir == absolute


def test_base_dir_com_segmentos_relativos_e_normalizado(make_settings, tmp_path: Path) -> None:
    s = make_settings(base_dir=tmp_path / "a" / ".." / "b")

    assert s.base_dir == (tmp_path / "b").resolve()
    assert s.base_dir.is_absolute()


def test_til_e_expandido(make_settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home-do-usuario"
    monkeypatch.setenv("HOME", str(fake_home))
    s = make_settings(repos_dir="~/clones-code-index")

    assert s.repos_dir == fake_home / "clones-code-index"


def test_aliases_de_ambiente_sao_lidos(make_settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_INDEX_HOME", str(tmp_path))
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@h:5433/db")
    monkeypatch.setenv("EMBED_MODEL", "voyage/outro")
    monkeypatch.setenv("EMBED_DIMENSIONS", "512")
    monkeypatch.setenv("EMBED_MAX_INFLIGHT", "2")
    monkeypatch.setenv("RERANK_MODEL", "")
    monkeypatch.setenv("RERANK_CANDIDATES", "7")
    monkeypatch.setenv("CODE_INDEX_REPOS", "cfg/r.toml")
    monkeypatch.setenv("CODE_INDEX_REPOS_DIR", "clones")
    monkeypatch.setenv("COCOINDEX_DB", "estado")
    monkeypatch.setenv("CODE_INDEX_BATCH", "3")

    s = Settings(_env_file=None)

    assert s.base_dir == tmp_path.resolve()
    assert s.database_url == "postgres://u:p@h:5433/db"
    assert s.embed_model == "voyage/outro"
    assert s.embed_dimensions == 512
    assert s.embed_max_inflight == 2
    assert s.rerank_model == ""
    assert s.rerank_candidates == 7
    assert s.repos_file == (tmp_path / "cfg" / "r.toml").resolve()
    assert s.repos_dir == (tmp_path / "clones").resolve()
    assert s.cocoindex_db == (tmp_path / "estado").resolve()
    assert s.default_batch == 3


def test_github_token_aceita_github_token_e_gh_token(make_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "do-gh")
    assert make_settings().github_token == "do-gh"

    monkeypatch.setenv("GITHUB_TOKEN", "do-github")
    assert make_settings().github_token == "do-github"  # GITHUB_TOKEN tem precedência


def test_embed_kwargs_so_quando_ha_dimensoes(make_settings) -> None:
    assert make_settings().embed_kwargs == {}
    assert make_settings(embed_dimensions=1024).embed_kwargs == {"dimensions": 1024}


def test_settings_e_imutavel(make_settings) -> None:
    s = make_settings()
    with pytest.raises(ValidationError):
        s.pg_schema = "outro"  # type: ignore[misc]


def test_use_settings_substitui_o_settings_do_processo(make_settings) -> None:
    s = make_settings(pg_schema="code_index_test")

    assert use_settings(s) is s
    assert get_settings() is s


def test_get_settings_carrega_do_ambiente_uma_vez(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clean_env) -> None:
    monkeypatch.setenv("CODE_INDEX_HOME", str(tmp_path))
    monkeypatch.setenv("POSTGRES_URL", "postgres://x:y@z/db")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.database_url == "postgres://x:y@z/db"


def test_bootstrap_aponta_cocoindex_db_para_o_settings(make_settings, tmp_path: Path, monkeypatch) -> None:
    # bootstrap carrega o .env do projeto em os.environ: trabalha numa cópia para não vazar entre testes.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    s = make_settings(cocoindex_db=tmp_path / "lmdb")

    result = bootstrap(s)

    assert result is s
    assert os.environ["COCOINDEX_DB"] == str(tmp_path / "lmdb")
