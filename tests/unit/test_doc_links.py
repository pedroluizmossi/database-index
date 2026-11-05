"""domain/doc_links: apelidos de repos (qualquer stack), menções de páginas e front-matter."""

from __future__ import annotations

import fnmatch

import pytest

from code_index.domain.doc_links import (
    alias_is_strong, blank_lines, glob_to_like, manifest_aliases, page_mentions, page_path, parse_front_matter,
    repo_name_aliases,
)


def matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g.replace("**/", "*")) or fnmatch.fnmatch(path, g) for g in globs)


def test_apelidos_do_nome_do_repo() -> None:
    assert [a for a, _ in repo_name_aliases("minha-org__snr-email-backend")] == [
        "minha-org__snr-email-backend", "minha-org/snr-email-backend", "snr-email-backend", "email-backend",
    ]
    assert [a for a, _ in repo_name_aliases("projeto-local")] == ["projeto-local"]


@pytest.mark.parametrize(
    ("filename", "text", "expected"),
    [
        ("package.json", '{"name": "@minha-org/email-frontend", "version": "1.0.0"}',
         ["@minha-org/email-frontend", "email-frontend"]),
        ("pom.xml", """<project xmlns="http://maven.apache.org/POM/4.0.0">
            <parent><artifactId>parent-pom</artifactId></parent>
            <artifactId>audit-library</artifactId></project>""", ["audit-library"]),
        ("pyproject.toml", '[project]\nname = "relatorios-python"\n', ["relatorios-python"]),
        ("pyproject.toml", '[tool.poetry]\nname = "etl-jobs"\n', ["etl-jobs"]),
        ("setup.cfg", "[metadata]\nname = legacy-tool\n", ["legacy-tool"]),
        ("go.mod", "module github.com/minha-org/webhook-relay\n\ngo 1.22\n",
         ["github.com/minha-org/webhook-relay", "webhook-relay"]),
        ("Cargo.toml", '[package]\nname = "fast-parser"\n', ["fast-parser"]),
        ("settings.gradle", "rootProject.name = 'mobile-app'\n", ["mobile-app"]),
        ("catalog-info.yaml", """apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: email-frontend
  annotations:
    sonarqube.org/project-key: arquitetura-email-frontend
    grafana/tag-selector: ignorado
""", ["email-frontend", "arquitetura-email-frontend"]),
        ("package.json", "{ quebrado", []),
        ("pom.xml", "<project>${artifactId}</project>", []),
    ],
)
def test_apelidos_de_manifestos_de_varias_stacks(filename: str, text: str, expected: list[str]) -> None:
    assert [a for a, _ in manifest_aliases(filename, text)] == expected


@pytest.mark.parametrize(
    ("alias", "strong"),
    [("email-backend", True), ("@minha-org/ui-kit", True), ("functions", False), ("email", False),
     ("arquiteturaplataforma", True), ("core-lib", True), ("ui-kit", False)],
)
def test_apelido_forte_ou_fraco(alias: str, strong: bool) -> None:
    assert alias_is_strong(alias) is strong


def test_front_matter_e_linhas_preservadas() -> None:
    text = '---\ntitle: "Serviço de e-mail"\nurl: "https://w/pt-br/E"\ntags: ["email-backend"]\n---\n\n# Email\n'
    meta, n = parse_front_matter(text)
    assert meta == {"title": "Serviço de e-mail", "url": "https://w/pt-br/E", "tags": ["email-backend"]}
    blanked = blank_lines(text, n)
    assert blanked.count("\n") == text.count("\n") and blanked.split("\n")[6] == "# Email"
    assert parse_front_matter("# sem front-matter") == ({}, 0)


def test_caminho_da_pagina_e_padrao_like() -> None:
    assert page_path("pt-br/Plataforma/Email.md") == "Plataforma/Email"
    assert glob_to_like("*email*") == "%email%"
    assert glob_to_like("snr-email-backend") == r"snr-email-backend"
    assert glob_to_like("org__snr-x") == r"org\_\_snr-x"


def test_mencoes_da_pagina() -> None:
    body = "Ver https://github.com/minha-org/snr-email-backend e o email-backend. Fala de email."
    m = page_mentions("Plataforma/Email", ["Email-Frontend"], body, [("Plataforma/**", ("*email*",))], matches)
    assert ("%email%", "explicit") in m
    assert ("email-frontend", "tag") in m
    assert ("minha-org__snr-email-backend", "url") in m
    assert ("email-backend", "mention") in m
    # regra que não casa com o caminho não gera vínculo
    assert not any(o == "explicit" for _, o in page_mentions("Outro/X", [], "", [("Plataforma/**", ("*",))], matches))
