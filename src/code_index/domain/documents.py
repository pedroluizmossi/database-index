"""Documentos de fontes externas com metadados tipados (puro, sem I/O).

Cada fonte de documentação descreve o que materializou: tipo do documento e atributos que ela conhece
(ex.: domínio/serviço/operação de uma página de API). O resto do sistema lê esses campos em vez de
deduzir tipo pelo nome do repo ou operação pelo título.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WIKI_PAGE = "wiki_page"
API_OPERATION = "api_operation"
API_OVERVIEW = "api_overview"
DOC_TYPES = (WIKI_PAGE, API_OPERATION, API_OVERVIEW)
API_TYPES = frozenset({API_OPERATION, API_OVERVIEW})


@dataclass(frozen=True)
class DocumentMeta:
    path: str                                   # relativo à pasta da fonte (como em doc_pages.path)
    type: str
    title: str = ""
    url: str = ""
    tags: tuple[str, ...] = ()
    updated_at: str = ""                        # versão na origem (ex.: updatedAt da wiki)
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in DOC_TYPES:
            raise ValueError(f"tipo de documento inválido: {self.type}")
