"""Leitura dos arquivos de teste de um clone (sem CocoIndex e sem embedding): casos de cada arquivo.

Usa os filtros do repo menos os padrões de teste (node_modules, ocultos e os excludes do usuário continuam
valendo) e só lê o que `domain.test_cases.is_test_path` reconhece como teste. Links simbólicos ficam de fora,
como na indexação (um link commitado levaria a arquivos de fora do clone).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..domain.path_glob import glob_regex
from ..domain.test_cases import MAX_FILE_BYTES, TestCase, extract_test_cases, family, is_test_path, without_test_excludes
from .content import file_language


@dataclass(frozen=True)
class TestFile:
    __test__ = False

    path: str
    language: str
    cases: list[TestCase]


def _compile(globs: Sequence[str]) -> list[re.Pattern[str]]:
    out = []
    for g in globs:
        try:
            out.append(re.compile(glob_regex(g)))
        except ValueError:
            continue
    return out


def scan_tests(root: Path, include: Sequence[str], exclude: Sequence[str]) -> tuple[int, list[TestFile]]:
    """(arquivos de teste lidos, arquivos com pelo menos um caso). Bloqueante: chame via asyncio.to_thread."""
    inc = _compile(include)
    exc = _compile(without_test_excludes(exclude))
    root = root.resolve()
    read = 0
    out: list[TestFile] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if os.path.islink(os.path.join(dirpath, d)) or any(rx.match(rel) for rx in exc):
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(dirpath, name)
            if not family(rel) or not is_test_path(rel) or os.path.islink(full):
                continue
            if not any(rx.match(rel) for rx in inc) or any(rx.match(rel) for rx in exc):
                continue
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                raw = Path(full).read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            read += 1
            cases = extract_test_cases(raw.decode("utf-8", errors="replace").replace("\r\n", "\n"), rel)
            if cases:
                out.append(TestFile(rel, file_language(name), cases))
    return read, out
