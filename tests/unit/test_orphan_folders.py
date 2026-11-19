"""Limpeza das pastas órfãs: só na pasta de clones que pertence ao schema (pasta compartilhada não perde clones)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from code_index.indexing.orchestrator import REPOS_DIR_OWNER, Indexer, _claim_repos_dir


def _clone(root: Path, name: str) -> None:
    (root / name / ".git").mkdir(parents=True)


def _indexer(root: Path, schema: str, known: list[str]) -> Any:
    async def all_rows() -> dict[str, Any]:
        return {n: None for n in known}

    async def empty() -> list[Any]:
        return []

    return SimpleNamespace(_settings=SimpleNamespace(repos_dir=root, pg_schema=schema),
                           _repos=SimpleNamespace(all_rows=all_rows), _config=SimpleNamespace(repos=empty, docs=empty))


def test_sem_marcador_marca_e_nao_apaga(tmp_path: Path) -> None:
    assert _claim_repos_dir(tmp_path, "a") is None
    assert (tmp_path / REPOS_DIR_OWNER).read_text().strip() == "a"
    assert _claim_repos_dir(tmp_path, "b") == "a"


async def test_pasta_de_outro_schema_nao_perde_clones(tmp_path: Path) -> None:
    for n in ("x", "y", "z"):
        _clone(tmp_path, n)
    # primeira rodada de um índice novo (outro schema, conhece só "x"): marca a pasta e não apaga nada
    await Indexer._prune_orphan_folders(_indexer(tmp_path, "teste", ["x"]))
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ["x", "y", "z"]
    # outro schema numa pasta já marcada: também não apaga
    await Indexer._prune_orphan_folders(_indexer(tmp_path, "code_index", ["x"]))
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ["x", "y", "z"]


async def test_dono_apaga_so_as_orfas(tmp_path: Path) -> None:
    for n in ("x", "y"):
        _clone(tmp_path, n)
    (tmp_path / "solta").mkdir()            # não foi o code-index que criou: fica
    (tmp_path / REPOS_DIR_OWNER).write_text("code_index\n")
    await Indexer._prune_orphan_folders(_indexer(tmp_path, "code_index", ["x"]))
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ["solta", "x"]
