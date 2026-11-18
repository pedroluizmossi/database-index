"""Dono no DDD pela convenção da plataforma (regras de dono do perfil `senior`, `domain/ownership.OwnershipRules`).

- **Catálogo**: os domínios e serviços declarados nos `.sdl` (`domain x ("Rótulo")`, `service y ("Rótulo")`,
  seção `sdl` dos fatos + rótulos `sdl_labels` da coleta). Não há lista oficial: o SDL é a referência.
- **Inferências** (sem LLM), da mais forte para a mais fraca: `declared` (o próprio .sdl), `sibling` (nome-base
  igual ao de um repo com SDL) e `api_calls` (o código chama majoritariamente as rotas
  `<domínio>/<serviço>/{queries|actions|signals|entities|events|apis}/` de um serviço). O `usage` (lib usada só
  por repos de um domínio) e a resolução são do núcleo.
- **Coleta** (`collect`): `sdl_labels` (rótulos dos `.sdl` já lidos) e `api_refs` (rotas citadas no código).

Puro: a coleta lê o clone pela função que o núcleo passa.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any

from code_index.profiles.api import (
    CONTRACTS_SECTION, Assignment, Catalog, FactsByRepo, NameRules, Origin, OriginPlacement, Owner, OwnByRepo,
    base_name, dir_key, is_name, is_test_path, norm, repo_key, repo_owners, resolve,
)

from .sdl import parse_sdl_labels

DECLARED, SIBLING, API_CALLS = "declared", "sibling", "api_calls"
OWNER_ORIGINS = (DECLARED, SIBLING, API_CALLS)   # da mais forte para a mais fraca

# Na ordem global (domain/relations), logo antes do `usage` do núcleo: depois de `sdl`, como sempre foi.
ORIGINS = OriginPlacement(before="usage", origins=(
    Origin(DECLARED, "SDL do repo", "domínio/serviço declarados no .sdl do próprio repo (ou da pasta)",
           authoritative=True),
    Origin(SIBLING, "repo irmão", "mesmo nome-base de um repo com SDL (ex.: x-frontend e x-backend)"),
    Origin(API_CALLS, "chamadas de API", "o código chama, na maioria, rotas domínio/serviço deste serviço"),
))

# Chaves gravadas na seção `ownership` do material (nomes históricos: o material já gravado continua valendo).
LABELS_KEY, REFS_KEY = "sdl_labels", "api_refs"


# ------------------------------------------------------------------ catálogo


def _sdl(facts: FactsByRepo) -> dict[str, Sequence[Mapping[str, Any]]]:
    """Repo → serviços dos `.sdl` (só repos que têm algum)."""
    return {repo: f[CONTRACTS_SECTION] for repo, f in facts.items() if f.get(CONTRACTS_SECTION)}


def build_catalog(sdl_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
                  labels_by_repo: Mapping[str, Mapping[str, Any]] | None = None) -> Catalog:
    """Domínios e serviços dos `.sdl` (fatos `sdl` do cartão) + rótulos (`sdl_labels` da coleta)."""
    catalog = Catalog()
    for repo, services in sorted(sdl_by_repo.items()):
        labels = (labels_by_repo or {}).get(repo, {})
        for svc in services:
            domain, service = norm(str(svc.get("domain", ""))), norm(str(svc.get("service", "")))
            if not (is_name(domain) and is_name(service)):
                continue
            catalog.add(Owner(domain, service), repo=repo,
                        label=str(labels.get("services", {}).get(service, "")),
                        domain_label=str(labels.get("domains", {}).get(domain, "")))
    return catalog


# ------------------------------------------------------------------ inferências


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def declared(sdl_by_repo: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[Assignment]:
    """O repo pertence aos serviços do seu `.sdl`. Monorepo (serviços diferentes em pastas diferentes):
    cada pasta também pertence ao seu serviço, para a busca separar os módulos."""
    out: list[Assignment] = []
    for repo, services in sorted(sdl_by_repo.items()):
        found: list[tuple[Owner, str]] = []
        for svc in services:
            domain, service = norm(str(svc.get("domain", ""))), norm(str(svc.get("service", "")))
            if is_name(domain) and is_name(service):
                found.append((Owner(domain, service), str(svc.get("path", ""))))
        owners = sorted({o for o, _ in found})
        for owner in owners:
            paths = sorted({p for o, p in found if o == owner})
            out.append(Assignment(repo_key(repo), owner, DECLARED, "alta", {"sdl": paths[:5]}))
        dirs = {_parent(p) for _, p in found}
        if len(owners) > 1 and len(dirs) > 1 and "" not in dirs:
            for owner, path in found:
                out.append(Assignment(dir_key(repo, _parent(path)), owner, DECLARED, "alta", {"sdl": [path]}))
    return list({(a.entity, a.owner): a for a in out}.values())


def siblings(repos: Iterable[str], declared_by_repo: Mapping[str, Sequence[Owner]], catalog: Catalog,
             skip: Iterable[str] = (), naming: NameRules = NameRules()) -> list[Assignment]:
    """Repo sem SDL com o mesmo nome-base de um repo com SDL herda os serviços dele. Sem repo irmão, um
    nome-base igual a um serviço do catálogo (só em um domínio) também serve."""
    skipped = set(skip)
    by_base: dict[str, dict[Owner, list[str]]] = {}
    for repo, owners in declared_by_repo.items():
        for owner in owners:
            by_base.setdefault(base_name(repo, naming), {}).setdefault(owner, []).append(repo)
    service_names = catalog.service_names()
    out: list[Assignment] = []
    for repo in sorted(set(repos)):
        if repo in skipped or repo in declared_by_repo:
            continue
        base = base_name(repo, naming)
        matched = by_base.get(base)
        if matched and len(matched) <= 3:  # nome-base genérico casando com muitos serviços não serve
            for owner, from_repos in sorted(matched.items()):
                out.append(Assignment(repo_key(repo), owner, SIBLING, "alta", {"repos": sorted(from_repos)[:5]}))
            continue
        named = service_names.get(base, [])
        if len(named) == 1:
            out.append(Assignment(repo_key(repo), named[0], SIBLING, "media", {"service_name": base}))
    return out


# Rotas da plataforma: '<domínio>/<serviço>/{queries|actions|signals|entities|events|apis}/...'
_API_REF = re.compile(
    r"(?<=[\"'`/])([a-z][a-z0-9_]*)/([a-z][a-zA-Z0-9_]*)/(?:queries|actions|signals|entities|events|apis)/"
)


def api_refs(text: str) -> Counter[str]:
    """Quantas vezes o texto cita rotas de cada 'domínio/serviço'."""
    return Counter(f"{d}/{s.lower()}" for d, s in _API_REF.findall(text))


API_MIN_CALLS = 2
API_MIN_SHARE = 0.5
UBIQUITOUS_SHARE = 0.3   # serviço chamado por 30%+ dos repos (tradução, usuário...) não define o dono
UBIQUITOUS_MIN_REPOS = 5


def api_call_owners(refs_by_repo: Mapping[str, Mapping[str, int]], catalog: Catalog,
                    skip: Iterable[str] = ()) -> list[Assignment]:
    """Repo cujo código chama, na maioria, as rotas de um serviço do catálogo pertence a ele.

    Serviços chamados por quase todo mundo (utilitários) só contam se forem os únicos chamados."""
    skipped = set(skip)
    known = {repo: {k: n for k, n in refs.items() if n > 0 and catalog.has(Owner(*k.split("/", 1)))}
             for repo, refs in refs_by_repo.items()}
    callers = Counter(k for refs in known.values() for k in refs)
    with_refs = sum(1 for refs in known.values() if refs)
    ubiquitous = {k for k, n in callers.items()
                  if n >= UBIQUITOUS_MIN_REPOS and n / max(with_refs, 1) >= UBIQUITOUS_SHARE}
    out: list[Assignment] = []
    for repo, refs in sorted(known.items()):
        if repo in skipped or not refs:
            continue
        specific = {k: n for k, n in refs.items() if k not in ubiquitous} or refs
        total = sum(specific.values())
        top, n = max(specific.items(), key=lambda kv: (kv[1], kv[0]))
        share = n / total
        if n < API_MIN_CALLS or share < API_MIN_SHARE:
            continue
        domain, service = top.split("/", 1)
        others = sorted(((k, v) for k, v in specific.items() if k != top), key=lambda kv: -kv[1])[:3]
        confidence = "alta" if share >= 0.8 and n >= 3 else "media"
        out.append(Assignment(repo_key(repo), Owner(domain, service), API_CALLS, confidence,
                              {"calls": n, "share": round(share, 2), "others": [f"{k} ({v})" for k, v in others]}))
    return out




# ------------------------------------------------------------------ coleta

# Rotas chamadas (domínio/serviço): só arquivos de código/config, sem testes, até este total lido.
_REF_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".html", ".java", ".kt", ".groovy", ".py", ".go", ".cs",
    ".json", ".yaml", ".yml", ".properties", ".xml", ".env.example",
})
_MAX_REF_FILES = 5000
_MAX_REFS = 40


def collect_labels(files: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """Rótulos de domínio/serviço dos `.sdl` já lidos pela coleta."""
    labels: dict[str, dict[str, str]] = {"domains": {}, "services": {}}
    for rel, text in sorted(files.items()):
        if rel.rsplit("/", 1)[-1].endswith(".sdl"):
            for k, v in parse_sdl_labels(text).items():
                labels.setdefault(k, {}).update(v)
    return labels


def collect_refs(paths: Sequence[str], read: Callable[[str], str | None]) -> dict[str, int]:
    """Rotas da plataforma citadas no código (as mais frequentes)."""
    refs: Counter[str] = Counter()
    done = 0
    for rel in paths:
        if done >= _MAX_REF_FILES:
            break
        if is_test_path(rel) or not rel.lower().endswith(tuple(_REF_EXTENSIONS)):
            continue
        content = read(rel)
        if content is None:
            continue
        done += 1
        refs.update(api_refs(content))
    return dict(refs.most_common(_MAX_REFS))


# ------------------------------------------------------------------ regras


class SeniorOwnership:
    """`OwnershipRules` da plataforma: catálogo do SDL e as inferências declared → sibling → api_calls."""

    origins: tuple[str, ...] = OWNER_ORIGINS
    fact_sections: tuple[str, ...] = (CONTRACTS_SECTION,)
    material_keys: tuple[str, ...] = (LABELS_KEY, REFS_KEY)

    def collect(self, paths: Sequence[str], files: Mapping[str, str],
                read: Callable[[str], str | None]) -> dict[str, Any]:
        return {LABELS_KEY: collect_labels(files), REFS_KEY: collect_refs(paths, read)}

    def catalog(self, facts: FactsByRepo, own: OwnByRepo) -> Catalog:
        return build_catalog(_sdl(facts), {r: o.get(LABELS_KEY) or {} for r, o in own.items()})

    def assign(self, repos: AbstractSet[str], facts: FactsByRepo, own: OwnByRepo, catalog: Catalog,
               naming: NameRules) -> list[Assignment]:
        decl = declared(_sdl(facts))
        decl_owners = repo_owners(resolve(decl, OWNER_ORIGINS))
        sib = siblings(repos, decl_owners, catalog, naming=naming)
        refs = {r: o.get(REFS_KEY) or {} for r, o in own.items() if r in repos}
        api = api_call_owners(refs, catalog, skip=set(decl_owners))
        return [*decl, *sib, *api]
