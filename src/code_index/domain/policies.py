"""Regras de negócio puras: nomes, origem de repos e seleção do próximo lote."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .models import Org, RepoState


def name_from_url(url: str) -> str:
    """'https://github.com/org/proj.git' -> 'org__proj'."""
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    if m:
        return f"{m.group(1)}__{m.group(2)}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", url.rstrip("/").rsplit("/", 1)[-1])


def name_from_path(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", path.rstrip("/").rsplit("/", 1)[-1])


def normalize_source(source: str) -> tuple[str | None, str | None]:
    """Aceita URL git, 'org/repo' (GitHub) ou diretório local existente. Retorna (url, path)."""
    p = Path(source).expanduser()
    if p.is_dir():
        return None, str(p.resolve())
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):
        return f"https://github.com/{source}.git", None
    if re.match(r"^(https?://|git@|ssh://|git://)", source):
        return source, None
    return None, None


def resolve_name(name: str, known: Iterable[str]) -> str | None:
    """Aceita 'org__repo', 'org/repo' ou só 'repo' (se não for ambíguo).

    Retorna o nome conhecido, ou None. Levanta ValueError se ambíguo.
    """
    known = set(known)
    if name in known:
        return name
    if "/" in name and not name.startswith(("http", "git@", "/", ".", "~")):
        cand = name.replace("/", "__", 1)
        if cand in known:
            return cand
    suffix = sorted(n for n in known if n.endswith("__" + name))
    if len(suffix) > 1:
        raise ValueError(f"nome ambíguo {name!r}: {', '.join(suffix)}")
    return suffix[0] if suffix else None


def pick_batch(
    rows: Mapping[str, RepoState],
    batch: int | None,
    orgs: Iterable[Org] = (),
    manual_changed: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[str], int]:
    """Escolhe os próximos repos a processar.

    Ordem: (1) indexados que mudaram upstream, (2) pendentes, (3) com erro (mais antigos antes).
    Em (1) e (2): pela posição da linguagem em `Org.languages`, depois push mais recente.
    `manual_changed`: repos avulsos com HEAD remoto diferente do indexado (calculado fora, via git).
    Diretórios locais (`path`) indexados entram sempre em (1): revarrer é barato.

    Retorna (selecionados, quantos ficaram na fila).
    """
    org_by_name = {o.name: o for o in orgs}
    changed, pending, failed = [], [], []
    for r in rows.values():
        if r.ignored:
            continue
        if r.error:
            failed.append(r)
        elif not r.indexed:
            pending.append(r)
        elif r.path is not None or (r.org is not None and r.changed_upstream) or r.name in manual_changed:
            changed.append(r)

    def priority(r: RepoState) -> tuple[int, float]:
        org = org_by_name.get(r.org or "")
        rank = org.language_rank(r.language) if org else 0
        return rank, -(r.pushed_at.timestamp() if r.pushed_at else 0)

    order = sorted(changed, key=priority) + sorted(pending, key=priority) + sorted(failed, key=lambda r: r.updated_at)
    names = [r.name for r in order]
    if batch is None:
        return names, 0
    return names[:batch], max(0, len(names) - batch)
