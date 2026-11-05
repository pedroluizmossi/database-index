"""Fonte OpenAPI no S3: catálogo público, prévia, status das seleções e registro de domínio/serviços."""

from __future__ import annotations

from typing import Any

from ..domain.models import DocSource, validate_name
from .base import ServiceBase, _iso_local


class ApisMixin(ServiceBase):
    async def openapi_catalog(self) -> list[dict[str, Any]]:
        """Lista o catálogo público sem baixar especificações nem gerar embeddings."""
        import httpx
        from ..sources.openapi_s3 import list_objects

        async with httpx.AsyncClient(timeout=60) as client:
            items = await list_objects(client, self.settings.openapi_bucket_url)
        return [{"domain": x.domain, "service": x.service, "key": x.key, "url": x.url,
                 "size": x.size, "modified": x.modified} for x in items]

    async def openapi_preview(self, domain: str, services: list[str] | None) -> dict[str, Any]:
        from ..sources.openapi_s3 import SwaggerObject, select_objects

        catalog = await self.openapi_catalog()
        items = [SwaggerObject(x["key"], x["domain"], x["service"], "", x["modified"], x["size"])
                 for x in catalog]
        selected = select_objects(items, domain, services)
        return {"domain": domain, "services": [x.service for x in selected],
                "bytes": sum(x.size for x in selected)}

    async def openapi_status(self) -> list[dict[str, Any]]:
        """Seleções persistidas e operações existentes no índice, sem consultar o S3."""
        sources = [d for d in self._config.load_docs() if d.type == "openapi_s3"]
        states = await self._repos.all_rows()
        counts = await self._documents.api_counts([d.repo_name for d in sources])
        return [{"domain": d.locale, "repo": d.repo_name, "all_services": not bool(d.include),
                 "selected_services": list(d.include),
                 "status": states[d.repo_name].status if d.repo_name in states else "pending",
                 "indexed_at": _iso_local(states[d.repo_name].indexed_at) if d.repo_name in states and states[d.repo_name].indexed_at else None,
                 "error": states[d.repo_name].error if d.repo_name in states else None,
                 "services": [{"service": x["service"], "operations": x["operations"]} for x in counts
                              if x["repo"] == d.repo_name and x["domain"] == d.locale]}
                for d in sources]

    async def register_openapi(self, domain: str, services: list[str] | None) -> str:
        """Acrescenta a seleção à fonte do domínio; vazio inclui serviços futuros."""
        await self.openapi_preview(domain, services)
        validate_name(domain)
        name = f"openapi-{domain}"
        docs = self._config.load_docs()
        existing = next((d for d in docs if d.name == name), None)
        if existing and (existing.type != "openapi_s3" or existing.locale != domain):
            raise ValueError(f"nome de fonte já em uso: {name}")
        if any(r.name == f"docs__{name}" for r in self._config.load_repos()):
            raise ValueError(f"nome de repositório já em uso: docs__{name}")
        if existing is None:
            if f"docs__{name}" in await self._repos.all_rows():
                raise ValueError(f"nome de repositório já em uso: docs__{name}")
            if (self.settings.repos_dir / f"docs__{name}").exists():
                raise ValueError(f"pasta da fonte já existe: docs__{name}")
        selected = tuple(sorted(set(services or ())))
        if existing and existing.include and selected:
            selected = tuple(sorted(set(existing.include) | set(selected)))
        elif existing and not existing.include:
            selected = ()
        source = DocSource(name=name, url=self.settings.openapi_bucket_url, type="openapi_s3",
                           locale=domain, include=selected)
        self._config.save(docs=[d for d in docs if d.name != name] + [source])
        return source.repo_name
