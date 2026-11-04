"""Políticas de seleção de arquivos: o que entra e o que fica fora do índice.

Puro (sem I/O). Os globs seguem o PatternFilePathMatcher do CocoIndex; um padrão de
diretório (ex.: `**/test`) poda a árvore inteira abaixo dele.
"""

from __future__ import annotations

DEFAULT_INCLUDE = [
    "**/*.py", "**/*.pyi", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.mjs",
    "**/*.java", "**/*.kt", "**/*.kts", "**/*.scala", "**/*.go", "**/*.rs",
    "**/*.c", "**/*.h", "**/*.cc", "**/*.cpp", "**/*.hpp", "**/*.cs", "**/*.fs",
    "**/*.rb", "**/*.php", "**/*.swift", "**/*.m", "**/*.dart", "**/*.lua",
    "**/*.sh", "**/*.bash", "**/*.zsh", "**/*.ps1", "**/*.sql", "**/*.r", "**/*.R",
    "**/*.vue", "**/*.svelte", "**/*.html", "**/*.css", "**/*.scss",
    "**/*.toml", "**/*.yaml", "**/*.yml", "**/*.json", "**/*.xml", "**/*.gradle",
    "**/*.md", "**/*.mdx", "**/*.rst", "**/Dockerfile", "**/Makefile",
    "**/*.sdl", "**/*.pdl", "**/*.properties",
]
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
# Ruído: histórico de versões, especificações OpenAPI (indexadas por outra fonte) e
# migrations duplicadas por banco (mantemos só PostgreSql).
_DB_VENDORS = ["H2", "h2", "Oracle", "oracle", "SqlServer", "SQLServer", "sqlserver", "MySql", "MySQL", "mysql"]
NOISE_EXCLUDE = [
    "**/CHANGELOG*", "**/CHANGES*", "**/HISTORY*", "**/RELEASE_NOTES*", "**/RELEASE-NOTES*",
    "**/openapi.json", "**/openapi.yaml", "**/openapi.yml", "**/*.openapi.json", "**/*.openapi.yaml",
    "**/swagger.json", "**/swagger.yaml", "**/swagger.yml",
] + [f"**/{d}/**/{v}" for d in ("migration", "migrations") for v in _DB_VENDORS]
DEFAULT_EXCLUDE = DEFAULT_EXCLUDE + TEST_EXCLUDE + NOISE_EXCLUDE
