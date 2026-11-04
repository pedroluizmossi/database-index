"""Leitura de arquivos do clone local, com números de linha e proteção contra path traversal.

O conteúdo passa pelas mesmas regras de mascaramento do índice (`indexing.content.redact`):
segredos escondidos no índice também não vazam por aqui (ex.: via MCP).
"""

from __future__ import annotations

from pathlib import Path

from cocoindex.ops.text import detect_code_language

from ..indexing.content import redact


def _inside_root(root: Path, path: str) -> Path | None:
    """Caminho real de `path` dentro de `root`. None se não for um arquivo que esteja ali dentro."""
    try:
        real_root = root.resolve()
        target = (real_root / path).resolve()
        if target.is_relative_to(real_root) and target.is_file():
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
    symlinks apontando para fora) são recusados como "não encontrado".
    """
    target = _inside_root(local_path, path)
    if target is None:
        raise ValueError(f"arquivo não encontrado: {repo}/{path}")
    try:
        text = target.read_text(errors="replace")
    except OSError as e:
        raise ValueError(f"não foi possível ler {repo}/{path}: {e}") from None
    text, _ = redact(text, detect_code_language(filename=target.name) or "")
    lines = text.splitlines()
    first = max(1, start_line or 1)
    last = min(len(lines), end_line or len(lines))
    return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(first, last + 1))
