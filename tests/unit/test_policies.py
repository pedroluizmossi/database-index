"""Políticas puras: nomes, origem dos repos e seleção do próximo lote (domain/policies.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from code_index.domain.models import Org, RepoState
from code_index.domain.policies import (
    name_from_path,
    name_from_url,
    normalize_source,
    pick_batch,
    resolve_name,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def state(
    name: str,
    *,
    org: str | None = None,
    path: str | None = None,
    pushed: datetime | None = None,
    indexed_pushed: datetime | None = None,
    revision: str | None = None,
    error: str | None = None,
    ignored: bool = False,
    updated: datetime = T0,
    language: str | None = None,
) -> RepoState:
    url = None if path else f"https://github.com/x/{name}.git"
    return RepoState(
        name=name, org=org, url=url, path=path, default_branch="main", pushed_at=pushed, size_kb=10,
        ignored=ignored, indexed_commit="abc" if revision else None, revision=revision,
        indexed_pushed_at=indexed_pushed, indexed_at=T0 if revision else None, error=error,
        updated_at=updated, language=language,
    )


def rows(*items: RepoState) -> dict[str, RepoState]:
    return {r.name: r for r in items}


# ---------------------------------------------------------------- nomes e origem

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/org/proj.git", "org__proj"),
        ("https://github.com/org/proj", "org__proj"),
        ("https://github.com/org/proj/", "org__proj"),
        ("git@github.com:org/proj.git", "org__proj"),
        ("https://github.com/org/my.repo-x.git", "org__my.repo-x"),
    ],
)
def test_name_from_url(url: str, expected: str) -> None:
    assert name_from_url(url) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/pedro/Projetos/api-gateway/", "api-gateway"),
        ("/tmp/Meu Projeto", "Meu_Projeto"),
    ],
)
def test_name_from_path(path: str, expected: str) -> None:
    assert name_from_path(path) == expected


def test_normalize_source_diretorio_local(tmp_path) -> None:
    assert normalize_source(str(tmp_path)) == (None, str(tmp_path.resolve()))


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("org/repo", "https://github.com/org/repo.git"),
        ("https://github.com/org/repo.git", "https://github.com/org/repo.git"),
        ("git@github.com:org/repo.git", "git@github.com:org/repo.git"),
        ("ssh://git@host/org/repo.git", "ssh://git@host/org/repo.git"),
    ],
)
def test_normalize_source_remotos(source: str, url: str) -> None:
    assert normalize_source(source) == (url, None)


@pytest.mark.parametrize("source", ["nao-existe", "./nao-existe/x"])
def test_normalize_source_desconhecido_nao_e_aceito(source: str, monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    assert normalize_source(source) == (None, None)


# ---------------------------------------------------------------- resolve_name

@pytest.mark.parametrize(
    ("name", "known", "expected"),
    [
        ("acme__proj", ["acme__proj", "x__y"], "acme__proj"),
        ("acme/proj", ["acme__proj"], "acme__proj"),
        ("proj", ["acme__proj", "x__other"], "acme__proj"),
        ("missing", ["acme__proj"], None),
        ("pro", ["acme__proj"], None),  # sufixo parcial não casa
        ("https://github.com/acme/proj", ["acme__proj"], None),
        ("proj", ["proj", "acme__proj"], "proj"),  # nome exato tem precedência
    ],
)
def test_resolve_name(name: str, known: list[str], expected: str | None) -> None:
    assert resolve_name(name, known) == expected


def test_resolve_name_ambiguo_levanta_erro() -> None:
    with pytest.raises(ValueError, match="ambíguo"):
        resolve_name("proj", ["a__proj", "b__proj"])


# ---------------------------------------------------------------- pick_batch

def test_ordem_alterados_depois_pendentes_depois_com_erro() -> None:
    org = Org(name="acme")
    changed = state(
        "acme__old", org="acme", pushed=T0 + timedelta(days=1), indexed_pushed=T0, revision="r1"
    )
    pending = state("acme__new", org="acme")
    failed = state("acme__broken", org="acme", error="boom", updated=T0 - timedelta(days=5))

    names, remaining = pick_batch(rows(failed, pending, changed), None, [org])

    assert names == ["acme__old", "acme__new", "acme__broken"]
    assert remaining == 0


def test_lote_limita_e_informa_quantos_ficaram_na_fila() -> None:
    items = [state(f"acme__r{i}", org="acme", pushed=T0 + timedelta(hours=i)) for i in range(4)]

    names, remaining = pick_batch(rows(*items), 2, [Org(name="acme")])

    assert len(names) == 2
    assert remaining == 2


def test_lote_maior_que_a_fila_pega_tudo() -> None:
    names, remaining = pick_batch(rows(state("a__x"), state("b__y")), 10)

    assert sorted(names) == ["a__x", "b__y"]
    assert remaining == 0


def test_ignorados_e_indexados_sem_mudanca_ficam_de_fora() -> None:
    ignored = state("acme__skip", org="acme", ignored=True)
    unchanged = state(
        "acme__same", org="acme", pushed=T0, indexed_pushed=T0 + timedelta(days=1), revision="r1"
    )
    no_push_info = state("acme__nopush", org="acme", pushed=None, revision="r2")

    names, remaining = pick_batch(rows(ignored, unchanged, no_push_info), None, [Org(name="acme")])

    assert names == []
    assert remaining == 0


def test_indexado_de_org_sem_indexed_pushed_conta_como_alterado() -> None:
    never_stamped = state("acme__x", org="acme", pushed=T0, indexed_pushed=None, revision="r1")
    pending = state("acme__y", org="acme")

    names, _ = pick_batch(rows(pending, never_stamped), None, [Org(name="acme")])

    assert names == ["acme__x", "acme__y"]


def test_repo_avulso_indexado_entra_sempre_nos_alterados() -> None:
    local = state("meu-projeto", path="/tmp/meu-projeto", revision="local#1")
    pending = state("novo", path="/tmp/novo")

    names, _ = pick_batch(rows(pending, local), None)

    assert names == ["meu-projeto", "novo"]


def test_repo_avulso_com_url_so_entra_se_head_remoto_mudou() -> None:
    manual_same = state("url-same", revision="r1")
    manual_changed = state("url-changed", revision="r2")

    names, _ = pick_batch(
        rows(manual_same, manual_changed), None, manual_changed={"url-changed"}
    )

    assert names == ["url-changed"]


def test_ordem_dentro_do_grupo_por_posicao_da_linguagem_e_push_recente() -> None:
    org = Org(name="acme", languages=("python", "go"))
    items = [
        state("acme__a", org="acme", language="Go", pushed=T0 + timedelta(days=5)),
        state("acme__b", org="acme", language="python", pushed=T0 + timedelta(days=1)),
        state("acme__c", org="acme", language="rust", pushed=T0 + timedelta(days=10)),
        state("acme__d", org="acme", language=None, pushed=T0 + timedelta(days=9)),
        state("acme__e", org="acme", language="Python", pushed=T0 + timedelta(days=3)),
    ]

    names, _ = pick_batch(rows(*items), None, [org])

    # python (rank 0, o mais recente primeiro), go (rank 1), depois fora da lista (rank 2)
    assert names == ["acme__e", "acme__b", "acme__a", "acme__c", "acme__d"]


def test_com_erro_prevalece_sobre_indexado_alterado() -> None:
    changed_but_failing = state(
        "acme__x", org="acme", pushed=T0 + timedelta(days=1), indexed_pushed=T0,
        revision="r1", error="falha no git",
    )
    pending = state("acme__p", org="acme")

    names, _ = pick_batch(rows(changed_but_failing, pending), None, [Org(name="acme")])

    assert names == ["acme__p", "acme__x"]


def test_falhas_mais_antigas_primeiro() -> None:
    recent = state("a__recent", error="e", updated=T0 + timedelta(days=2))
    old = state("a__old", error="e", updated=T0)

    names, _ = pick_batch(rows(recent, old), None)

    assert names == ["a__old", "a__recent"]


def test_wiki_nao_bloqueia_pendentes_e_alterna_com_erros_e_outras_wikis() -> None:
    wiki1 = replace(state("docs__one", revision="docs:1", updated=T0), kind="docs")
    wiki2 = replace(state("docs__two", revision="docs:2", updated=T0 + timedelta(seconds=1)), kind="docs")
    failed = state("falhou", error="erro", updated=T0 + timedelta(seconds=2))
    pending = state("novo")
    assert pick_batch(rows(wiki1, wiki2, failed, pending), 1)[0] == ["novo"]
    assert pick_batch(rows(wiki1, wiki2, failed), 1)[0] == ["docs__one"]
    wiki1 = replace(wiki1, updated_at=T0 + timedelta(seconds=3))
    assert pick_batch(rows(wiki1, wiki2, failed), 1)[0] == ["docs__two"]
    wiki2 = replace(wiki2, updated_at=T0 + timedelta(seconds=4))
    assert pick_batch(rows(wiki1, wiki2, failed), 1)[0] == ["falhou"]
