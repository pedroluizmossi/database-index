"""Contratos de fonte de dados e de linker (ver ARQUITETURA.md).

- `RepoSource`: descobre repositórios de código (ex.: organização do GitHub).
- `DocSourcePlugin`: fonte de documentação. Materializa arquivos `.md` numa pasta (que o pipeline indexa
  como um repo `kind = docs`) e descreve o que materializou (`documents`: tipo e atributos). Pode trazer
  linkers próprios.
- `Linker`: produz relações a partir do que está no banco. Linkers são recalculados no fim de cada sync
  (indexing/linkers.py); cada um é dono das suas origens.

Fonte nova = um `DocSourcePlugin` registrado em `sources/registry.py`. Nada mais precisa mudar para busca,
X-Ray, MCP e interface mostrarem os documentos e as relações.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import asyncpg

from code_index.domain.documents import DocumentMeta
from code_index.domain.models import DiscoveredRepo, DocSource
from code_index.domain.relations import Relation
from code_index.settings import Settings


# Marca a pasta de uma fonte de documentação como gerenciada pelo code-index (o sync só escreve nela, e
# `docs remove` só apaga a pasta, se a marca existir).
OWNER_MARKER = ".code-index-docs"


def is_docs_folder(dest: Path) -> bool:
    """Pasta criada pelo code-index para uma fonte de documentação (marca, ou manifesto de versões antigas)."""
    return not (dest / ".git").exists() and ((dest / OWNER_MARKER).is_file() or (dest / ".manifest.json").is_file())


class RepoSource(Protocol):
    name: str

    async def discover(self) -> list[DiscoveredRepo]:
        """Lista os repositórios desta fonte, já filtrados pelas regras da fonte."""
        ...


class Linker(Protocol):
    name: str
    origins: frozenset[str]    # origens que este linker produz (e regrava a cada execução)

    async def run(self, conn: asyncpg.Connection, settings: Settings) -> list[Relation]:
        """Todas as relações destas origens, calculadas do zero a partir do banco (sem LLM, sem Voyage)."""
        ...


class DocSourcePlugin(Protocol):
    type: str                  # valor de `type` da fonte de documentação (DocSource.type)
    label: str

    async def fetch(self, source: DocSource, dest: Path, settings: Settings, *,
                    only: frozenset[str] | None = None) -> str:
        """Baixa só o que mudou para `dest` e devolve a revisão (muda quando o conteúdo muda)."""
        ...

    def documents(self, source: DocSource, dest: Path) -> list[DocumentMeta]:
        """Descreve os documentos materializados em `dest` (síncrono; lê só arquivos locais)."""
        ...

    async def preview(self, source: DocSource, settings: Settings, *, limit: int) -> dict[str, Any]:
        """O que entraria na indexação, sem baixar conteúdo nem gerar embeddings."""
        ...

    def linkers(self) -> Sequence[Linker]:
        """Linkers próprios da fonte (podem ser nenhum)."""
        ...
