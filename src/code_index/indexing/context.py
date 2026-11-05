"""Contexto de cada chunk: linhas inteiras para exibição e cabeçalho para o embedding.

O embedding de um trecho solto ("return pedido.total") é pobre. O cabeçalho diz ao modelo
onde o trecho vive: repo/caminho, linguagem, a classe/seção que o envolve e o que ele declara.
A extração é heurística (regex por linguagem), sem I/O: barata e tolerante a código quebrado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DeclKind = Literal["container", "member", "section"]
_MAX_NAMES = 10
_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "new",
    "throw", "await", "typeof", "super", "this", "constructor", "sizeof",
}


@dataclass(frozen=True)
class Decl:
    line: int          # 1-based
    kind: DeclKind     # container: classe/interface/...; member: função/método; section: título/recurso
    name: str


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.MULTILINE)


_MODS = r"(?:(?:export|default|public|private|protected|internal|abstract|final|sealed|open|data|static|partial|declare|inline|value)\s+)*"
_CONTAINER = _rx(rf"^[ \t]*(?:@\w+(?:\([^)\n]*\))?\s+)*{_MODS}(?:class|interface|enum|record|struct|trait|object|annotation\s+class)\s+([A-Za-z_$][\w$]*)")
_JAVA_LIKE_METHOD = _rx(
    r"^[ \t]*(?:(?:public|private|protected|internal|static|final|abstract|synchronized|override|virtual|async|default|native)\s+)+"
    r"(?:<[^>\n]+>\s+)?[\w<>\[\],.?\s]*?\b([a-zA-Z_]\w*)\s*\("
)
_KOTLIN_FUN = _rx(r"^[ \t]*(?:\w+\s+)*fun\s+(?:<[^>\n]+>\s+)?(?:[\w.]+\.)?([a-zA-Z_]\w*)\s*\(")
_SWIFT_SCALA = _rx(r"^[ \t]*(?:\w+\s+)*(?:func|def)\s+([a-zA-Z_]\w*)")
_JS_FUNCTION = _rx(r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)")
_JS_ARROW = _rx(r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s+)?(?:\([^)\n]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=>")
_JS_METHOD = _rx(
    r"^[ \t]+(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*"
    r"([A-Za-z_$][\w$]*)\s*\([^)\n]*\)\s*(?::\s*[^{;\n]+)?\{"
)
_PY_CLASS = _rx(r"^[ \t]*class\s+([A-Za-z_]\w*)")
_PY_DEF = _rx(r"^[ \t]*(?:async\s+)?def\s+([A-Za-z_]\w*)")
_GO_FUNC = _rx(r"^func\s+(?:\([^)\n]*\)\s*)?([A-Za-z_]\w*)")
_GO_TYPE = _rx(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)")
_RUST_TYPE = _rx(r"^[ \t]*(?:pub(?:\([^)\n]*\))?\s+)?(?:struct|enum|trait|impl(?:<[^>\n]*>)?(?:\s+[\w:<>]+\s+for)?)\s+([A-Za-z_][\w:]*)")
_RUST_FN = _rx(r"^[ \t]*(?:pub(?:\([^)\n]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)")
_RUBY_CLASS = _rx(r"^[ \t]*(?:class|module)\s+([A-Z][\w:]*)")
_RUBY_DEF = _rx(r"^[ \t]*def\s+(?:self\.)?([a-z_]\w*[?!]?)")
_PHP_FUNCTION = _rx(r"^[ \t]*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+([A-Za-z_]\w*)")
_SQL_OBJECT = re.compile(
    r"^[ \t]*create\s+(?:or\s+replace\s+)?(?:unique\s+)?(table|view|materialized\s+view|function|procedure|index|trigger|sequence|type)"
    r"\s+(?:if\s+not\s+exists\s+)?([\w.\"]+)",
    re.MULTILINE | re.IGNORECASE,
)
_HCL_BLOCK = _rx(r'^(resource|data|module|variable|output|provider)\s*(?:"([^"\n]+)")?\s*(?:"([^"\n]+)")?')
_SDL_DECL = _rx(r"^[ \t]*(query|action|signal|event|record|entity|enumeration|enum|service)\s+([A-Za-z_]\w*)")
_PROTO_CONTAINER = _rx(r"^[ \t]*(?:message|service|enum)\s+([A-Za-z_]\w*)")
_PROTO_RPC = _rx(r"^[ \t]*rpc\s+([A-Za-z_]\w*)")
_GRAPHQL = _rx(r"^[ \t]*(?:type|input|interface|enum|union|scalar|schema|extend\s+type)\s+([A-Za-z_]\w*)")
_YAML_KIND = _rx(r"^kind:\s*([\w.-]+)")
_YAML_NAME = _rx(r"^[ \t]{2}name:\s*([^\s#]+)")
_MD_HEADING = _rx(r"^(#{1,3})\s+(.+?)\s*#*$")

_JAVA_LIKE = {"java", "csharp", "c#", "scala", "groovy", "dart", "php", "c++", "cpp", "c", "objective-c"}
_JS_LIKE = {"javascript", "typescript", "tsx", "jsx", "vue", "svelte"}


def _line_of(text: str, offset: int, starts: list[int]) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:  # busca binária: maior início de linha <= offset
        mid = (lo + hi + 1) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def declarations(text: str, language: str, filename: str = "") -> list[Decl]:
    """Declarações do arquivo, em ordem de linha."""
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    out: list[Decl] = []

    def add(pattern: re.Pattern[str], kind: DeclKind, group: int = 1) -> None:
        for m in pattern.finditer(text):
            name = m.group(group)
            if name and name not in _KEYWORDS:
                # linha do NOME, não do início do casamento (que pode ser uma anotação acima: @Service)
                out.append(Decl(_line_of(text, m.start(group), starts), kind, name.strip()))

    lang = (language or "").lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else filename.lower()

    if ext in ("sdl", "pdl"):
        for m in _SDL_DECL.finditer(text):
            kind: DeclKind = "container" if m.group(1) == "service" else "member"
            out.append(Decl(_line_of(text, m.start(), starts), kind, f"{m.group(1)} {m.group(2)}"))
    elif lang == "python":
        add(_PY_CLASS, "container")
        add(_PY_DEF, "member")
    elif lang == "go":
        add(_GO_TYPE, "container")
        add(_GO_FUNC, "member")
    elif lang == "rust":
        add(_RUST_TYPE, "container")
        add(_RUST_FN, "member")
    elif lang == "ruby":
        add(_RUBY_CLASS, "container")
        add(_RUBY_DEF, "member")
    elif lang == "kotlin":
        add(_CONTAINER, "container")
        add(_KOTLIN_FUN, "member")
    elif lang in ("swift",):
        add(_CONTAINER, "container")
        add(_SWIFT_SCALA, "member")
    elif lang in _JS_LIKE:
        add(_CONTAINER, "container")
        add(_JS_FUNCTION, "member")
        add(_JS_ARROW, "member")
        add(_JS_METHOD, "member")
    elif lang in _JAVA_LIKE:
        add(_CONTAINER, "container")
        add(_JAVA_LIKE_METHOD, "member")
        if lang == "scala":
            add(_SWIFT_SCALA, "member")
        if lang == "php":
            add(_PHP_FUNCTION, "member")
    elif lang == "sql":
        for m in _SQL_OBJECT.finditer(text):
            out.append(Decl(_line_of(text, m.start(), starts), "section", f"{m.group(1).lower()} {m.group(2)}"))
    elif lang == "hcl" or ext in ("tf", "tfvars", "hcl"):
        for m in _HCL_BLOCK.finditer(text):
            label = " ".join(g for g in m.groups() if g)
            out.append(Decl(_line_of(text, m.start(), starts), "section", label))
    elif lang == "proto":
        add(_PROTO_CONTAINER, "container")
        add(_PROTO_RPC, "member")
    elif lang == "graphql":
        add(_GRAPHQL, "container")
    elif lang == "yaml":
        for m in _YAML_KIND.finditer(text):
            line = _line_of(text, m.start(), starts)
            tail = text[m.end(): m.end() + 400]
            nm = _YAML_NAME.search(tail)
            out.append(Decl(line, "section", f"{m.group(1)} {nm.group(1)}" if nm else m.group(1)))
    elif lang == "markdown":
        for m in _MD_HEADING.finditer(text):
            out.append(Decl(_line_of(text, m.start(), starts), "section", m.group(2)))

    out.sort(key=lambda d: d.line)
    return out


def chunk_bounds(start_line: int, end_line: int, end_column: int) -> tuple[int, int]:
    """Linhas inteiras do chunk. Se ele termina logo após uma quebra de linha (coluna 1),
    a linha final não tem conteúdo do chunk e não entra."""
    if end_column <= 1 and end_line > start_line:
        end_line -= 1
    return start_line, end_line


def full_lines(lines: list[str], start_line: int, end_line: int) -> str:
    """Linhas `start_line..end_line` (1-based, inclusivo), inteiras."""
    return "\n".join(lines[start_line - 1: end_line])


def symbol_names(decls: list[Decl], start_line: int, end_line: int) -> list[str]:
    """Nomes que identificam o trecho: a classe/seção que o envolve e o que ele declara."""
    before = [d for d in decls if d.line < start_line]
    names = [d.name for d in decls if start_line <= d.line <= end_line]
    enclosing = next((d.name for d in reversed(before) if d.kind in ("container", "section")), None)
    return list(dict.fromkeys(([enclosing] if enclosing else []) + names))


def chunk_header(repo: str, path: str, language: str, decls: list[Decl], start_line: int, end_line: int) -> str:
    """Cabeçalho do texto embedado. Ex.:
    # org__repo/src/HmacServiceImpl.java (java)
    # em: HmacServiceImpl · declara: sign, verify
    """
    before = [d for d in decls if d.line < start_line]
    inside = [d for d in decls if start_line <= d.line <= end_line]
    parts: list[str] = []

    enclosing = next((d.name for d in reversed(before) if d.kind == "container"), None)
    if enclosing:
        parts.append(f"em: {enclosing}")
    section = next((d.name for d in reversed(before) if d.kind == "section"), None)
    if section and not any(d.kind == "section" for d in inside):
        parts.append(f"seção: {section}")
    names = list(dict.fromkeys(d.name for d in inside))[:_MAX_NAMES]
    if names:
        parts.append("declara: " + ", ".join(names))

    first = f"# {repo}/{path}" + (f" ({language})" if language else "")
    return first if not parts else first + "\n# " + " · ".join(parts)


MIN_CHUNK_CHARS = 120     # trecho menor que isto (ex.: "  }\n}") é fundido ao vizinho
MAX_MERGED_CHARS = 2400   # ... desde que o resultado não passe disto


def chunk_spans(raw: list[tuple[int, int, int]], lines: list[str]) -> list[tuple[int, int]]:
    """Intervalos de linhas (inteiras) dos trechos, a partir de (linha inicial, linha final, coluna final)
    do splitter.

    - Trecho contido em outro já escolhido (pedaços de uma linha longa viram a mesma linha
      inteira) é descartado: antes, um JSON de uma linha virava dezenas de trechos iguais.
    - Trecho pequeno (só fechamento de bloco, por exemplo) é fundido ao anterior, ou ao próximo
      se for o primeiro, quando o resultado não fica grande demais. Arquivo pequeno inteiro
      continua sendo um trecho só.
    """

    def size(start: int, end: int) -> int:
        return len(full_lines(lines, start, end).strip())

    spans: list[tuple[int, int]] = []
    for start, end, end_col in raw:
        start, end = chunk_bounds(start, end, end_col)
        if spans and spans[-1][0] <= start and end <= spans[-1][1]:
            continue
        spans.append((start, end))

    out: list[tuple[int, int]] = []
    for start, end in spans:
        if out and size(start, end) < MIN_CHUNK_CHARS and size(out[-1][0], end) <= MAX_MERGED_CHARS:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    if len(out) > 1 and size(*out[0]) < MIN_CHUNK_CHARS and size(out[0][0], out[1][1]) <= MAX_MERGED_CHARS:
        out[0:2] = [(out[0][0], max(out[0][1], out[1][1]))]
    return out
