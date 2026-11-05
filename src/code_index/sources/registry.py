"""Registro das fontes de documentação. Fonte nova = um plugin aqui (ver sources/base.py)."""

from __future__ import annotations

from .base import DocSourcePlugin
from .openapi_s3 import OpenApiPlugin
from .wikijs import WikiJsPlugin

_PLUGINS: dict[str, DocSourcePlugin] = {p.type: p for p in (WikiJsPlugin(), OpenApiPlugin())}


def doc_plugin(source_type: str) -> DocSourcePlugin:
    try:
        return _PLUGINS[source_type]
    except KeyError:
        raise ValueError(f"tipo de fonte não suportado: {source_type!r} (use {', '.join(sorted(_PLUGINS))})") from None


def doc_plugins() -> list[DocSourcePlugin]:
    return list(_PLUGINS.values())
