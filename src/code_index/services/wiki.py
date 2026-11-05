"""Fonte Wiki.js: cadastro (valida acesso) e indexação de páginas escolhidas."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.models import DocLinkRule, DocSource, SyncResult
from .base import ServiceBase


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
