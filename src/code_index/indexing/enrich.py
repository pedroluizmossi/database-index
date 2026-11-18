"""Coleta do material do cartão do repo (sem LLM): lê o clone e a tabela de símbolos.

Síncrono e bloqueante (varre o disco): chame via `asyncio.to_thread`. A montagem dos fatos e o texto da
tarefa ficam em `domain/repo_card.py`; aqui só o I/O.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..domain.doc_links import MANIFESTS, manifest_aliases, manifest_dependencies
from ..domain.ownership import OwnershipRules
from ..domain.repo_card import FactExtractor, build_facts, clean_readme, is_test_path
from .content import redact

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__", ".idea",
    ".gradle", ".mvn", "coverage", ".next", "bower_components",
})
_MAX_PATHS = 50_000
_MAX_FILE_BYTES = 256 * 1024
_MANIFEST_DEPTH = 2  # manifestos na raiz e até 2 níveis (projetos multi-módulo)
_README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md", "README.rst", "README.txt", "README")
_MAX_SYMBOLS = 80
_MAX_SUGGESTED = 8


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


def _extracted(name: str, extractors: Sequence[FactExtractor]) -> bool:
    return any(x.matches(name) for x in extractors)


def collect_material(
    root: Path, *, language: str | None, symbols: Sequence[tuple[str, str, int, str]],
    extractors: Sequence[FactExtractor] = (), ownership: Sequence[OwnershipRules] = (),
) -> dict[str, Any]:
    """Material da tarefa: fatos, README limpo (com máscara), símbolos principais e arquivos sugeridos.
    `symbols`: (path, nome, membros, kind) do repo (SymbolStore.repo_overview). Classes primeiro;
    funções de nível superior aparecem como `nome()`. `extractors`: os extratores de fatos dos perfis ativos
    (`ActiveProfiles.fact_extractors`): dizem quais arquivos ler (e até que profundidade) e preenchem as seções.
    `ownership`: as regras de dono dos perfis ativos (`ActiveProfiles.ownership_rules`), que acrescentam as suas
    chaves à seção `ownership`."""
    paths = list_paths(root)
    interesting: dict[str, str] = {}
    for rel in paths:
        name = rel.rsplit("/", 1)[-1]
        depth = rel.count("/")
        if (name in MANIFESTS and depth <= _MANIFEST_DEPTH) or any(
                x.matches(name) and depth <= x.max_depth for x in extractors):
            text = _read(root, rel)
            if text is not None:
                interesting[rel] = text
    facts = build_facts(interesting, paths, language, extractors)

    readme = ""
    for name in _README_NAMES:
        text = _read(root, name)
        if text:
            readme = clean_readme(redact(text, "markdown")[0])
            break

    # declarações de documentação e dos arquivos dos extratores (contratos, propriedades) não são classes do código
    code_symbols = [s for s in symbols if not is_test_path(s[0]) and not s[0].endswith(".md")
                    and not _extracted(s[0].rsplit("/", 1)[-1], extractors)]
    classes = [name for _, name, _, kind in code_symbols if kind == "container"]
    funcs = [f"{name}()" for _, name, _, kind in code_symbols if kind != "container"]
    names = list(dict.fromkeys(classes + funcs))[:_MAX_SYMBOLS]
    by_file: dict[str, int] = {}
    for path, _, members, _ in code_symbols:
        by_file[path] = max(by_file.get(path, 0), members)
    suggested = [p for p, _ in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_SUGGESTED]]

    return {"facts": facts.to_dict(), "readme": readme, "symbols": names, "suggested_files": suggested,
            "ownership": ownership_material(root, paths, interesting, extractors, ownership)}


def ownership_material(root: Path, paths: Sequence[str], files: Mapping[str, str],
                       extractors: Sequence[FactExtractor] = (),
                       ownership: Sequence[OwnershipRules] = ()) -> dict[str, Any]:
    """Dados para inferir o domínio/serviço do repo (domain/ownership.py). Fora do hash do cartão.
    Do núcleo: artefatos e dependências dos manifestos (o `usage`); as regras de dono dos perfis acrescentam as
    suas chaves (`OwnershipRules.collect`: ex.: rótulos do catálogo, rotas chamadas). `files`: os arquivos de
    interesse já lidos; os dos extratores de fatos não contam como manifesto."""
    artifacts: list[str] = []
    deps: list[str] = []
    for rel, text in sorted(files.items()):
        name = rel.rsplit("/", 1)[-1]
        if _extracted(name, extractors):
            continue
        if name in MANIFESTS:
            artifacts += [a for a, _ in manifest_aliases(name, text)]
            deps += manifest_dependencies(name, text)
    own = set(artifacts)
    out: dict[str, Any] = {
        "artifacts": list(dict.fromkeys(artifacts))[:50],
        "dependencies": [d for d in dict.fromkeys(deps) if d not in own][:300],
    }
    for rules in ownership:
        extra = rules.collect(paths, files, lambda rel: _read(root, rel))
        unexpected = sorted(set(extra) - set(rules.material_keys))
        if unexpected:
            raise ValueError(f"regras de dono gravaram chaves fora de material_keys: {', '.join(unexpected)}")
        out.update(extra)
    return out


def path_exists(root: Path, rel: str) -> bool:
    """O caminho existe dentro do clone (sem escapar da raiz)."""
    try:
        base = root.resolve()
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return False
    return target.is_relative_to(base) and target.exists() and ".git" not in target.relative_to(base).parts
