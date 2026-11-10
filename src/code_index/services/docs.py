"""Fontes de documentação: cadastro, prévia, vínculos gravados, páginas ignoradas, visão geral e X-Ray.

Vínculos são relações (`RelationStore`); o catálogo de documentos (tipo, atributos, ignorados) fica no
`DocumentStore`. Nada aqui depende do tipo da fonte: o que é específico mora no plugin (`sources/`).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from ..domain.doc_links import page_path
from ..domain.documents import API_TYPES
from ..domain.models import DocSource
from ..domain.relations import STORED, sort_origins
from ..sources.base import is_docs_folder
from ..sources.registry import doc_plugin
from .base import ServiceBase
from .wiki import WIKI_TYPE

log = logging.getLogger(__name__)


class DocsMixin(ServiceBase):
    async def list_docs(self) -> list[DocSource]:
        return await self._config.docs()

    async def doc_sources(self) -> list[dict[str, Any]]:
        """Fontes cadastradas com a seleção (include/exclude) e quantos documentos estão no índice e fora da busca."""
        counts = await self._documents.source_counts()
        out = []
        for d in await self._config.docs():
            c = counts.get(d.repo_name, {})
            out.append({"name": d.name, "repo": d.repo_name, "type": d.type, "label": doc_plugin(d.type).label,
                        "url": d.url, "locale": d.locale, "include": list(d.include), "exclude": list(d.exclude),
                        "documents": c.get("documents", 0), "ignored": c.get("ignored", 0),
                        "page_selection": d.type == WIKI_TYPE})
        return out

    async def docs_preview(self, name: str, *, limit: int = 200) -> dict:
        """Prévia sem custo: o que entraria na indexação (o plugin da fonte lista a origem; nada é baixado)."""
        source = await self._doc_source(name)
        return await doc_plugin(source.type).preview(source, self.settings, limit=limit)

    # ------------------------------------------------------------ vínculos gravados (manual/agent)

    async def _check_link(self, origin: str, reason: str, repo: str) -> tuple[str, str]:
        """Valida origem, motivo e o repo de código de um vínculo. Devolve (repo resolvido, motivo limpo)."""
        if origin not in ("manual", "agent"):
            raise ValueError("origem inválida: use manual ou agent")
        reason = reason.strip()
        if origin == "agent" and not reason:
            raise ValueError("vínculo de agent precisa de motivo (o texto que justifica a ligação)")
        (resolved,) = await self._resolve_repos([repo]) or (None,)
        if resolved is None:
            raise ValueError(f"repositório desconhecido: {repo}")
        state = (await self._repos.all_rows()).get(resolved)
        if state is None or state.kind != "code":
            raise ValueError(f"só repos de código podem receber vínculo: {resolved}")
        return resolved, reason

    def _page_key(self, source: DocSource, page: str) -> str:
        """Caminho da página sem idioma ('Plataforma/Email') → caminho do documento ('pt-br/Plataforma/Email.md')."""
        page = page.strip().strip("/")
        if not page or ".." in page.split("/"):
            raise ValueError(f"caminho de página inválido: {page!r}")
        return f"{source.locale}/{page}.md"

    async def link_doc(self, source: str, page: str, repo: str, *, reason: str = "", origin: str = "manual") -> dict:
        """Grava um vínculo página → repo (origem manual ou agent) a partir do caminho sem idioma.

        Não reindexa nada: vale na próxima consulta, e some da consulta se a página ou o repo saírem.
        """
        doc = await self._doc_source(source)
        result = await self.link_doc_key(doc.repo_name, self._page_key(doc, page), repo, reason=reason, origin=origin)
        return {**result, "page": page.strip().strip("/")}

    async def link_doc_key(self, doc_repo: str, doc_path: str, repo: str, *, reason: str = "",
                           origin: str = "manual") -> dict:
        """Grava vínculo pela chave da página (doc_repo + doc_path, como aparece em docs_overview)."""
        doc = await self._doc_key(doc_repo, doc_path)
        resolved, reason = await self._check_link(origin, reason, repo)
        await self._relations.set_stored_link(doc.repo_name, doc_path, resolved, origin=origin, reason=reason)
        return {
            "doc_repo": doc.repo_name, "doc_path": doc_path, "repo": resolved, "origin": origin,
            "indexed": await self._documents.page_indexed(doc.repo_name, doc_path),
        }

    async def unlink_doc(self, source: str, page: str, repo: str) -> bool:
        """Remove um vínculo gravado (manual, agent ou LLM aprovado). Devolve False se não existia."""
        doc = await self._doc_source(source)
        return await self.unlink_doc_key(doc.repo_name, self._page_key(doc, page), repo)

    async def unlink_doc_key(self, doc_repo: str, doc_path: str, repo: str) -> bool:
        doc = await self._doc_source(doc_repo)
        (resolved,) = await self._resolve_repos([repo]) or (repo,)
        return await self._relations.remove_stored_link(doc.repo_name, doc_path, resolved)

    async def doc_links(self, *, source: str | None = None, repo: str | None = None) -> list[dict]:
        """Vínculos gravados (manual/agent/LLM aprovado), com a página e o repo de cada um."""
        doc_repo = (await self._doc_source(source)).repo_name if source else None
        resolved: str | None = None
        if repo:
            names = await self._resolve_repos([repo]) or []
            resolved = names[0] if names else None
        return await self._relations.stored_links(doc_repo=doc_repo, repo=resolved)

    # ------------------------------------------------------------ páginas ignoradas

    async def ignore_doc(self, source: str, page: str, reason: str = "") -> dict:
        """Ignora uma página da documentação: ela sai dos resultados da busca (reversível com unignore_doc)."""
        doc = await self._doc_source(source)
        doc_path = self._page_key(doc, page)
        await self._documents.set_ignore(doc.repo_name, doc_path, reason.strip())
        return {"page": page.strip().strip("/"), "doc": doc.repo_name, "doc_path": doc_path}

    async def unignore_doc(self, source: str, page: str) -> bool:
        doc = await self._doc_source(source)
        return await self._documents.remove_ignore(doc.repo_name, self._page_key(doc, page))

    async def _doc_key(self, doc_repo: str, doc_path: str) -> DocSource:
        doc = await self._doc_source(doc_repo)
        if not doc_path.startswith(f"{doc.locale}/") or not doc_path.endswith(".md") or ".." in doc_path.split("/"):
            raise ValueError(f"caminho de página inválido: {doc_path!r}")
        return doc

    async def ignore_doc_key(self, doc_repo: str, doc_path: str, reason: str = "") -> dict:
        """Ignora pela chave da página (doc_repo + doc_path, como aparece em docs_overview)."""
        doc = await self._doc_key(doc_repo, doc_path)
        await self._documents.set_ignore(doc.repo_name, doc_path, reason.strip())
        return {"doc_repo": doc.repo_name, "doc_path": doc_path, "ignored": True}

    async def unignore_doc_key(self, doc_repo: str, doc_path: str) -> bool:
        doc = await self._doc_key(doc_repo, doc_path)
        return await self._documents.remove_ignore(doc.repo_name, doc_path)

    # ------------------------------------------------------------ leitura: visão geral, X-Ray, repo

    async def docs_overview(self, *, limit: int = 500, offset: int = 0, docs_only: bool = False) -> list[dict]:
        """Documentos com a raiz e os repos ligados (origens; motivo e se é removível para os gravados)."""
        rows = await self._documents.listing(limit=limit, offset=offset,
                                             exclude_types=sorted(API_TYPES) if docs_only else ())
        links = await self._relations.links_for_docs([(r["doc_repo"], r["path"]) for r in rows])
        reasons = await self._relations.stored_reasons()
        out = []
        for r in rows:
            page = page_path(r["path"])
            by_repo = links.get((r["doc_repo"], r["path"]), {})
            entries = []
            for repo, origins in sorted(by_repo.items()):
                stored = bool(set(origins) & STORED)
                entries.append({"repo": repo, "origins": sort_origins(origins), "removable": stored,
                                "reason": reasons.get((r["doc_repo"], r["path"], repo)) if stored else None})
            out.append({"doc_repo": r["doc_repo"], "doc_path": r["path"], "page": page,
                        "root": page.split("/", 1)[0], "title": r["title"] or None, "url": r["url"] or None,
                        "type": r["type"], "attrs": r["attrs"], "ignored": r["ignored"], "links": entries})
        return out

    async def xray(self) -> dict[str, Any]:
        """Documentos (com tipo e atributos) e vínculos página → repo, para o Raio X."""
        pages = [{"repo": d["doc_repo"], "path": d["path"], "title": d["title"], "url": d["url"],
                  "type": d["type"], "attrs": d["attrs"]} for d in await self._documents.inventory()]
        return {"available": True, "pages": pages, "links": await self._relations.all_doc_links()}

    async def linked_docs(self, repo: str, *, limit: int = 100) -> list[dict]:
        """Páginas de documentação externa ligadas a um repo, com a origem de cada vínculo."""
        resolved = await self._resolve_repos([repo])
        assert resolved
        return await self._relations.docs_for_repo(resolved[0], limit=limit)

    # ------------------------------------------------------------ remoção e arquivos descartados

    async def skipped_files(self, repo: str | None = None, *, limit: int = 200) -> list[dict]:
        """Arquivos descartados na indexação (de um repo, ou de todos), com o motivo e o tamanho."""
        resolved = None
        if repo:
            names = await self._resolve_repos([repo]) or []
            resolved = names[0] if names else None
        return await self._chunks.skips.rows_for(repo=resolved, limit=limit)

    async def remove_docs(self, name: str, *, keep_files: bool = False) -> None:
        """Remove a fonte; os trechos somem no próximo sync, e documentos/relações saem já."""
        source = next((d for d in await self._config.docs() if d.name == name or d.repo_name == name), None)
        if source is None:
            raise ValueError(f"fonte de documentação não encontrada: {name}")
        if any(r.name == source.repo_name for r in await self._config.repos()):
            raise ValueError(f"fonte de documentação conflita com repo: {source.repo_name}")
        row = (await self._repos.all_rows()).get(source.repo_name)
        if row is not None and (row.kind != "docs" or row.org is not None):
            raise ValueError(f"fonte de documentação conflita com repo existente: {source.repo_name}")
        folder = self.settings.repos_dir / source.repo_name
        if not keep_files and folder.exists() and not is_docs_folder(folder):
            raise ValueError(f"pasta de {source.repo_name} não identificada como documentação; use --keep-files")
        await self._config.delete("docs", source.name)
        await self._repos.delete([source.repo_name])
        await self._documents.drop_source(source.repo_name)
        await self.rebuild_relations()
        if not keep_files and folder.is_dir():
            await asyncio.to_thread(shutil.rmtree, folder)
