"""Coleta do material do cartão do repo (sem LLM): lê o clone e a tabela de símbolos.

Síncrono e bloqueante (varre o disco): chame via `asyncio.to_thread`. A montagem dos fatos e o texto da
tarefa ficam em `domain/repo_card.py`; aqui só o I/O.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..domain.doc_links import MANIFESTS, manifest_aliases, manifest_dependencies
from ..domain.ownership import api_refs
from ..domain.repo_card import build_facts, clean_readme, is_test_path, parse_sdl_labels
from .content import redact

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__", ".idea",
    ".gradle", ".mvn", "coverage", ".next", "bower_components",
})
_MAX_PATHS = 50_000
_MAX_FILE_BYTES = 256 * 1024
_MANIFEST_DEPTH = 2  # manifestos na raiz e até 2 níveis (projetos multi-módulo)
_SDL_DEPTH = 4
_README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md", "README.rst", "README.txt", "README")
_MAX_SYMBOLS = 80
_MAX_SUGGESTED = 8
# Rotas chamadas (domínio/serviço): só arquivos de código/config, sem testes, até este total lido.
_REF_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".html", ".java", ".kt", ".groovy", ".py", ".go", ".cs",
    ".json", ".yaml", ".yml", ".properties", ".xml", ".env.example",
})
_MAX_REF_FILES = 5000
_MAX_REFS = 40


def list_paths(root: Path) -> list[str]:
    """Caminhos relativos dos arquivos do clone (sem .git, dependências e saídas de build)."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        rel = os.path.relpath(dirpath, root)
        prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        for f in sorted(filenames):
            out.append(prefix + f)
            if len(out) >= _MAX_PATHS:
                return out
    return out


def _read(root: Path, rel: str) -> str | None:
    p = root / rel
    try:
        if p.is_symlink() or p.stat().st_size > _MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def collect_material(
    root: Path, *, language: str | None, symbols: Sequence[tuple[str, str, int, str]],
) -> dict[str, Any]:
    """Material da tarefa: fatos, README limpo (com máscara), símbolos principais e arquivos sugeridos.
    `symbols`: (path, nome, membros, kind) do repo (SymbolStore.repo_overview). Classes primeiro;
    funções de nível superior aparecem como `nome()`."""
    paths = list_paths(root)
    interesting: dict[str, str] = {}
    for rel in paths:
        name = rel.rsplit("/", 1)[-1]
        depth = rel.count("/")
        if (name in MANIFESTS and depth <= _MANIFEST_DEPTH) or (name.endswith((".sdl", ".pdl")) and depth <= _SDL_DEPTH):
            text = _read(root, rel)
            if text is not None:
                interesting[rel] = text
    facts = build_facts(interesting, paths, language)

    readme = ""
    for name in _README_NAMES:
        text = _read(root, name)
        if text:
            readme = clean_readme(redact(text, "markdown")[0])
            break

    code_symbols = [s for s in symbols if not is_test_path(s[0]) and not s[0].endswith((".md", ".sdl", ".pdl"))]
    classes = [name for _, name, _, kind in code_symbols if kind == "container"]
    funcs = [f"{name}()" for _, name, _, kind in code_symbols if kind != "container"]
    names = list(dict.fromkeys(classes + funcs))[:_MAX_SYMBOLS]
    by_file: dict[str, int] = {}
    for path, _, members, _ in code_symbols:
        by_file[path] = max(by_file.get(path, 0), members)
    suggested = [p for p, _ in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_SUGGESTED]]

    return {"facts": facts.to_dict(), "readme": readme, "symbols": names, "suggested_files": suggested,
            "ownership": ownership_material(root, paths, interesting)}


def ownership_material(root: Path, paths: Sequence[str], files: Mapping[str, str]) -> dict[str, Any]:
    """Dados para inferir o domínio/serviço do repo (domain/ownership.py). Fora do hash do cartão."""
    labels: dict[str, dict[str, str]] = {"domains": {}, "services": {}}
    artifacts: list[str] = []
    deps: list[str] = []
    for rel, text in sorted(files.items()):
        name = rel.rsplit("/", 1)[-1]
        if name.endswith(".sdl"):
            for k, v in parse_sdl_labels(text).items():
                labels[k].update(v)
        elif name in MANIFESTS:
            artifacts += [a for a, _ in manifest_aliases(name, text)]
            deps += manifest_dependencies(name, text)
    refs: Counter[str] = Counter()
    read = 0
    for rel in paths:
        if read >= _MAX_REF_FILES:
            break
        if is_test_path(rel) or not rel.lower().endswith(tuple(_REF_EXTENSIONS)):
            continue
        content = _read(root, rel)
        if content is None:
            continue
        read += 1
        refs.update(api_refs(content))
    own = set(artifacts)
    return {
        "sdl_labels": labels,
        "artifacts": list(dict.fromkeys(artifacts))[:50],
        "dependencies": [d for d in dict.fromkeys(deps) if d not in own][:300],
        "api_refs": dict(refs.most_common(_MAX_REFS)),
    }


def path_exists(root: Path, rel: str) -> bool:
    """O caminho existe dentro do clone (sem escapar da raiz)."""
    try:
        base = root.resolve()
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return False
    return target.is_relative_to(base) and target.exists() and ".git" not in target.relative_to(base).parts
