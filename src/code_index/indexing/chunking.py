"""Divisão dos arquivos em trechos pela estrutura de cada formato (sem I/O).

- Código: o splitter do CocoIndex (tree-sitter) corta nas fronteiras da sintaxe; aqui ficam o empacotamento por tamanho
  (`Chunking`, em tokens) e o contexto do cabeçalho: pacote/namespace e o método a que o trecho pertence.
- Markdown (wiki, README): um bloco por seção (título até o próximo título); seções curtas vizinhas se juntam até o alvo,
  seção grande vira janelas de linhas. O cabeçalho leva o caminho dos títulos (Página › Seção › Subseção).
- YAML / JSON / properties / TOML: um bloco por documento ou chave de primeiro nível, com o mesmo empacotamento.

Nada aqui corta uma linha: trechos são sempre linhas inteiras (o `read_file` mostra as mesmas linhas).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context import Chunking, Decl, line_windows

# Bytes por token, para converter o alvo em tokens no tamanho que o splitter entende (bytes). Código e texto técnico
# ficam entre 3 e 4 bytes por token nos tokenizadores atuais (Qwen, Voyage); 3,5 mira o meio.
BYTES_PER_TOKEN = 3.5


def chunking_for_tokens(target: int, min_tokens: int, max_tokens: int) -> Chunking:
    """`Chunking` (em caracteres) a partir do alvo em tokens: alvo, mínimo para juntar vizinhos, máximo antes das
    janelas de linhas e sobreposição de ~1/16 do alvo."""
    size = int(target * BYTES_PER_TOKEN)
    return Chunking(size=size, min_size=int(min_tokens * BYTES_PER_TOKEN), overlap=max(0, size // 16),
                    max_chars=int(max_tokens * BYTES_PER_TOKEN))


def _span_chars(lines: list[str], start: int, end: int) -> int:
    return sum(len(lines[i - 1]) + 1 for i in range(start, end + 1))


def pack_blocks(lines: list[str], starts: list[int], chunking: Chunking) -> list[tuple[int, int]]:
    """Blocos (cada um começa numa linha de `starts` e vai até a véspera do próximo) agrupados em ordem até o alvo.
    Bloco sozinho maior que o máximo vira janelas de linhas inteiras. Linhas antes do primeiro bloco entram nele."""
    n = len(lines)
    if n == 0:
        return []
    marks = sorted({s for s in starts if 1 <= s <= n} | {1})
    blocks = [(s, (marks[i + 1] - 1) if i + 1 < len(marks) else n) for i, s in enumerate(marks)]
    out: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = None
    for start, end in blocks:
        size = _span_chars(lines, start, end)
        if size > chunking.max_chars:
            if cur:
                out.append(cur)
                cur = None
            out.extend(line_windows(lines, start, end, chunking.size, chunking.overlap))
            continue
        if cur is None:
            cur = (start, end)
        elif _span_chars(lines, cur[0], end) <= chunking.size or _span_chars(lines, cur[0], cur[1]) < chunking.min_size:
            cur = (cur[0], end)
        else:
            out.append(cur)
            cur = (start, end)
    if cur:
        out.append(cur)
    # o último pedaço pequeno vai para o anterior, se couber no máximo
    if len(out) > 1 and _span_chars(lines, *out[-1]) < chunking.min_size \
            and _span_chars(lines, out[-2][0], out[-1][1]) <= chunking.max_chars and out[-2][1] < out[-1][0]:
        out[-2:] = [(out[-2][0], out[-1][1])]
    return out


# ------------------------------------------------------------------ markdown

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class Heading:
    line: int
    level: int
    title: str


def markdown_headings(lines: list[str]) -> list[Heading]:
    """Títulos ATX (`#` a `######`) fora de blocos de código."""
    out: list[Heading] = []
    fenced = False
    for i, line in enumerate(lines, 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            m = _HEADING.match(line)
            if m:
                out.append(Heading(i, len(m.group(1)), m.group(2).strip()))
    return out


def markdown_spans(lines: list[str], chunking: Chunking) -> list[tuple[int, int]]:
    """Um bloco por seção, agrupado até o alvo; sem títulos, janelas de linhas (ou o arquivo inteiro, se pequeno)."""
    return pack_blocks(lines, [h.line for h in markdown_headings(lines)], chunking)


def heading_path(headings: list[Heading], start: int, end: int) -> list[str]:
    """Caminho dos títulos onde o trecho começa (Seção › Subseção). Trecho antes do primeiro título: o primeiro título
    que ele contém, se houver."""
    stack: list[Heading] = []
    for h in headings:
        if h.line > start:
            break
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
    if not stack:
        first = next((h for h in headings if start <= h.line <= end), None)
        return [first.title] if first else []
    return [h.title for h in stack]


# ------------------------------------------------------------------ dados (YAML, JSON, properties, TOML)

_YAML_TOP = re.compile(r"^(---\s*$|[^\s#-][^:]*:)")
_TOML_TABLE = re.compile(r"^\s*\[")
STRUCTURED_LANGS = ("yaml", "json", "properties", "toml")


def _json_starts(lines: list[str]) -> list[int]:
    """Linhas das chaves (ou elementos) de primeiro nível de um JSON formatado: a menor indentação acima da raiz."""
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip() and l.strip() not in ("{", "}", "[", "]", "},", "],")]
    if not indents:
        return []
    first = min(i for i in indents if i > 0) if any(i > 0 for i in indents) else 0
    out = []
    for n, line in enumerate(lines, 1):
        s = line.lstrip()
        if s and len(line) - len(s) == first and (s.startswith('"') or s.startswith("{") or s.startswith("[")):
            out.append(n)
    return out


def _properties_starts(lines: list[str]) -> list[int]:
    """Uma linha nova quando muda o primeiro segmento da chave (spring.datasource.* fica junto)."""
    out, prev = [], None
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith(("#", "!")):
            continue
        prefix = re.split(r"[.=:\s]", s, 1)[0]
        if prefix != prev:
            out.append(n)
            prev = prefix
    return out


def structured_spans(lines: list[str], language: str, chunking: Chunking) -> list[tuple[int, int]]:
    if language == "yaml":
        starts = [n for n, l in enumerate(lines, 1) if _YAML_TOP.match(l)]
    elif language == "json":
        starts = _json_starts(lines)
    elif language == "toml":
        starts = [n for n, l in enumerate(lines, 1) if _TOML_TABLE.match(l)]
    else:
        starts = _properties_starts(lines)
    return pack_blocks(lines, starts, chunking)


# ------------------------------------------------------------------ contexto do código para o cabeçalho

_PACKAGE = re.compile(r"^\s*(?:package|namespace)\s+([\w.]+)", re.M)
_PACKAGE_LANGS = ("java", "kotlin", "scala", "groovy", "go", "csharp", "c#", "php")
_BRACE_LANGS = ("java", "kotlin", "scala", "groovy", "go", "csharp", "c#", "php", "javascript", "typescript", "tsx",
                "jsx", "rust", "swift", "c", "cpp", "c++", "dart")


def package_name(text: str, language: str) -> str | None:
    """Pacote/namespace declarado no arquivo (Java, Kotlin, Go, C#...). Nas outras linguagens o caminho já diz."""
    if (language or "").lower() not in _PACKAGE_LANGS:
        return None
    m = _PACKAGE.search(text[:20000])
    return m.group(1) if m else None


def _signature(lines: list[str], line: int) -> str:
    """Assinatura do método a partir da linha do nome: até a abertura do corpo (`{`, `:`, `=>`), no máximo 4 linhas."""
    parts = []
    for i in range(line - 1, min(line + 3, len(lines))):
        part = lines[i].strip()
        parts.append(part)
        if part.endswith(("{", ":", "=>")) or "{" in part:
            break
    sig = " ".join(parts)
    sig = re.split(r"\s*(\{|=>)\s*", sig, maxsplit=1)[0].rstrip(":").strip()
    return sig[:200]


def enclosing_member(lines: list[str], decls: list[Decl], start: int, language: str) -> str | None:
    """Assinatura do método/função em cujo corpo o trecho começa (o trecho é um pedaço do meio dele), ou None.
    Chaves: o corpo segue aberto se há mais `{` que `}` entre a declaração e o início do trecho. Python: o trecho está
    mais indentado que a declaração e nenhuma linha no meio voltou ao nível dela."""
    member = next((d for d in reversed(decls) if d.kind == "member" and d.line < start), None)
    if member is None:
        return None
    lang = (language or "").lower()
    between = lines[member.line - 1:start - 1]
    if lang == "python":
        def indent(s: str) -> int:
            return len(s) - len(s.lstrip())
        base = indent(lines[member.line - 1])
        body = [l for l in between[1:] + [lines[start - 1]] if l.strip()]
        if not body or any(indent(l) <= base for l in body):
            return None
        return _signature(lines, member.line)
    if lang in _BRACE_LANGS:
        depth = sum(l.count("{") - l.count("}") for l in between)
        return _signature(lines, member.line) if depth > 0 else None
    return None
