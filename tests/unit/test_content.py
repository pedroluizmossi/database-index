"""Filtros de conteúdo: redact (mascara segredos antes da Voyage/Postgres) e skip_reason."""

from __future__ import annotations

import pytest

from code_index.indexing.content import MAX_DATA_FILE_BYTES, redact, skip_reason

PEM_CERT = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIJAJ0d\nabc123==\n-----END CERTIFICATE-----"
BASE64_250 = "QUJD" * 62 + "QQ"  # exatamente 250 caracteres de base64, sem quebras


# ---------------------------------------------------------------- casos positivos (mascaram)

@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ('API_KEY = "a8f9d0s9f8d0s9f8";', "python", 'API_KEY = "***";'),
        ("jdbc:postgresql://admin:S3cr3tPw@db", "", "jdbc:postgresql://admin:***@db"),
        ("Password=Abc12345;", "", "Password=***;"),
        ("password: hunter2secret", "yaml", "password: ***"),
        ('"AKIAIOSFODNN7EXAMPLE"', "json", '"***"'),
        ('String password = "hunter2hunter2";', "java", 'String password = "***";'),
        ('const token = "abc123def456";', "javascript", 'const token = "***";'),
        ("secret_key = 'x9y8z7w6'", "python", "secret_key = '***'"),
        ('"password": "hunter2hunter2"', "json", '"password": "***"'),
        ("db_password: s3cr3tpass", "yaml", "db_password: ***"),
        ('token: "abc123xyz"', "yaml", 'token: "***"'),
        ("  password: hunter2secret", "yaml", "  password: ***"),
        ('client_secret = "abcdef123456"', "python", 'client_secret = "***"'),
        ("DB_PASSWORD=Sup3rSecret!", "dotenv", "DB_PASSWORD=***"),
        ("AccountKey=abcdef123456==;", "", "AccountKey=***;"),
        ("postgres://user:pass@host", "", "postgres://user:***@host"),
        ('url = "https://x-access-token:ghp_abc@github.com/a/b.git"', "python",
         'url = "https://x-access-token:***@github.com/a/b.git"'),
        ('token = "ghp_' + "a" * 36 + '"', "python", 'token = "***"'),
        ('x = "xoxb-1234567890-abcdefghij"', "python", 'x = "***"'),
        ('k = "sk-' + "a" * 30 + '"', "python", 'k = "***"'),
        ('k = "sk-ant-' + "a" * 30 + '"', "python", 'k = "***"'),
        ('k = "pa-' + "a" * 30 + '"', "python", 'k = "***"'),
        ('k = "AIzaSyD-' + "a" * 31 + '"', "python", 'k = "***"'),
        ('k = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"',
         "python", 'k = "***"'),
    ],
)
def test_redact_mascara_segredos(text: str, language: str, expected: str) -> None:
    out, count = redact(text, language)

    assert out == expected
    assert count >= 1


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ('password = "mysecretpassword"', "python", 'password = "***"'),
        ("password: supersecret", "yaml", "password: ***"),
    ],
)
def test_senha_so_com_letras_e_mascarada(text: str, language: str, expected: str) -> None:
    assert redact(text, language)[0] == expected


def test_bloco_pem_vira_marcador() -> None:
    out, count = redact(PEM_CERT, "text")

    assert out == "<certificate omitido>"
    assert count == 1


def test_bloco_pem_preserva_o_texto_ao_redor() -> None:
    text = "antes\n-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\ndepois"

    out, _ = redact(text, "text")

    assert out == "antes\n<private key omitido>\ndepois"


def test_base64_longo_vira_marcador() -> None:
    out, count = redact(BASE64_250, "text")

    assert out == "<base64 omitido>"
    assert count == 1


def test_base64_curto_nao_e_mascarado() -> None:
    text = "A" * 199

    assert redact(text, "text") == (text, 0)


# ---------------------------------------------------------------- casos negativos (inalterados)

@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("String password = request.getPassword();", "java"),
        ('ACCESS_KEY_ID = "AccessKeyId"', "python"),
        ("x.stage_create_tenant_access_key=Criação da chave", "properties"),
        ("  Password:\n    Description: password", "yaml"),
        ('{"token.refresh.ttl":"15552000"}', "json"),
        ("accessKey: string?", ""),
        ('X = "platform.functions.cloud9_managed_x"', "python"),
        ("password: ${DB_PASSWORD}", "yaml"),
        ("password: changeme", "yaml"),
        ("password: <your-password>", "yaml"),
        ("# password: hunter2secret", "yaml"),
        ("Password=${DB};", ""),
        ('password = ""', "python"),
        ('password = "short"', "python"),
        ("password = get_password()", "python"),
        ("https://example.com/path", ""),
        ("http://user@host/x", ""),
        ('token = "ghp_short"', "python"),
        ("ok text line\nno secrets here", "text"),
    ],
)
def test_redact_nao_mexe_em_texto_sem_segredo(text: str, language: str) -> None:
    assert redact(text, language) == (text, 0)


# ---------------------------------------------------------------- skip_reason

def test_arquivo_de_dados_grande_e_descartado() -> None:
    big = "a: 1\n" * (MAX_DATA_FILE_BYTES // 5 + 10)

    assert skip_reason("dados.yaml", "yaml", big) == "arquivo de dados grande"


def test_arquivo_de_dados_no_limite_e_mantido() -> None:
    text = "x" * MAX_DATA_FILE_BYTES

    assert skip_reason("dados.json", "json", text) is None


def test_codigo_grande_nao_e_descartado_por_tamanho() -> None:
    text = "x = 1\n" * 100_000

    assert skip_reason("big.py", "python", text) is None


def test_crd_em_yaml_e_descartado() -> None:
    text = "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata:\n  name: x\n"

    assert skip_reason("crd.yaml", "yaml", text) == "CRD"


def test_crd_em_json_e_descartado() -> None:
    assert skip_reason("crd.json", "json", '{"kind": "CustomResourceDefinition"}') == "CRD"


def test_yaml_comum_e_mantido() -> None:
    assert skip_reason("deploy.yaml", "yaml", "kind: Deployment\n") is None


def test_crd_dentro_de_codigo_python_nao_e_descartado() -> None:
    text = 'DOC = "kind: CustomResourceDefinition"\n'

    assert skip_reason("gen.py", "python", text) is None


# ------------------------------------------------------------------ binários


@pytest.mark.parametrize(
    "text",
    [
        "GIF89a\x00\x01\x00\x80",                               # byte nulo
        "\x47\x40\x00\x10" * 50,                                  # MPEG-TS com extensão .ts
        "��\x01\x02abc��\x03\x04" * 100,      # bytes inválidos e de controle
        "h\x00e\x00l\x00l\x00o\x00",                              # UTF-16 sem BOM lido como UTF-8
    ],
)
def test_binario_e_ignorado(text: str) -> None:
    assert skip_reason("video/aula.ts", "typescript", text) == "binário"


@pytest.mark.parametrize(
    "text",
    [
        "export const x = 1;\n\tconsole.log(x)\r\n",
        "// coment�rio com acento em Latin-1\nint a�o = 1;\n" * 20,   # poucos caracteres trocados
        "\x1b[31mvermelho\x1b[0m\n",                                     # ESC de cores ANSI em script
        "",
    ],
)
def test_texto_nao_e_tratado_como_binario(text: str) -> None:
    assert skip_reason("src/app.ts", "typescript", text) is None
