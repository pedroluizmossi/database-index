"""Políticas de arquivos (include/exclude) avaliadas com o PatternFilePathMatcher real do CocoIndex.

O walker do CocoIndex poda diretórios com `is_dir_included` e só então testa arquivos com
`is_file_included`. `indexed()` replica esse caminho: um arquivo só entra se nenhum ancestral
for podado e se passar pelo filtro de arquivo.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from cocoindex.resources.file import PatternFilePathMatcher

from code_index.domain.file_patterns import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    NOISE_EXCLUDE,
    TEST_EXCLUDE,
)


@pytest.fixture(scope="module")
def matcher() -> PatternFilePathMatcher:
    return PatternFilePathMatcher(included_patterns=DEFAULT_INCLUDE, excluded_patterns=DEFAULT_EXCLUDE)


def indexed(matcher: PatternFilePathMatcher, rel: str) -> bool:
    path = PurePosixPath(rel)
    for parent in path.parents:
        if parent == PurePosixPath("."):
            continue
        if not matcher.is_dir_included(parent):
            return False
    return matcher.is_file_included(path)


@pytest.mark.parametrize(
    "rel",
    [
        "src/app/main.py", "src/A.java", "lib/x.kt", "svc/x.go", "core/lib.rs", "a/b.cs",
        "web/App.tsx", "web/a.vue", "db/q.sql", "conf/app.yaml", "conf/app.properties",
        "docs/guide.md", "pkg/Dockerfile", "build.gradle", "src/x.swift", "src/x.R", "src/a.hpp",
        "src/main/java/App.java", "a/b/c.ts",
    ],
)
def test_codigo_de_producao_e_incluido(matcher, rel: str) -> None:
    assert indexed(matcher, rel)


@pytest.mark.parametrize(
    "rel",
    [
        # arquivos de teste por nome, em várias linguagens
        "src/test/java/AppTest.java", "src/main/kotlin/FooTest.kt", "web/src/a.test.ts",
        "web/src/a.spec.tsx", "pkg/test_x.py", "pkg/x_test.py", "app/conftest.py", "svc/x_test.go",
        "src/FooTests.cs", "spec/x_spec.rb", "config/jest.config.js", "a/vitest.config.ts",
        "pkg/karma.conf.js", "src/FooSpec.scala", "a/x_test.dart",
        # diretórios de teste / mocks / fixtures de teste (poda da árvore inteira)
        "tests/unit/a.py", "a/tests/x.py", "web/__tests__/a.ts", "src/e2e/a.ts", "a/__mocks__/a.js",
        "a/cypress/x.js", "a/Test/x.cs", "a/x.Tests/Foo.cs", "a/testFixtures/x.java", "src/testdata/x.json",
        "a/__snapshots__/x.js",
    ],
)
def test_testes_sao_excluidos(matcher, rel: str) -> None:
    assert not indexed(matcher, rel)


@pytest.mark.parametrize(
    "rel",
    [
        "a/node_modules/x.js", "a/dist/x.js", "src/build/a.py", "src/target/a.py", "src/venv/a.py",
        "src/vendor/a.go", "src/__pycache__/x.py", "a/.venv/x.py", ".github/workflows/ci.yml", "a/.env",
        "web/app.min.js", "web/app.min.css", "package-lock.json", "pnpm-lock.yaml",
    ],
)
def test_ruido_de_build_dependencias_e_ocultos_e_excluido(matcher, rel: str) -> None:
    assert not indexed(matcher, rel)


@pytest.mark.parametrize(
    "rel",
    [
        "i18n/it/x.json", "api/spec/a.yaml", "db/migration/PostgreSql/V1__init.sql",
        "db/migration/PostgreSql/x.sql", "src/migration/PostgreSql/V2.sql",
    ],
)
def test_arquivos_que_devem_ficar_no_indice(matcher, rel: str) -> None:
    assert indexed(matcher, rel)


@pytest.mark.parametrize(
    "rel",
    [
        "openapi.json", "api/openapi.yaml", "api/swagger.yml", "CHANGELOG.md", "docs/HISTORY.md",
        "RELEASE_NOTES.md", "db/migration/H2/V1.sql", "src/migration/a/Oracle/x.sql",
        "db/migrations/mysql/a.sql", "db/migration/SqlServer/V2.sql",
    ],
)
def test_ruido_de_versoes_openapi_e_migrations_de_outros_bancos_e_excluido(matcher, rel: str) -> None:
    assert not indexed(matcher, rel)


def test_extra_exclude_do_repo_entra_no_filtro(matcher) -> None:
    # Repo.all_excludes = exclude + extra_exclude: o padrão extra precisa podar de fato.
    with_extra = PatternFilePathMatcher(
        included_patterns=DEFAULT_INCLUDE, excluded_patterns=list(DEFAULT_EXCLUDE) + ["**/out"]
    )
    assert indexed(matcher, "lib/out/gen.java")
    assert not indexed(with_extra, "lib/out/gen.java")


def test_listas_de_teste_e_ruido_fazem_parte_do_padrao_de_exclusao() -> None:
    assert set(TEST_EXCLUDE) <= set(DEFAULT_EXCLUDE)
    assert set(NOISE_EXCLUDE) <= set(DEFAULT_EXCLUDE)
