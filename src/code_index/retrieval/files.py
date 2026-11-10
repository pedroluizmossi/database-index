"""Leitura de arquivos do clone local, com números de linha e proteção contra path traversal.

O conteúdo passa pela máscara de saída (`retrieval.redaction.mask_output`): segredos escondidos
no índice também não vazam por aqui (ex.: via MCP). Arquivos e pastas ocultos (`.git/`, `.env`,
`.npmrc`...) ficam fora, como no grep e no índice.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..indexing.content import file_language
from .redaction import mask_output

MAX_LINES = 2000  # por leitura; arquivo maior pede intervalo
MAX_BYTES = 2 * 1024 * 1024  # tamanho máximo de um arquivo lido (o clone é local, mas o índice não é)


def _hidden(rel: Path) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def _inside_root(root: Path, path: str) -> Path | None:
    """Caminho real de `path` dentro de `root`. None se não for um arquivo que esteja ali dentro."""
    try:
        real_root = root.resolve()
        target = (real_root / path).resolve()
        if target.is_relative_to(real_root) and target.is_file() and not _hidden(target.relative_to(real_root)):
            return target
    except (OSError, RuntimeError, ValueError):  # ex.: byte NUL no nome, loop de symlink
        return None
    return None


def read_file(
    local_path: Path,
    repo: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Lê um arquivo (ou intervalo de linhas) do clone local, com números de linha.

    `local_path` é a raiz do repositório. Caminhos que saem da raiz (`..`, absolutos,
    symlinks apontando para fora) e arquivos ocultos são recusados como "não encontrado".
    Intervalo fora do arquivo é erro (com o total de linhas); sem intervalo, devolve no máximo
    MAX_LINES linhas e avisa no fim.
    """
    target = _inside_root(local_path, path)
    if target is None:
        raise ValueError(f"arquivo não encontrado: {repo}/{path}")
    try:
        size = target.stat().st_size
    except OSError as e:
        raise ValueError(f"não foi possível ler {repo}/{path}: {e}") from None
    if size > MAX_BYTES:
        raise ValueError(f"{repo}/{path} é grande demais para leitura ({size} bytes; máximo {MAX_BYTES})")
    try:
        text = target.read_text(errors="replace")
    except OSError as e:
        raise ValueError(f"não foi possível ler {repo}/{path}: {e}") from None
    text = mask_output(text, file_language(target.name))
    lines = text.splitlines()
    total = len(lines)
    first = max(1, start_line or 1)
    if total == 0:
        return ""
    if first > total:
        raise ValueError(f"{repo}/{path} tem {total} linhas; start_line={first} está fora do arquivo")
    if end_line is not None and end_line < first:
        raise ValueError(f"intervalo inválido: end_line ({end_line}) < start_line ({first})")
    last = min(total, end_line or total, first + MAX_LINES - 1)
    out = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(first, last + 1))
    if last < min(total, end_line or total):
        out += (f"\n… mostrando linhas {first}-{last} de {total}; "
                f"use start_line={last + 1} para continuar")
    return out


def declaration_line(local_path: Path, path: str, line: int, name: str, *, lookahead: int = 15) -> tuple[int, str] | None:
    """Linha real de uma declaração anotada: o índice pode apontar a primeira anotação
    (`@Service`) em vez da linha `class Nome`. Procura `name` nas linhas seguintes.
    None se o arquivo não puder ser lido ou a declaração não aparecer."""
    target = _inside_root(local_path, path)
    if target is None:
        return None
    try:
        with target.open(errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    for i in range(max(1, line), min(len(lines), line + lookahead) + 1):
        text = lines[i - 1].strip()
        if text and not text.startswith("@") and word.search(text):
            return i, mask_output(text, "")[:240]
    return None
