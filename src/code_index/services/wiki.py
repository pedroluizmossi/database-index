"""Fonte Wiki.js: cadastro (valida acesso), escolha das páginas e indexação de páginas escolhidas."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..domain.models import DocLinkRule, DocSource, SyncResult
from .base import ServiceBase

if TYPE_CHECKING:
    from ..sources.wikijs import PageMeta

WIKI_TYPE = "wikijs"


class WikiMixin(ServiceBase):
    async def add_docs(
        self,
        name: str,
        *,
        url: str,
        locale: str = "pt-br",
        include: Sequence[str] = (),
        exclude: Sequence[str] = (),
        links: Sequence[tuple[str, Sequence[str]]] = (),
    ) -> dict:
        """Cadastra uma wiki (Wiki.js), validando acesso à lista e ao conteúdo de uma página."""
        from ..sources.wikijs import WikiJsClient, WikiJsError, selected_pages

        docs = self._config.load_docs()
        if any(d.name == name for d in docs):
            raise ValueError(f"fonte de documentação já existe: {name}")
        if not url.startswith(("https://", "http://")):
            raise ValueError("url da wiki deve começar com https://")
        source = DocSource(
            name=name, url=url.rstrip("/"), locale=locale, include=tuple(include), exclude=tuple(exclude),
            links=tuple(DocLinkRule(path=p, repos=tuple(r)) for p, r in links),
        )
        if any(r.name == source.repo_name for r in self._config.load_repos()):
            raise ValueError(f"fonte de documentação conflita com repo: {source.repo_name}")
        if source.repo_name in await self._repos.all_rows():
            raise ValueError(f"fonte de documentação conflita com repo existente: {source.repo_name}")
        if (self.settings.repos_dir / source.repo_name).exists():
            raise ValueError(f"pasta já existe para {source.repo_name}; escolha outro nome")
        token = self.settings.wikijs_token.get_secret_value() if self.settings.wikijs_token else None
        client = WikiJsClient(source.url, token)
        pages = await client.list_pages(source.locale)
        if not pages:
            raise WikiJsError("nenhuma página acessível para validar read:source; publique uma página antes de cadastrar")
        await client.get_page(pages[0].id)
        selected = selected_pages(source, pages)
        self._config.save(docs=docs + [source])
        return {"name": source.repo_name, "pages": len(selected), "pages_in_locale": len(pages)}

    async def index_doc_pages(self, name: str, pages: Sequence[str]) -> SyncResult:
        """Indexa só as páginas indicadas de uma wiki já cadastrada (caminho sem idioma, ex.: 'Plataforma/Email').

        A página precisa estar na seleção da fonte. A seleção não muda, e as outras páginas já baixadas
        ficam como estão (sem reprocessar nem apagar nada).
        """
        from ..sources.wikijs import WikiJsClient, selected_pages

        source = self._doc_source(name)
        if source.type != "wikijs":
            raise ValueError("indexação por página só existe para wikis")
        wanted = {p.strip().strip("/") for p in pages if p.strip().strip("/")}
        if not wanted or any(".." in p.split("/") for p in wanted):
            raise ValueError("informe caminhos de página válidos, ex.: Plataforma/Email")
        token = self.settings.wikijs_token.get_secret_value() if self.settings.wikijs_token else None
        listed = selected_pages(source, await WikiJsClient(source.url, token).list_pages(source.locale))
        missing = sorted(wanted - {p.path for p in listed})
        if missing:
            raise ValueError(f"fora da seleção da fonte {source.name} (ou inexistente): {', '.join(missing)}")
        result = await self._indexer.sync(
            [source.repo_name], batch=None, do_discover=False, pull=True, progress=False,
            allow_register=False, doc_pages=frozenset(wanted),
        )
        await self._ensure_search_indexes()
        await self.rebuild_relations()
        return result

    # ------------------------------------------------------------ escolha das páginas (sem custo)

    def _wiki_source(self, name: str) -> DocSource:
        source = self._doc_source(name)
        if source.type != WIKI_TYPE:
            raise ValueError(f"escolha de páginas só existe para wikis; {source.name} é {source.type}")
        return source

    async def _wiki_pages(self, source: DocSource) -> list[PageMeta]:
        from ..sources.wikijs import WikiJsClient

        token = self.settings.wikijs_token.get_secret_value() if self.settings.wikijs_token else None
        return await WikiJsClient(source.url, token).list_pages(source.locale)

    async def wiki_catalog(self, name: str) -> dict[str, Any]:
        """Todas as páginas publicadas no idioma da wiki (só a lista; nada é baixado), marcando as da seleção
        atual, as que já estão no índice e as que estão fora da busca."""
        from ..sources.wikijs import selected_pages

        source = self._wiki_source(name)
        pages = sorted(await self._wiki_pages(source), key=lambda p: p.path.lower())
        chosen = {p.path for p in selected_pages(source, pages)}
        state = await self._documents.source_paths(source.repo_name)
        out = []
        for p in pages:
            doc = state.get(f"{source.locale}/{p.path}.md", {})
            out.append({"path": p.path, "title": p.title, "url": source.page_url(p.locale, p.path),
                        "updated_at": p.updated_at, "selected": p.path in chosen,
                        "indexed": bool(doc.get("present")), "ignored": bool(doc.get("ignored"))})
        return {"name": source.name, "repo": source.repo_name, "url": source.url, "locale": source.locale,
                "include": list(source.include), "exclude": list(source.exclude), "pages": out}

    async def set_wiki_selection(self, name: str, pages: Sequence[str], *, dry_run: bool = False) -> dict[str, Any]:
        """Troca a seleção da wiki pelas páginas indicadas (caminho sem idioma). As regras include/exclude são
        calculadas e conferidas contra a lista da wiki antes de gravar. Não indexa: páginas novas entram (e as que
        saíram são apagadas do índice) no próximo sync da fonte."""
        from ..sources.wikijs import selected_pages, selection_rules

        source = self._wiki_source(name)
        listed = await self._wiki_pages(source)
        known = {p.path for p in listed}
        chosen = {p.strip().strip("/") for p in pages if p.strip().strip("/")}
        missing = sorted(chosen - known)
        if missing:
            more = f" (+{len(missing) - 5})" if len(missing) > 5 else ""
            raise ValueError(f"páginas que não existem (ou não estão publicadas) na wiki: {', '.join(missing[:5])}{more}")
        include, exclude = selection_rules(known, chosen)
        updated = replace(source, include=include, exclude=exclude)
        if {p.path for p in selected_pages(updated, listed)} != chosen:
            raise RuntimeError("as regras calculadas não reproduzem a seleção; nada foi salvo")
        before = {p.path for p in selected_pages(source, listed)}
        changed = (include, exclude) != (source.include, source.exclude)
        if changed and not dry_run:
            self._config.save(docs=[updated if d.name == source.name else d for d in self._config.load_docs()])
        return {"name": source.repo_name, "include": list(include), "exclude": list(exclude),
                "previous": {"include": list(source.include), "exclude": list(source.exclude)},
                "selected": len(chosen), "added": len(chosen - before), "removed": len(before - chosen),
                "changed": changed, "saved": changed and not dry_run}
