"""Dono do código no DDD: a que domínio e serviço pertence cada repo (ou pasta de monorepo). Puro, sem I/O.

O núcleo não conhece a convenção de nenhuma empresa: o catálogo e as inferências próprias vêm dos perfis de
organização ativos (`OwnershipRules`, ex.: o perfil `senior` lê os `.sdl`). O núcleo mantém:

- **Catálogo** (`Catalog`): domínios e serviços com rótulos. Vem das regras dos perfis (`build_catalog`); pessoas
  podem criar outros na atribuição manual, e o domínio `transversal` existe sempre.
- **Inferência** (sem LLM, `infer`), cada uma uma origem de relação `belongs_to` (domain/relations.py): primeiro
  as dos perfis (`OwnershipRules.assign`, na ordem dos perfis ativos), depois `usage` (lib usada só por repos de
  um domínio), que usa os donos que as anteriores e as manuais acharam.
- **Resolução**: por entidade, vale a origem mais forte presente (`manual` vence tudo, `usage` é a mais fraca;
  as dos perfis ficam no meio, na ordem que declaram: `owner_origins`); as mais fracas ficam como evidência,
  mas não somam donos.
- **Escopo**: o que um filtro "domínio/serviço" significa em (repo, caminho) para a busca. Uma pasta com
  dono próprio (monorepo) vale sobre o dono do repo inteiro.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .naming import NameRules
from .relations import (
    DIR, DOM, REPO, SVC, dir_key, dom_key, repo_key, split_dir_key, svc_key,
)

# Domínio reservado para código compartilhado entre domínios (libs, infraestrutura comum).
TRANSVERSAL = "transversal"
TRANSVERSAL_LABEL = "Transversal (compartilhado)"

# Origens do dono que são do núcleo: a manual (mais forte) e o uso de lib (mais fraca). As dos perfis entram
# entre as duas (`owner_origins`).
MANUAL, USAGE = "manual", "usage"
# Chaves da seção `ownership` do material que são do núcleo (o `usage`): as regras de um perfil não as usam.
CORE_MATERIAL_KEYS = frozenset({"artifacts", "dependencies"})

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def norm(name: str) -> str:
    """Nome de domínio/serviço comparável: minúsculas, '-' e espaço viram '_'."""
    return re.sub(r"[\s-]+", "_", name.strip().lower())


def is_name(name: str) -> bool:
    """Nome técnico válido de domínio ou serviço (já normalizado)."""
    return bool(_NAME.match(name))


# ------------------------------------------------------------------ dono (domínio[/serviço])


@dataclass(frozen=True, order=True)
class Owner:
    domain: str
    service: str | None = None

    @property
    def key(self) -> str:
        return svc_key(self.domain, self.service) if self.service else dom_key(self.domain)

    @property
    def label(self) -> str:
        return f"{self.domain}/{self.service}" if self.service else self.domain

    def matches(self, domain: str, service: str | None = None) -> bool:
        return self.domain == domain and (service is None or self.service == service)


def parse_owner(text: str) -> Owner:
    """'platform/authentication' ou 'platform' (rótulos são do catálogo, aqui só os nomes técnicos)."""
    domain, _, raw_service = norm(text).partition("/")
    service = raw_service or None
    for part in (domain, service):
        if part is not None and not _NAME.match(part):
            raise ValueError(f"nome inválido: {text!r} (use letras minúsculas, dígitos e _; ex.: platform/authentication)")
    return Owner(domain, service)


def owner_from_key(key: str) -> Owner | None:
    kind, _, body = key.partition(":")
    if kind == DOM:
        return Owner(body)
    if kind == SVC:
        domain, _, service = body.partition("/")
        return Owner(domain, service or None)
    return None


def entity_key(repo: str, path: str | None = None) -> str:
    """Repo inteiro ('repo:x') ou uma pasta dele ('dir:x/caminho')."""
    clean = (path or "").strip().strip("/")
    if clean and (".." in clean.split("/") or clean.startswith(".")):
        raise ValueError(f"caminho inválido: {path!r}")
    return dir_key(repo, clean) if clean else repo_key(repo)


def split_entity(key: str) -> tuple[str, str | None]:
    """'repo:x' → ('x', None); 'dir:x/a/b' → ('x', 'a/b')."""
    if key.startswith(f"{DIR}:"):
        return split_dir_key(key)
    return key.removeprefix(f"{REPO}:"), None


# ------------------------------------------------------------------ catálogo


@dataclass
class CatalogService:
    domain: str
    service: str
    label: str = ""
    repos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "service": self.service, "label": self.label, "repos": sorted(self.repos)}


@dataclass
class Catalog:
    domains: dict[str, str] = field(default_factory=dict)                       # nome → rótulo
    services: dict[tuple[str, str], CatalogService] = field(default_factory=dict)

    def has(self, owner: Owner) -> bool:
        if owner.service is None:
            return owner.domain in self.domains
        return (owner.domain, owner.service) in self.services

    def add(self, owner: Owner, *, repo: str | None = None, label: str = "", domain_label: str = "") -> None:
        if owner.domain not in self.domains or (domain_label and not self.domains[owner.domain]):
            self.domains[owner.domain] = domain_label or self.domains.get(owner.domain, "")
        if owner.service:
            svc = self.services.setdefault((owner.domain, owner.service), CatalogService(owner.domain, owner.service))
            if label and not svc.label:
                svc.label = label
            if repo and repo not in svc.repos:
                svc.repos.append(repo)

    def merge(self, other: Catalog) -> None:
        """Acrescenta outro catálogo (outro perfil): rótulo vazio é preenchido, repos somados."""
        for domain, label in other.domains.items():
            self.add(Owner(domain), domain_label=label)
        for svc in other.services.values():
            self.add(Owner(svc.domain, svc.service), label=svc.label)
            for repo in svc.repos:
                self.add(Owner(svc.domain, svc.service), repo=repo)

    def service_names(self) -> dict[str, list[Owner]]:
        """Nome do serviço → donos com esse nome (o mesmo nome pode existir em dois domínios)."""
        out: dict[str, list[Owner]] = {}
        for domain, service in self.services:
            out.setdefault(service, []).append(Owner(domain, service))
        return out


@dataclass(frozen=True)
class Assignment:
    """Uma afirmação "esta entidade pertence a este dono", com a origem e a evidência."""

    entity: str                 # 'repo:x' ou 'dir:x/caminho'
    owner: Owner
    origin: str
    confidence: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


def base_name(repo: str, naming: NameRules = NameRules()) -> str:
    """Nome do repo sem org, sem prefixo curto e sem sufixos de papel (regras do perfil, `NameRules`).
    Ex.: com prefixo curto de até 3 letras e o papel 'frontend', 'org__ms-audit-authorization-frontend' →
    'audit_authorization'. Sem regras (padrão): 'ms_audit_authorization_frontend'."""
    short = repo.split("__", 1)[-1].lower()
    parts = [p for p in re.split(r"[-_.]+", short) if p]
    if len(parts) > 1 and len(parts[0]) <= naming.short_prefix_max:  # prefixo curto: ms-, lib-...
        parts = parts[1:]
    while len(parts) > 1 and parts[-1] in naming.role_suffixes:  # x-frontend e x-backend: o mesmo assunto
        parts = parts[:-1]
    return "_".join(parts)


def usage_owners(deps_by_repo: Mapping[str, Sequence[str]], artifacts_by_repo: Mapping[str, Sequence[str]],
                 owners_by_repo: Mapping[str, Sequence[Owner]], skip: Iterable[str] = ()) -> list[Assignment]:
    """Lib sem dono usada só por repos de um domínio pertence a ele (ao serviço, se todos forem do mesmo).
    Usada por domínios diferentes: fica sem dono aqui (candidata a "transversal" na revisão manual)."""
    skipped = set(skip)
    provider: dict[str, str] = {}
    for repo, artifacts in artifacts_by_repo.items():
        for a in artifacts:
            provider.setdefault(a.lower(), repo)
    consumers: dict[str, set[str]] = {}
    for repo, deps in deps_by_repo.items():
        for dep in deps:
            lib = provider.get(dep.lower())
            if lib and lib != repo and owners_by_repo.get(repo):
                consumers.setdefault(lib, set()).add(repo)
    out: list[Assignment] = []
    for lib, users in sorted(consumers.items()):
        if lib in skipped or lib in owners_by_repo:
            continue
        owners = {o for u in users for o in owners_by_repo[u]}
        domains = {o.domain for o in owners}
        if len(domains) != 1:
            continue
        services = {o.service for o in owners}
        owner = Owner(domains.pop(), services.pop() if len(services) == 1 else None)
        out.append(Assignment(repo_key(lib), owner, USAGE, "alta" if len(users) >= 2 else "media",
                              {"consumers": sorted(users)[:5], "n": len(users)}))
    return out


# ------------------------------------------------------------------ regras dos perfis


FactsByRepo = Mapping[str, Mapping[str, Any]]   # repo → seções dos fatos do cartão (só as de `fact_sections`)
OwnByRepo = Mapping[str, Mapping[str, Any]]     # repo → seção `ownership` do material do cartão


@runtime_checkable
class OwnershipRules(Protocol):
    """Dono no DDD segundo as convenções de uma organização (gancho `ownership` do perfil). Puro: recebe dados.

    - `origins`: as origens `belongs_to` que `assign` produz, da mais forte para a mais fraca. Ficam entre
      `manual` e `usage` na resolução; o perfil as registra também na ordem global (`OrgProfile.origins`).
    - `fact_sections`: seções dos fatos do cartão que `catalog`/`assign` leem (ex.: `sdl`). O catálogo da
      leitura vem só de repos com alguma delas preenchida (sem seção nenhuma: de todos os repos).
    - `material_keys`: chaves que `collect` grava na seção `ownership` do material.
    - `collect(paths, files, read)`: na coleta do material (`enrich facts`): `paths` são os arquivos do clone,
      `files` os de interesse já lidos (manifestos e os dos extratores de fatos) e `read(rel)` lê outro arquivo
      do clone (None se não der). Devolve as chaves de `material_keys`.
    - `catalog(facts, own)`: domínios e serviços que a organização declara (com rótulos).
    - `assign(repos, facts, own, catalog, naming)`: as atribuições inferidas (só das suas `origins`). `catalog` é o
      catálogo de todos os perfis ativos; `naming`, as regras de nome combinadas."""

    origins: tuple[str, ...]
    fact_sections: tuple[str, ...]
    material_keys: tuple[str, ...]

    def collect(self, paths: Sequence[str], files: Mapping[str, str],
                read: Callable[[str], str | None]) -> dict[str, Any]: ...

    def catalog(self, facts: FactsByRepo, own: OwnByRepo) -> Catalog: ...

    def assign(self, repos: AbstractSet[str], facts: FactsByRepo, own: OwnByRepo, catalog: Catalog,
               naming: NameRules) -> list[Assignment]: ...


def owner_origins(rules: Sequence[OwnershipRules] = ()) -> tuple[str, ...]:
    """Origens do dono, da mais forte para a mais fraca: manual, as dos perfis (na ordem deles), usage."""
    return (MANUAL, *dict.fromkeys(o for r in rules for o in r.origins), USAGE)


def derived_owner_origins(rules: Sequence[OwnershipRules] = ()) -> frozenset[str]:
    """Origens do dono recalculadas no sync (todas menos a manual): as do linker `ownership`."""
    return frozenset(owner_origins(rules)[1:])


def build_catalog(rules: Sequence[OwnershipRules], facts: FactsByRepo, own: OwnByRepo) -> Catalog:
    """Catálogo dos perfis ativos (um perfil: o dele; vários: somados na ordem). Sem perfil: vazio."""
    catalogs = [r.catalog(facts, own) for r in rules]
    if len(catalogs) == 1:
        return catalogs[0]
    out = Catalog()
    for c in catalogs:
        out.merge(c)
    return out


# ------------------------------------------------------------------ resolução


@dataclass
class Resolved:
    """Donos efetivos de uma entidade: os da origem mais forte presente."""

    entity: str
    owners: list[Owner]
    origin: str
    confidence: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    weaker: list[Assignment] = field(default_factory=list)   # o que as origens mais fracas diziam

    def to_dict(self) -> dict[str, Any]:
        repo, path = split_entity(self.entity)
        return {
            "repo": repo, "path": path, "owners": [o.label for o in self.owners], "origin": self.origin,
            "confidence": self.confidence, "evidence": dict(self.evidence),
            "alternatives": [{"owner": a.owner.label, "origin": a.origin, "confidence": a.confidence}
                             for a in self.weaker],
        }


def _ranker(order: Sequence[str]) -> Callable[[str], int]:
    """Força de uma origem: posição em `order` (desconhecida: depois de todas). A ordem é sempre explícita (a dos
    perfis ativos, `ActiveProfiles.owner_origins`): não depende de os perfis já terem sido carregados no processo."""
    index = {o: i for i, o in enumerate(order)}
    return lambda origin: index.get(origin, len(index))


def resolve(assignments: Iterable[Assignment], order: Sequence[str]) -> dict[str, Resolved]:
    """Donos efetivos por entidade. `order`: origens do dono da mais forte para a mais fraca
    (`owner_origins` dos perfis ativos). Origem fora dela (perfil desligado) vale menos que todas."""
    rank_of = _ranker(order)
    by_entity: dict[str, list[Assignment]] = {}
    for a in assignments:
        by_entity.setdefault(a.entity, []).append(a)
    out: dict[str, Resolved] = {}
    for entity, items in by_entity.items():
        items.sort(key=lambda a: (rank_of(a.origin), a.owner))
        best = items[0].origin
        chosen = [a for a in items if a.origin == best]
        rank = {"alta": 0, "media": 1, "baixa": 2}
        confidence = min((a.confidence for a in chosen if a.confidence), key=lambda c: rank.get(c, 3), default=None)
        weaker = [a for a in items if a.origin != best and a.owner not in {c.owner for c in chosen}]
        out[entity] = Resolved(entity, sorted({a.owner for a in chosen}), best, confidence,
                               chosen[0].evidence, weaker)
    return out


def repo_owners(resolved: Mapping[str, Resolved]) -> dict[str, list[Owner]]:
    """Repo → donos (só o repo inteiro; pastas ficam de fora)."""
    return {split_entity(k)[0]: r.owners for k, r in resolved.items() if k.startswith(f"{REPO}:")}


def owners_of(resolved: Mapping[str, Resolved], repo: str, path: str) -> list[Owner]:
    """Donos de um arquivo: a pasta mais específica com dono; senão, o repo."""
    best: tuple[int, list[Owner]] | None = None
    prefix = f"{DIR}:{repo}/"
    for key, r in resolved.items():
        if not key.startswith(prefix):
            continue
        folder = key[len(prefix):]
        if (path == folder or path.startswith(folder + "/")) and (best is None or len(folder) > best[0]):
            best = (len(folder), r.owners)
    if best is not None:
        return best[1]
    whole = resolved.get(repo_key(repo))
    return whole.owners if whole else []


def infer(repos: Iterable[str], facts: FactsByRepo, own: OwnByRepo, manual: Sequence[Assignment] = (),
          rules: Sequence[OwnershipRules] = (), naming: NameRules = NameRules()) -> list[Assignment]:
    """Todas as relações derivadas (fase 1, sem LLM), na ordem em que cada passo usa os anteriores: primeiro as
    regras dos perfis (`rules`, na ordem), depois `usage`.

    `facts`: seções dos fatos de cada repo (as de `fact_sections` das regras). `own`: seção `ownership` do
    material de cada repo (artefatos e dependências, do núcleo, e as chaves das regras). `manual`: atribuições
    gravadas. A inferência roda também para quem tem dono manual (a resolução dá precedência ao manual e mostra o
    resto como alternativa na revisão); o dono manual conta como dono dos consumidores de uma lib. `naming`:
    regras de nome dos perfis."""
    names = set(repos)
    order = owner_origins(rules)
    catalog = build_catalog(rules, facts, own)
    found: list[Assignment] = []
    for r in rules:
        allowed = set(r.origins)
        for a in r.assign(names, facts, own, catalog, naming):
            if a.origin not in allowed:
                raise ValueError(f"regra de dono produziu origem fora das suas: {a.origin!r}")
            found.append(a)
    owners_now = repo_owners(resolve([*manual, *found], order))
    inferred = repo_owners(resolve(found, order))
    use = usage_owners(
        {r: o.get("dependencies") or [] for r, o in own.items() if r in names},
        {r: o.get("artifacts") or [] for r, o in own.items() if r in names},
        owners_now,
        skip=set(inferred),
    )
    return [*found, *use]


# ------------------------------------------------------------------ escopo da busca


@dataclass
class Scope:
    """Onde está o código de um domínio/serviço: repos inteiros, pastas incluídas e pastas excluídas.

    Vale o dono da pasta mais específica, como em `owners_of`. As listas guardam só as trocas de dono:
    `exclude[repo]`: pastas que saem do escopo (o dono da pasta acima era deste escopo, o dela não);
    `include[repo]`: pastas que entram (o de cima não era, o dela é), em repos fora do escopo ou dentro de
    uma pasta excluída (monorepo com pasta de outro dono que tem, dentro, uma subpasta deste)."""

    whole: set[str] = field(default_factory=set)
    include: dict[str, list[str]] = field(default_factory=dict)
    exclude: dict[str, list[str]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.whole and not self.include

    @property
    def repos(self) -> set[str]:
        return self.whole | set(self.include)

    def contains(self, repo: str, path: str) -> bool:
        rules = [(f, True) for f in self.include.get(repo, [])] + [(f, False) for f in self.exclude.get(repo, [])]
        deepest = max((r for r in rules if _under(path, r[0])), key=lambda r: len(r[0]), default=None)
        return deepest[1] if deepest is not None else repo in self.whole

    def clauses(self) -> list[tuple[str, str | None, list[str]]]:
        """(repo, pasta ou None = repo inteiro, pastas de dentro que saem). Um caminho está no escopo se casa
        com alguma cláusula: forma usada para montar o filtro SQL."""
        out: list[tuple[str, str | None, list[str]]] = []
        for repo in sorted(self.whole):
            out.append((repo, None, sorted(self.exclude.get(repo, []))))
        for repo, folders in sorted(self.include.items()):
            for folder in sorted(folders):
                out.append((repo, folder, sorted(f for f in self.exclude.get(repo, []) if _under(f, folder) and f != folder)))
        return out


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def scope_for(resolved: Mapping[str, Resolved], domain: str, service: str | None = None) -> Scope:
    domain, service = norm(domain), norm(service) if service else None
    scope = Scope()
    repo_level: dict[str, bool] = {}
    folders: dict[str, list[tuple[str, bool]]] = {}
    for key, r in resolved.items():
        hit = any(o.matches(domain, service) for o in r.owners)
        repo, path = split_entity(key)
        if path is None:
            repo_level[repo] = hit
        else:
            folders.setdefault(repo, []).append((path.rstrip("/"), hit))
    for repo, hit in repo_level.items():
        if hit:
            scope.whole.add(repo)
    for repo, items in folders.items():
        seen: list[tuple[str, bool]] = []
        for path, hit in sorted(items, key=lambda x: (x[0].count("/"), x[0])):  # pais antes dos filhos
            parent = max((s for s in seen if _under(path, s[0])), key=lambda s: len(s[0]), default=None)
            parent_hit = parent[1] if parent is not None else repo_level.get(repo, False)
            if hit and not parent_hit:
                scope.include.setdefault(repo, []).append(path)
            elif parent_hit and not hit:
                scope.exclude.setdefault(repo, []).append(path)
            seen.append((path, hit))
    return scope


# ------------------------------------------------------------------ cobertura


def coverage(repos: Iterable[str], resolved: Mapping[str, Resolved], order: Sequence[str]) -> dict[str, Any]:
    """Quantos repos têm dono e por qual origem (para a interface acompanhar o mapeamento). `by_origin` na
    ordem de força (`order`, ver `resolve`), e `none` no fim."""
    names = sorted(set(repos))
    by_origin: Counter[str] = Counter()
    for name in names:
        r = resolved.get(repo_key(name))
        by_origin[r.origin if r else "none"] += 1
    owned = len(names) - by_origin["none"]
    rank_of = _ranker(order)
    seen = sorted((o for o in by_origin if o != "none" and by_origin[o]), key=lambda o: (rank_of(o), o))
    return {"repos": len(names), "owned": owned, "percent": round(100 * owned / len(names)) if names else 0,
            "by_origin": {o: by_origin[o] for o in (*seen, "none") if by_origin[o]}}
