"""Quantos arquivos de um repo entrariam no índice, contados na árvore do GitHub (sem clonar e sem embedding).

Usa o mesmo casador de padrões do sync (`PatternFilePathMatcher` do CocoIndex, com a poda de diretórios) e o mesmo
limite de tamanho. Diferenças conhecidas para o sync real: arquivos binários (só descobertos ao ler) contam aqui.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from cocoindex.resources.file import PatternFilePathMatcher

from ..domain.models import Repo


def count_indexable(files: Iterable[tuple[str, int]], repo: Repo, *, max_bytes: int) -> int:
    matcher = PatternFilePathMatcher(included_patterns=list(repo.include), excluded_patterns=repo.all_excludes)
    dirs: dict[PurePosixPath, bool] = {}

    def dir_ok(d: PurePosixPath) -> bool:
        if d == d.parent:  # raiz ("."): sempre entra
            return True
        if d not in dirs:
            dirs[d] = dir_ok(d.parent) and matcher.is_dir_included(d)
        return dirs[d]

    total = 0
    for path, size in files:
        p = PurePosixPath(path)
        if size <= max_bytes and dir_ok(p.parent) and matcher.is_file_included(p):
            total += 1
    return total
