"""Políticas de seleção de arquivos: o que entra e o que fica fora do índice.

Puro (sem I/O). Os globs seguem o PatternFilePathMatcher do CocoIndex; um padrão de
diretório (ex.: `**/test`) poda a árvore inteira abaixo dele.
"""

from __future__ import annotations

from collections.abc import Sequence

# Include padrão do núcleo em dois blocos: os globs dos tipos de arquivo dos perfis de organização
# (OrgProfile.file_types) entram entre eles (`default_include`). Posição fixa: a lista final faz parte da chave de
# memo do CocoIndex (Repo.include), e mudar a ordem revarreria todos os repos.
_INCLUDE_CODE_AND_DOCS = [
    "**/*.py", "**/*.pyi", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.mjs",
    "**/*.java", "**/*.kt", "**/*.kts", "**/*.scala", "**/*.go", "**/*.rs",
    "**/*.c", "**/*.h", "**/*.cc", "**/*.cpp", "**/*.hpp", "**/*.cs", "**/*.fs",
    "**/*.rb", "**/*.php", "**/*.swift", "**/*.m", "**/*.dart", "**/*.lua",
    "**/*.sh", "**/*.bash", "**/*.zsh", "**/*.ps1", "**/*.sql", "**/*.r", "**/*.R",
    "**/*.vue", "**/*.svelte", "**/*.html", "**/*.css", "**/*.scss",
    "**/*.toml", "**/*.yaml", "**/*.yml", "**/*.json", "**/*.xml", "**/*.gradle",
    "**/*.md", "**/*.mdx", "**/*.rst", "**/Dockerfile", "**/Makefile",
]
_INCLUDE_CONFIG_AND_INFRA = [
    "**/*.properties",
    # infraestrutura e contratos
    "**/*.tf", "**/*.tfvars", "**/*.hcl", "**/*.proto", "**/*.graphql", "**/*.gql",
    "**/Jenkinsfile", "**/*.groovy", "**/*.tpl",
    # variações de JS/TS e estilos
    "**/*.cjs", "**/*.mts", "**/*.cts", "**/*.less", "**/*.sass",
]
DEFAULT_INCLUDE = _INCLUDE_CODE_AND_DOCS + _INCLUDE_CONFIG_AND_INFRA
DEFAULT_EXCLUDE = [
    "**/.*", "**/node_modules", "**/vendor", "**/dist", "**/build", "**/target",
    "**/__pycache__", "**/venv", "**/*.min.js", "**/*.min.css", "**/*.lock",
    "**/package-lock.json", "**/pnpm-lock.yaml", "**/*.map",
]

# Testes não ajudam a entender o código de produção e custam tokens: fora por padrão.
TEST_EXCLUDE = [
    # diretórios de teste (Maven/Gradle src/test, Python tests/, JS __tests__, e2e, fixtures...)
    "**/test", "**/tests", "**/Test", "**/Tests", "**/__tests__", "**/__test__",
    "**/e2e", "**/integration-test", "**/integrationTest",
    "**/androidTest", "**/testFixtures", "**/testdata", "**/test-data",
    "**/__mocks__", "**/__fixtures__", "**/__snapshots__", "**/cypress", "**/playwright",
    # Java / Kotlin / Scala / Groovy
    "**/*Test.java", "**/*Tests.java", "**/*IT.java", "**/*TestCase.java",
    "**/*Test.kt", "**/*Tests.kt", "**/*Test.scala", "**/*Spec.scala", "**/*Spec.groovy", "**/*Test.groovy",
    # JS / TS
    "**/*.test.js", "**/*.test.jsx", "**/*.test.ts", "**/*.test.tsx", "**/*.test.mjs",
    "**/*.spec.js", "**/*.spec.jsx", "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.mjs",
    "**/*.cy.js", "**/*.cy.ts", "**/karma.conf.js", "**/jest.config.*", "**/vitest.config.*",
    # Python
    "**/test_*.py", "**/*_test.py", "**/conftest.py",
    # Go / Rust / Dart / Elixir / C++
    "**/*_test.go", "**/*_test.rs", "**/*_test.dart", "**/*_test.exs", "**/*_test.cc", "**/*_test.cpp",
    # C# / .NET
    "**/*Test.cs", "**/*Tests.cs", "**/*.Tests", "**/*.UnitTests", "**/*.IntegrationTests",
    # Ruby / PHP / Swift
    "**/*_spec.rb", "**/*_test.rb", "**/*Test.php", "**/*Tests.swift", "**/*Test.swift",
]
# Ruído: histórico de versões e especificações OpenAPI (indexadas por outra fonte). Ruído próprio de uma
# organização (ex.: migrations duplicadas por banco) vem do perfil (OrgProfile.noise_exclude, `default_exclude`).
NOISE_EXCLUDE = [
    "**/CHANGELOG*", "**/CHANGES*", "**/HISTORY*", "**/RELEASE_NOTES*", "**/RELEASE-NOTES*",
    "**/openapi.json", "**/openapi.yaml", "**/openapi.yml", "**/*.openapi.json", "**/*.openapi.yaml",
    "**/swagger.json", "**/swagger.yaml", "**/swagger.yml",
]
DEFAULT_EXCLUDE = DEFAULT_EXCLUDE + TEST_EXCLUDE + NOISE_EXCLUDE


def default_include(extra_globs: Sequence[str] = ()) -> list[str]:
    """Include padrão de um repo: o do núcleo com os globs dos tipos de arquivo dos perfis ativos (sem repetir
    padrões), na posição fixa entre os blocos de código/docs e de configuração/infra."""
    seen = set(DEFAULT_INCLUDE)
    extra = [g for g in dict.fromkeys(extra_globs) if g not in seen]
    return _INCLUDE_CODE_AND_DOCS + extra + _INCLUDE_CONFIG_AND_INFRA


def default_exclude(extra_noise: Sequence[str] = ()) -> list[str]:
    """Exclude padrão de um repo: o do núcleo seguido do ruído dos perfis ativos (sem repetir padrões)."""
    out = list(DEFAULT_EXCLUDE)
    seen = set(out)
    for pattern in extra_noise:
        if pattern not in seen:
            seen.add(pattern)
            out.append(pattern)
    return out
