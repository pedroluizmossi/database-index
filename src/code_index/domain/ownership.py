"""Dono do código no DDD: a que domínio e serviço pertence cada repo (ou pasta de monorepo). Puro, sem I/O.

- **Catálogo**: os domínios e serviços declarados nos `.sdl` (`domain x ("Rótulo")`, `service y ("Rótulo")`).
  Não há lista oficial: o SDL é a referência. Pessoas podem criar outros na atribuição manual.
- **Inferência** (sem LLM), cada uma uma origem de relação `belongs_to` (domain/relations.py):
  `declared` (o próprio .sdl), `sibling` (nome-base igual ao de um repo com SDL), `api_calls` (o código chama
  majoritariamente as rotas de um serviço) e `usage` (lib usada só por repos de um domínio).
- **Resolução**: por entidade, vale a origem mais forte presente (`manual` vence tudo); as mais fracas ficam
  como evidência, mas não somam donos.
- **Escopo**: o que um filtro "domínio/serviço" significa em (repo, caminho) para a busca. Uma pasta com
  dono próprio (monorepo) vale sobre o dono do repo inteiro.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .relations import DIR, DOM, REPO, SVC, dir_key, dom_key, repo_key, split_dir_key, svc_key

# Domínio reservado para código compartilhado entre domínios (libs, infraestrutura comum).
TRANSVERSAL = "transversal"
TRANSVERSAL_LABEL = "Transversal (compartilhado)"

# Da mais forte para a mais fraca. A ordem global das origens fica em domain/relations.ORIGINS.
OWNER_ORIGINS = ("manual", "declared", "sibling", "api_calls", "usage")
DERIVED_OWNER_ORIGINS = frozenset(OWNER_ORIGINS[1:])

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def norm(name: str) -> str:
    """Nome de domínio/serviço comparável: minúsculas, '-' e espaço viram '_'."""
    return re.sub(r"[\s-]+", "_", name.strip().lower())


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


# ------------------------------------------------------------------ catálogo (SDL)


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

    def service_names(self) -> dict[str, list[Owner]]:
        """Nome do serviço → donos com esse nome (o mesmo nome pode existir em dois domínios)."""
        out: dict[str, list[Owner]] = {}
        for domain, service in self.services:
            out.setdefault(service, []).append(Owner(domain, service))
        return out


def build_catalog(sdl_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
                  labels_by_repo: Mapping[str, Mapping[str, Any]] | None = None) -> Catalog:
    """Domínios e serviços dos `.sdl` (fatos `sdl` do cartão) + rótulos (`sdl_labels` da coleta)."""
    catalog = Catalog()
    for repo, services in sorted(sdl_by_repo.items()):
        labels = (labels_by_repo or {}).get(repo, {})
        for svc in services:
            domain, service = norm(str(svc.get("domain", ""))), norm(str(svc.get("service", "")))
            if not (_NAME.match(domain) and _NAME.match(service)):
                continue
            catalog.add(Owner(domain, service), repo=repo,
                        label=str(labels.get("services", {}).get(service, "")),
                        domain_label=str(labels.get("domains", {}).get(domain, "")))
    return catalog


# ------------------------------------------------------------------ inferência


@dataclass(frozen=True)
class Assignment:
    """Uma afirmação "esta entidade pertence a este dono", com a origem e a evidência."""

    entity: str                 # 'repo:x' ou 'dir:x/caminho'
    owner: Owner
    origin: str
    confidence: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


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
            if _NAME.match(domain) and _NAME.match(service):
                found.append((Owner(domain, service), str(svc.get("path", ""))))
        owners = sorted({o for o, _ in found})
        for owner in owners:
            paths = sorted({p for o, p in found if o == owner})
            out.append(Assignment(repo_key(repo), owner, "declared", "alta", {"sdl": paths[:5]}))
        dirs = {_parent(p) for _, p in found}
        if len(owners) > 1 and len(dirs) > 1 and "" not in dirs:
            for owner, path in found:
                out.append(Assignment(dir_key(repo, _parent(path)), owner, "declared", "alta", {"sdl": [path]}))
    return list({(a.entity, a.owner): a for a in out}.values())


# Sufixos que não mudam o "assunto" do repo: x-frontend, x-backend e x-service falam do mesmo serviço.
_ROLE_SUFFIXES = frozenset({
    "frontend", "front", "backend", "back", "web", "ui", "app", "service", "services", "api", "server", "client",
    "lib", "library", "sdk", "core", "impl", "bff", "angular", "react", "vue", "java", "node", "go", "py",
})


def base_name(repo: str) -> str:
    """'org__snr-audit-authorization-frontend' → 'audit_authorization' (sem prefixo curto nem papel)."""
    short = repo.split("__", 1)[-1].lower()
    parts = [p for p in re.split(r"[-_.]+", short) if p]
    if len(parts) > 1 and len(parts[0]) <= 3:  # prefixo curto: snr-, ms-, lib-
        parts = parts[1:]
    while len(parts) > 1 and parts[-1] in _ROLE_SUFFIXES:
        parts = parts[:-1]
    return "_".join(parts)


def siblings(repos: Iterable[str], declared_by_repo: Mapping[str, Sequence[Owner]], catalog: Catalog,
             skip: Iterable[str] = ()) -> list[Assignment]:
    """Repo sem SDL com o mesmo nome-base de um repo com SDL herda os serviços dele. Sem repo irmão, um
    nome-base igual a um serviço do catálogo (só em um domínio) também serve."""
    skipped = set(skip)
    by_base: dict[str, dict[Owner, list[str]]] = {}
    for repo, owners in declared_by_repo.items():
        for owner in owners:
            by_base.setdefault(base_name(repo), {}).setdefault(owner, []).append(repo)
    service_names = catalog.service_names()
    out: list[Assignment] = []
    for repo in sorted(set(repos)):
        if repo in skipped or repo in declared_by_repo:
            continue
        base = base_name(repo)
        matched = by_base.get(base)
        if matched and len(matched) <= 3:  # nome-base genérico casando com muitos serviços não serve
            for owner, from_repos in sorted(matched.items()):
                out.append(Assignment(repo_key(repo), owner, "sibling", "alta", {"repos": sorted(from_repos)[:5]}))
            continue
        named = service_names.get(base, [])
        if len(named) == 1:
            out.append(Assignment(repo_key(repo), named[0], "sibling", "media", {"service_name": base}))
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
        out.append(Assignment(repo_key(repo), Owner(domain, service), "api_calls", confidence,
                              {"calls": n, "share": round(share, 2), "others": [f"{k} ({v})" for k, v in others]}))
    return out


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
        out.append(Assignment(repo_key(lib), owner, "usage", "alta" if len(users) >= 2 else "media",
                              {"consumers": sorted(users)[:5], "n": len(users)}))
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


def _origin_rank(origin: str) -> int:
    return OWNER_ORIGINS.index(origin) if origin in OWNER_ORIGINS else len(OWNER_ORIGINS)


def resolve(assignments: Iterable[Assignment]) -> dict[str, Resolved]:
    by_entity: dict[str, list[Assignment]] = {}
    for a in assignments:
        by_entity.setdefault(a.entity, []).append(a)
    out: dict[str, Resolved] = {}
    for entity, items in by_entity.items():
        items.sort(key=lambda a: (_origin_rank(a.origin), a.owner))
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


def infer(repos: Iterable[str], sdl_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
          own_by_repo: Mapping[str, Mapping[str, Any]], manual: Sequence[Assignment] = ()) -> list[Assignment]:
    """Todas as relações derivadas (fase 1, sem LLM), na ordem em que cada passo usa os anteriores.

    `own_by_repo`: seção `ownership` do material de cada repo (rótulos do SDL, artefatos, dependências e
    rotas chamadas). `manual`: atribuições gravadas. A inferência roda também para quem tem dono manual (a
    resolução dá precedência ao manual e mostra o resto como alternativa na revisão); o dono manual conta
    como dono dos consumidores de uma lib."""
    names = set(repos)
    catalog = build_catalog(sdl_by_repo, {r: o.get("sdl_labels") or {} for r, o in own_by_repo.items()})
    decl = declared(sdl_by_repo)
    decl_owners = repo_owners(resolve(decl))
    sib = siblings(names, decl_owners, catalog)
    refs = {r: o.get("api_refs") or {} for r, o in own_by_repo.items() if r in names}
    api = api_call_owners(refs, catalog, skip=set(decl_owners))
    owners_now = repo_owners(resolve([*manual, *decl, *sib, *api]))
    inferred = repo_owners(resolve([*decl, *sib, *api]))
    use = usage_owners(
        {r: o.get("dependencies") or [] for r, o in own_by_repo.items() if r in names},
        {r: o.get("artifacts") or [] for r, o in own_by_repo.items() if r in names},
        owners_now,
        skip=set(inferred),
    )
    return [*decl, *sib, *api, *use]


# ------------------------------------------------------------------ escopo da busca


@dataclass
class Scope:
    """Onde está o código de um domínio/serviço: repos inteiros, pastas incluídas e pastas excluídas.

    `exclude[repo]`: pastas de um repo do escopo que pertencem a outro dono (monorepo).
    `include[repo]`: pastas de repos fora do escopo que pertencem a este dono."""

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
        def under(folders: Sequence[str]) -> bool:
            return any(path == f or path.startswith(f + "/") for f in folders)

        if repo in self.whole:
            return not under(self.exclude.get(repo, []))
        return under(self.include.get(repo, []))


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
            folders.setdefault(repo, []).append((path, hit))
    for repo, hit in repo_level.items():
        if hit:
            scope.whole.add(repo)
    for repo, items in folders.items():
        for path, hit in sorted(items):
            if repo in scope.whole and not hit:
                scope.exclude.setdefault(repo, []).append(path)
            elif repo not in scope.whole and hit:
                scope.include.setdefault(repo, []).append(path)
    return scope


# ------------------------------------------------------------------ cobertura


def coverage(repos: Iterable[str], resolved: Mapping[str, Resolved]) -> dict[str, Any]:
    """Quantos repos têm dono e por qual origem (para a interface acompanhar o mapeamento)."""
    names = sorted(set(repos))
    by_origin: Counter[str] = Counter()
    for name in names:
        r = resolved.get(repo_key(name))
        by_origin[r.origin if r else "none"] += 1
    owned = len(names) - by_origin["none"]
    return {"repos": len(names), "owned": owned, "percent": round(100 * owned / len(names)) if names else 0,
            "by_origin": {o: by_origin[o] for o in (*OWNER_ORIGINS, "none") if by_origin[o]}}
