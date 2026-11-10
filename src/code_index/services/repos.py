"""Repositórios, orgs e catálogo de APIs (OpenAPI no S3)."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal


from ..domain.models import Org, Repo, RepoStatus
from ..domain.policies import name_from_path, name_from_url, normalize_source
from ..retrieval.redaction import mask_output

from .base import _to_status
from .ownership import OwnershipMixin

log = logging.getLogger(__name__)


class ReposMixin(OwnershipMixin):
    async def add_repo(
        self,
        source: str,
        *,
        name: str | None = None,
        branch: str | None = None,
        include: Sequence[str] | None = None,
        extra_exclude: Sequence[str] | None = None,
    ) -> Repo:
        """Adiciona um repo avulso (URL git, 'org/repo' do GitHub ou diretório local) à configuração."""
        url, path = normalize_source(source)
        if url is not None:
            default_name = name_from_url(url)
        elif path is not None:
            default_name = name_from_path(path)
        else:
            raise ValueError(f"repositório desconhecido: {source}")
        name = name or default_name
        if any(r.name == name for r in await self._config.repos()):
            raise ValueError(f"já existe um repo chamado {name!r}")
        if any(d.repo_name == name for d in await self._config.docs()):
            raise ValueError(f"nome reservado por uma fonte de documentação: {name!r}")
        kwargs: dict[str, Any] = {}
        if include:
            kwargs["include"] = tuple(include)
        if extra_exclude:
            kwargs["extra_exclude"] = tuple(extra_exclude)
        repo = Repo(name=name, url=url, path=path, branch=branch, **kwargs)
        if not await self._config.add(repo):
            raise ValueError(f"já existe um repo chamado {name!r}")
        return repo

    async def remove_repo(self, name: str, *, keep_clone: bool = False) -> Literal["removed", "ignored"]:
        """Remove um repo avulso; um repo de org é apenas marcado como ignorado."""
        manual = next((r for r in await self._config.repos() if r.name == name), None)
        if manual is not None:
            with self._indexer.exclusive():  # o sync em andamento pode estar lendo este clone
                await self._config.delete("repo", name)
                await self._repos.delete([name])
                if not keep_clone:
                    await asyncio.to_thread(self._git.remove_clone, manual)
            await self.rebuild_relations()
            return "removed"
        if await self._repos.set_ignored(name, True):
            await self.rebuild_relations()
            return "ignored"
        raise ValueError(f"repo não encontrado: {name}")

    async def unignore_repo(self, name: str) -> None:
        """Devolve um repo de org ignorado à fila de indexação."""
        if not await self._repos.set_ignored(name, False):
            raise ValueError(f"repo não encontrado: {name}")
        await self.rebuild_relations()

    async def list_repos(
        self,
        *,
        status: str | None = None,
        org: str | None = None,
        name: str | None = None,
    ) -> list[RepoStatus]:
        """Estado de cada repo (tabela de estado) com contagens de arquivos e chunks do índice."""
        rows = await self._repos.all_rows()
        counts = await self._chunks.counts_by_repo()
        skipped = await self._chunks.skips.counts_by_repo()
        out: list[RepoStatus] = []
        for r in sorted(rows.values(), key=lambda r: r.name):
            if (status and r.status != status) or (org and r.org != org) or (name and name not in r.name):
                continue
            n_files, n_chunks = counts.get(r.name, (0, 0))
            out.append(_to_status(r, n_files, n_chunks, skipped.get(r.name, 0)))
        cards = await self._enrich.cards([r.name for r in out])
        owners = await self.owners_by_repo()
        for st in out:
            own = owners.get(st.name)
            if own is not None:
                st.owners = [o.label for o in own.owners]
                st.owner_origin, st.owner_confidence = own.origin, own.confidence
            card = cards.get(st.name)
            if card:
                st.summary = mask_output(card.get("summary") or "", "markdown") or None
                st.tags = list(card.get("domain_tags") or [])
                st.card_stale = bool(card.get("stale"))
        return out

    async def find_repos(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Repos cujo cartão casa com a pergunta ("quem cuida da blocklist de e-mail?"). Sem LLM e sem Voyage."""
        if not query.strip() or len(query) > 500:
            raise ValueError("informe o que procurar (até 500 caracteres)")
        hits = await self._enrich.find(query, limit=limit)
        for h in hits:
            if h["summary"]:
                h["summary"] = mask_output(h["summary"], "markdown")
        return hits

    # ------------------------------------------------------------ orgs

    async def add_org(
        self,
        name: str,
        *,
        match: Sequence[str] = (),
        skip: Sequence[str] = (),
        languages: Sequence[str] = (),
        include_archived: bool = False,
        include_forks: bool = False,
        max_size_mb: int | None = None,
        extra_exclude: Sequence[str] = (),
    ) -> dict:
        """Adiciona uma org e descobre seus repos (não indexa). Valida o acesso antes de salvar."""
        if any(o.name == name for o in await self._config.orgs()):
            raise ValueError(f"org já configurada: {name} (use `org set` para mudar filtros)")
        org = Org(
            name=name,
            match=tuple(match),
            skip=tuple(skip),
            languages=tuple(languages),
            include_archived=include_archived,
            include_forks=include_forks,
            max_size_mb=max_size_mb,
            extra_exclude=tuple(extra_exclude),
        )
        stats = (await self._indexer.discover([org]))[org.name]  # valida acesso antes de salvar
        if not await self._config.add(org):
            raise ValueError(f"org já configurada: {name} (use `org set` para mudar filtros)")
        return stats

    async def set_org(
        self,
        name: str,
        *,
        languages: Sequence[str] | None = None,
        match: Sequence[str] | None = None,
        skip: Sequence[str] | None = None,
        max_size_mb: int | None = None,
    ) -> dict:
        """Altera os filtros de uma org e redescobre seus repos.

        Lista vazia limpa o filtro; `max_size_mb=0` remove o limite.
        """
        org = next((o for o in await self._config.orgs() if o.name == name), None)
        if org is None:
            raise ValueError(f"org não configurada: {name}")
        changes: dict[str, Any] = {}
        for key, values in (("languages", languages), ("match", match), ("skip", skip)):
            if values is not None:
                changes[key] = tuple(x for x in values if x)
        if max_size_mb is not None:
            changes["max_size_mb"] = max_size_mb or None
        if not changes:
            raise ValueError("nada a alterar (use --languages, --match, --skip ou --max-size-mb)")
        new = replace(org, **changes)
        await self._config.put(new)
        return (await self._indexer.discover([new]))[new.name]

    # ------------------------------------------------------------ fontes de documentação

    async def remove_org(self, name: str, *, keep_clones: bool = False) -> int:
        """Remove uma org: apaga seus repos do estado (e, por padrão, os clones). Retorna quantos repos saíram."""
        orgs = await self._config.orgs()
        if not any(o.name == name for o in orgs):
            raise ValueError(f"org não configurada: {name}")
        remaining = [o for o in orgs if o.name != name]
        with self._indexer.exclusive():  # o sync em andamento pode estar lendo estes clones
            await self._config.delete("org", name)
            removed = await self._repos.prune_orgs([o.name for o in remaining])
            if not keep_clones:
                for repo_name in removed:
                    await asyncio.to_thread(self._git.remove_clone, Repo(name=repo_name, url="-"))
        if removed:
            await self.rebuild_relations()
        return len(removed)

    async def refresh_orgs(self, name: str | None = None) -> dict[str, dict]:
        """Redescobre os repos das orgs (todas, ou só a informada)."""
        orgs = [o for o in await self._config.orgs() if not name or o.name == name]
        if not orgs:
            raise ValueError("nenhuma org configurada" + (f" com nome {name}" if name else ""))
        return await self._indexer.discover(orgs)

    async def org_summary(self) -> list[dict]:
        """Por org: total e contagem por status, desatualizados, filtros ativos e linguagens."""
        rows = await self.list_repos()
        out: list[dict] = []
        for org in await self._config.orgs():
            mine = [r for r in rows if r.org == org.name]
            by_status = Counter(r.status for r in mine)
            filters = {
                k: v
                for k, v in (
                    ("languages", list(org.languages)),
                    ("match", list(org.match)),
                    ("skip", list(org.skip)),
                    ("max_size_mb", org.max_size_mb),
                )
                if v
            }
            langs = Counter(r.language or "none" for r in mine)
            out.append({
                "name": org.name,
                "total": len(mine),
                "status": {s: by_status[s] for s in ("indexed", "pending", "error", "ignored")},
                "outdated": sum(r.outdated for r in mine),
                "filters": filters,
                "languages": dict(langs.most_common()),
            })
        return out

    # ------------------------------------------------------------ sync / consulta

