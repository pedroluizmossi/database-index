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
from cocoindex.ops.text import detect_code_language

# Qualquer edição deste arquivo muda o hash e invalida o memo do process_file
# (e dos index_repo acima), forçando reaplicar as regras aos repos já indexados.
# Só os trechos cujo texto mudou são reembedados.
CONTENT_LOGIC = coco.LogicDep(hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest())

MASK = "***"

# Linguagens de config: `chave: valor` sem aspas é comum e o valor costuma ser literal.
_CONFIG_LANGS = {"yaml", "json", "xml", "toml", "ini", "properties", "dotenv", "bash", "shell", "sh", "hcl", "sdl", "pdl", ""}
# Extensões que o detector do CocoIndex não conhece, mas que têm regras próprias aqui e em context.py.
_EXTRA_LANGS = {"sdl": "sdl", "pdl": "pdl"}
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
# usuário:senha@host em URLs (usuário pode faltar: redis://:senha@host)
_URL_CREDS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]*:)([^/\s@'\"]+)(@)", re.I)
# .npmrc / .yarnrc: //registry.host/caminho/:_authToken=valor (também _auth e _password)
# Em qualquer ponto da linha: aparece também citado em docs ("Valor atual: ...:_authToken=...").
_NPMRC_AUTH = re.compile(r"(?i)(?<!\w)(:?_(?:authToken|auth|password)[ \t]*[=:][ \t]*[\"']?)([^\s\"';,)]+)")
_NPM_TOKEN = re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")
# Password=...; em connection strings (qualquer linguagem)
_CONN_STR = re.compile(r"(?i)\b(AccountKey|SharedAccessKey|Password|Pwd)=([^;\"'\s]{4,})")

_KEY = r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_.-]?key|access[_.-]?key|private[_.-]?key|client[_.-]?secret|credential)s?[\w.-]*"
# Código: só literais entre aspas (evita mascarar `password = req.getPassword()`).
_CODE_ASSIGN = re.compile(rf"(?i)(\b{_KEY}\b[\"']?[ \t]*[:=]+[ \t]*)([\"'])([^\"'\s]{{6,}})\2")
# Config: aceita valor sem aspas (yaml/properties/env), também após `export`/`ENV`/`ARG`/`set` (shell, Dockerfile).
_CONFIG_ASSIGN = re.compile(
    rf"(?im)^([ \t]*(?:(?:export|ENV|ARG|set)[ \t]+)?[\"']?{_KEY}[\"']?[ \t]*[:=][ \t]*)([\"']?)([^\s\"'#,}}]{{6,}})\2(?=[ \t]*(?:[,}}\]#]|$))")
# Bloco cercado de markdown (``` ou ~~~): yaml/env/properties citados em docs e páginas da wiki.
_MD_FENCE = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]*\1[ \t]*$|\Z)")


_UUID = re.compile(r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    if _UUID.fullmatch(v):  # client_secret/apiKey em UUID: segredo (a regra de chave i18n abaixo o aceitaria)
        return False
    return (v.startswith(("${", "{{", "%(", "#{", "<", "$")) or v.lower() in {"changeme", "password", "secret", "null", "none", "true", "false"}
            or v.isupper() and "_" in v  # nomes de variável, ex.: DB_PASSWORD
            # identificador (exige forma de identificador; palavra solta como "mysecretpassword" É mascarada)
            or re.fullmatch(r"[a-z]+(?:[A-Z][a-z0-9]*)+", v) is not None        # camelCase: outputTokenCount
            or re.fullmatch(r"(?:[A-Z][a-z0-9]+){2,}", v) is not None           # PascalCase: AccessKeyId
            or re.fullmatch(r"[A-Za-z]+(?:[_.-][A-Za-z]+)+", v) is not None     # com separador: auth_token_key
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

    # Mantém a quantidade de linhas: números de linha do índice, do read_file e do grep batem com o arquivo.
    text = sub(_PEM, lambda m: f"<{m.group(1).lower()} omitido>" + "\n" * m.group(0).count("\n"), text)
    text = sub(_BASE64_BLOB, "<base64 omitido>", text)
    for pat in _TOKENS:
        text = sub(pat, MASK, text)
    text = sub(_URL_CREDS, rf"\1{MASK}\3", text)
    text = sub(_NPM_TOKEN, MASK, text)

    def npmrc(m: re.Match) -> str:
        nonlocal count
        # variável de ambiente ou nome sem dígitos (password, None, true): não é segredo
        if m.group(2).startswith("$") or re.fullmatch(r"[A-Za-z_]+", m.group(2)):
            return m.group(0)
        count += 1
        return m.group(1) + MASK

    text = _NPMRC_AUTH.sub(npmrc, text)
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
    elif language == "markdown":  # só dentro dos blocos de código: na prosa, "token: obrigatório" não é segredo
        text = _MD_FENCE.sub(lambda m: _CONFIG_ASSIGN.sub(assign, m.group(0)), text)
    return text, count


_BINARY_SAMPLE = 8192
_BINARY_MAX_RATIO = 0.10
_TEXT_CONTROLS = {"\t", "\n", "\r", "\f", "\v", "\x1b"}


def looks_binary(text: str) -> bool:
    """Heurística em cima do texto já decodificado (o CocoIndex decodifica com errors="replace").

    Binário: byte nulo, ou mais de 10% de caracteres inválidos (U+FFFD) / de controle na amostra.
    Texto em Latin-1 decodificado como UTF-8 só troca os acentos, fica bem abaixo disso.
    """
    sample = text[:_BINARY_SAMPLE]
    if not sample:
        return False
    if "\x00" in sample:
        return True
    bad = sum(1 for c in sample if c == "\ufffd" or (c < " " and c not in _TEXT_CONTROLS))
    return bad / len(sample) > _BINARY_MAX_RATIO


_MINIFIED_MIN_BYTES = 2000
_MINIFIED_AVG_LINE = 400
_MINIFIED_LONGEST = 2000


def looks_minified(text: str) -> bool:
    """Gerado/minificado: linhas enormes (JSON numa linha só, bundle .js sem quebras).

    Esses arquivos viravam dezenas de trechos com a mesma linha gigante (os trechos são
    expandidos para linhas inteiras) e só fazem ruído na busca."""
    if len(text) < _MINIFIED_MIN_BYTES:
        return False
    lines = text.split("\n")
    longest = max(len(line) for line in lines)
    return longest >= _MINIFIED_LONGEST and len(text) / len(lines) >= _MINIFIED_AVG_LINE


def file_language(filename: str) -> str:
    """Linguagem pelo nome do arquivo ("" se desconhecida). Mesma regra na indexação, na leitura e no grep."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return detect_code_language(filename=filename) or _EXTRA_LANGS.get(ext, "")


def skip_reason(path: str, language: str, text: str) -> str | None:
    if looks_binary(text):
        return "binário"
    if looks_minified(text):
        return "minificado"
    if language in _DATA_LANGS:
        if len(text.encode()) > MAX_DATA_FILE_BYTES:
            return "arquivo de dados grande"
        if re.search(r"(?m)^\s*kind:\s*CustomResourceDefinition\b", text) or '"kind": "CustomResourceDefinition"' in text:
            return "CRD"
    return None
