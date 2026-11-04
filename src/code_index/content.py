"""Filtros de conteúdo aplicados antes de chunk/embedding.

- `redact`: mascara segredos, blocos PEM e blobs base64. O texto mascarado é o que
  vai para a Voyage e para o Postgres, então segredos não saem da máquina nem
  ficam expostos via MCP.
- `skip_reason`: descarta arquivos de dados que só fazem ruído (CRDs de terceiros,
  yaml/json gigantes).
"""

from __future__ import annotations

import hashlib
import pathlib
import re

import cocoindex as coco

# Qualquer edição deste arquivo muda o hash e invalida o memo do process_file
# (e dos index_repo acima), forçando reaplicar as regras aos repos já indexados.
# Só os trechos cujo texto mudou são reembedados.
CONTENT_LOGIC = coco.LogicDep(hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest())

MASK = "***"

# Linguagens de config: `chave: valor` sem aspas é comum e o valor costuma ser literal.
_CONFIG_LANGS = {"yaml", "json", "xml", "toml", "ini", "properties", "dotenv", "bash", "shell", "sh", "hcl", ""}
_DATA_LANGS = {"yaml", "json", "xml"}
MAX_DATA_FILE_BYTES = 100 * 1024

_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----[\s\S]*?-----END \1-----")
_BASE64_BLOB = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}")
_TOKENS = [
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                          # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),                          # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),                            # GitLab
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),                       # Slack
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"),                # OpenAI / Anthropic
    re.compile(r"\bpa-[A-Za-z0-9_-]{30,}\b"),                               # Voyage
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                               # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
]
# usuário:senha@host em URLs
_URL_CREDS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:)([^/\s@'\"]+)(@)", re.I)
# Password=...; em connection strings (qualquer linguagem)
_CONN_STR = re.compile(r"(?i)\b(AccountKey|SharedAccessKey|Password|Pwd)=([^;\"'\s]{4,})")

_KEY = r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_.-]?key|access[_.-]?key|private[_.-]?key|client[_.-]?secret|credential)s?[\w.-]*"
# Código: só literais entre aspas (evita mascarar `password = req.getPassword()`).
_CODE_ASSIGN = re.compile(rf"(?i)(\b{_KEY}\b[\"']?[ \t]*[:=]+[ \t]*)([\"'])([^\"'\s]{{6,}})\2")
# Config: aceita valor sem aspas (yaml/properties/env).
_CONFIG_ASSIGN = re.compile(
    rf"(?im)^([ \t]*[\"']?{_KEY}[\"']?[ \t]*[:=][ \t]*)([\"']?)([^\s\"'#,}}]{{6,}})\2(?=[ \t]*(?:[,}}\]#]|$))")


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    return (v.startswith(("${", "{{", "%(", "#{", "<", "$")) or v.lower() in {"changeme", "password", "secret", "null", "none", "true", "false"}
            or v.isupper() and "_" in v  # nomes de variável, ex.: DB_PASSWORD
            or re.fullmatch(r"[A-Za-z]+(?:[_.-][A-Za-z]+)*", v) is not None  # identificador: AccessKeyId, auth_token_key
            or re.fullmatch(r"\d+[smhdSMHD]?", v) is not None  # número/duração: 15552000, 30d
            or re.fullmatch(r"[a-z0-9]+(?:[_.-][a-z0-9]+){2,}", v) is not None  # chave i18n: platform.x.cloud9_y
            or re.fullmatch(r"(?i)(string|integer|int|long|double|boolean|bool|binary|date|datetime|object|number|uuid|blob|any)(\?|\[\])*", v) is not None)  # tipo (SDL)


def redact(text: str, language: str) -> tuple[str, int]:
    count = 0

    def sub(pattern: re.Pattern, repl, s: str) -> str:
        nonlocal count
        new, n = pattern.subn(repl, s)
        count += n
        return new

    text = sub(_PEM, lambda m: f"<{m.group(1).lower()} omitido>", text)
    text = sub(_BASE64_BLOB, "<base64 omitido>", text)
    for pat in _TOKENS:
        text = sub(pat, MASK, text)
    text = sub(_URL_CREDS, rf"\1{MASK}\3", text)
    def conn(m: re.Match) -> str:
        nonlocal count
        if _is_placeholder(m.group(2)):
            return m.group(0)
        count += 1
        return f"{m.group(1)}={MASK}"

    text = _CONN_STR.sub(conn, text)

    def assign(m: re.Match) -> str:
        nonlocal count
        if _is_placeholder(m.group(3)):
            return m.group(0)
        count += 1
        return f"{m.group(1)}{m.group(2)}{MASK}{m.group(2)}"

    text = _CODE_ASSIGN.sub(assign, text)
    if language in _CONFIG_LANGS:
        text = _CONFIG_ASSIGN.sub(assign, text)
    return text, count


def skip_reason(path: str, language: str, text: str) -> str | None:
    if language in _DATA_LANGS:
        if len(text.encode()) > MAX_DATA_FILE_BYTES:
            return "arquivo de dados grande"
        if re.search(r"(?m)^\s*kind:\s*CustomResourceDefinition\b", text) or '"kind": "CustomResourceDefinition"' in text:
            return "CRD"
    return None
