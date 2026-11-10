"""Dono do código no DDD: catálogo de domínios/serviços, atribuição manual e o escopo da busca.

A inferência (fase 1, sem LLM) roda no linker `ownership` a cada sync; aqui ficam a leitura (resolvida por
origem, ver domain/ownership.py) e a escrita manual, que vale na hora e vence a inferência.
"""

from __future__ import annotations

import difflib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.auth import Principal
from ..domain.models import Hit
from ..domain.ownership import (
    TRANSVERSAL, TRANSVERSAL_LABEL, Assignment, Catalog, Owner, Resolved, Scope, build_catalog, coverage,
    entity_key, norm, owner_from_key, owners_of, parse_owner, resolve, scope_for, split_entity,
)
from ..domain.relations import repo_key
from .base import ServiceBase

_CACHE_SECONDS = 60.0


@dataclass
class OwnershipState:
    resolved: dict[str, Resolved]
    catalog: Catalog
    sdl_services: set[tuple[str, str]]   # serviços declarados em algum .sdl (os outros foram criados à mão)
    built_at: float


class OwnershipMixin(ServiceBase):
    # ------------------------------------------------------------ leitura

    async def _ownership(self) -> OwnershipState:
        cached = self._owner_cache
        if isinstance(cached, OwnershipState) and time.monotonic() - cached.built_at < _CACHE_SECONDS:
            return cached
        rows = await self._relations.owner_rows()
        assignments = [Assignment(r["src"], owner, r["origin"], r["confidence"], r["evidence"])
                       for r in rows if (owner := owner_from_key(r["dst"])) is not None]
        resolved = resolve(assignments)
        sdl_rows = await self._enrich.sdl_catalog_rows()
        catalog = build_catalog({r["repo"]: r["sdl"] for r in sdl_rows}, {r["repo"]: r["labels"] for r in sdl_rows})
        sdl_services = set(catalog.services)
        catalog.add(Owner(TRANSVERSAL), domain_label=TRANSVERSAL_LABEL)
        for a in assignments:  # donos criados à mão entram no catálogo
            if not catalog.has(a.owner):
                catalog.add(a.owner)
        state = OwnershipState(resolved, catalog, sdl_services, time.monotonic())
        self._owner_cache = state
        return state

    async def ownership_catalog(self) -> dict[str, Any]:
        """Domínios e serviços (do SDL e os criados à mão), quantos repos cada um tem e a cobertura."""
        state = await self._ownership()
        code_repos = self._code_repo_names(await self._repos.all_rows())
        repo_level = {split_entity(k)[0]: r.owners for k, r in state.resolved.items() if k.startswith("repo:")}
        domains = []
        for name, label in sorted(state.catalog.domains.items()):
            in_domain = {repo for repo, owners in repo_level.items() if any(o.domain == name for o in owners)}
            services = []
            for (dom, svc), info in sorted(state.catalog.services.items()):
                if dom != name:
                    continue
                n = sum(1 for owners in repo_level.values() if Owner(dom, svc) in owners)
                services.append({"name": svc, "label": info.label, "repos": n,
                                 "declared_in": sorted(info.repos)[:10], "manual": (dom, svc) not in state.sdl_services})
            domains.append({"name": name, "label": label, "repos": len(in_domain), "services": services})
        return {"domains": domains, "coverage": coverage(code_repos, state.resolved)}

    async def ownership_list(
        self,
        *,
        domain: str | None = None,
        service: str | None = None,
        origin: str | None = None,
        unowned: bool = False,
        repo: str | None = None,
    ) -> list[dict[str, Any]]:
        """Dono de cada repo de código (e de cada pasta com dono próprio), com origem e alternativas."""
        state = await self._ownership()
        rows = await self._repos.all_rows()
        names = self._code_repo_names(rows)
        if repo is not None:
            resolved_names = await self._resolve_repos([repo]) or []
            names = [n for n in names if n in resolved_names]
        want = (norm(domain), norm(service) if service else None) if domain else None
        out: list[dict[str, Any]] = []
        for name in sorted(names):
            entries = [(repo_key(name), state.resolved.get(repo_key(name)))]
            entries += sorted((k, r) for k, r in state.resolved.items() if k.startswith(f"dir:{name}/"))
            for key, res in entries:
                item = res.to_dict() if res else {"repo": name, "path": None, "owners": [], "origin": None,
                                                   "confidence": None, "evidence": {}, "alternatives": []}
                if unowned and (item["owners"] or item["path"] is not None):
                    continue
                if origin and item["origin"] != origin:
                    continue
                if want and not (res and any(o.matches(*want) for o in res.owners)):
                    continue
                out.append(item)
        return out

    async def owner_scope(self, domain: str, service: str | None = None) -> Scope:
        """Onde está o código do domínio/serviço (repos e pastas). Nome fora do catálogo → ValueError."""
        state = await self._ownership()
        owner = parse_owner(f"{domain}/{service}" if service else domain)
        if not state.catalog.has(owner):
            raise ValueError(_unknown(owner, state.catalog))
        return scope_for(state.resolved, owner.domain, owner.service)

    async def annotate_owners(self, hits: Iterable[Hit]) -> None:
        """Preenche `owners` ('dominio/servico') de cada resultado de busca."""
        state = await self._ownership()
        for h in hits:
            h.owners = [o.label for o in owners_of(state.resolved, h.repo, h.path)]

    async def owners_by_repo(self) -> dict[str, Resolved]:
        """Repo → dono resolvido (só o repo inteiro), para listagens."""
        state = await self._ownership()
        return {split_entity(k)[0]: r for k, r in state.resolved.items() if k.startswith("repo:")}

    # ------------------------------------------------------------ escrita (manual)

    async def _owner_entities(self, repos: Sequence[str], path: str | None) -> list[str]:
        if not repos:
            raise ValueError("informe ao menos um repositório")
        names = await self._resolve_repos(list(repos)) or []
        rows = await self._repos.all_rows()
        not_code = [n for n in names if rows[n].kind != "code"]
        if not_code:
            raise ValueError(f"só repositórios de código têm dono: {', '.join(not_code)}")
        if path and path.strip("/"):
            if len(names) != 1:
                raise ValueError("pasta (monorepo) só com um repositório por vez")
            key = entity_key(names[0], path)
            root = await self._local_path(names[0])
            if root.is_dir() and not (root / split_entity(key)[1]).is_dir():  # type: ignore[operator]
                raise ValueError(f"pasta não encontrada em {names[0]}: {path}")
            return [key]
        return [entity_key(n) for n in dict.fromkeys(names)]

    async def set_owner(
        self,
        principal: Principal | None,
        repos: Sequence[str],
        owner: str,
        *,
        path: str | None = None,
        create: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """Define o dono manual de repos (ou de uma pasta de um repo). Domínio/serviço fora do catálogo só
        com `create`, para um erro de digitação não virar um domínio novo."""
        parsed = parse_owner(owner)
        state = await self._ownership()
        if not create and not state.catalog.has(parsed):
            raise ValueError(_unknown(parsed, state.catalog) + " Para criar, confirme com create.")
        keys = await self._owner_entities(repos, path)
        actor = principal.actor if principal else "operador local"
        n = await self._relations.set_manual_owner(keys, parsed.key, actor=actor, reason=reason.strip()[:500])
        await self.rebuild_relations(only=("ownership",))  # a inferência por uso de lib considera o dono manual
        return {"updated": n, "owner": parsed.label, "entities": keys}

    async def clear_owner(self, principal: Principal | None, repos: Sequence[str], *,
                          path: str | None = None) -> dict[str, Any]:
        """Remove o dono manual: a entidade volta ao que a inferência disser (ou fica sem dono).
        A auditoria fica com a interface (API/CLI), como nos demais casos de uso."""
        keys = await self._owner_entities(repos, path)
        n = await self._relations.clear_manual_owner(keys)
        await self.rebuild_relations(only=("ownership",))
        return {"cleared": n, "entities": keys}


def _unknown(owner: Owner, catalog: Catalog) -> str:
    if owner.service is None:
        options = list(catalog.domains)
        close = difflib.get_close_matches(owner.domain, options, n=3)
        what = f"domínio desconhecido: {owner.domain}"
    else:
        options = [f"{d}/{s}" for d, s in catalog.services]
        close = difflib.get_close_matches(owner.label, options, n=3, cutoff=0.5)
        what = f"serviço desconhecido: {owner.label}"
    return what + (f" (quis dizer {', '.join(close)}?)" if close else "") + "."
