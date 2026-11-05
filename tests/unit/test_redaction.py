"""retrieval.redaction.mask_output: redact da indexação + regras só de saída."""

from __future__ import annotations

from code_index.retrieval.redaction import mask_output

TOKEN = "12345678-aaaa-bbbb-cccc-1234567890ab"


def test_mascara_token_do_npmrc() -> None:
    out = mask_output(f"//maven.example.com/api/npm/npm/:_authToken={TOKEN}\n", "")
    assert TOKEN not in out and out.startswith("//maven.example.com/api/npm/npm/:_authToken=***")
    assert mask_output("//h/:_auth=dXNlcjpzM2NyZXQ=", "") == "//h/:_auth=***"
    assert mask_output("_password=\"abc12345\"", "") == '_password="***"'


def test_mantem_variavel_de_ambiente_e_registry() -> None:
    assert mask_output("//h/:_authToken=${NPM_TOKEN}", "") == "//h/:_authToken=${NPM_TOKEN}"
    line = "@fortawesome:registry=https://maven.example.com/npm/"
    assert mask_output(line, "") == line


def test_mascara_token_npm_e_regras_do_redact() -> None:
    assert mask_output("x npm_aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5 y", "") == "x *** y"
    assert mask_output('API_KEY = "a8f9d0s9f8d0s9f8"', "python") == 'API_KEY = "***"'
