"""Contrato de uma fonte de repositórios. Hoje: organização do GitHub; no futuro GitLab, OpenAPI..."""

from __future__ import annotations

from typing import Protocol

from code_index.domain.models import DiscoveredRepo


class RepoSource(Protocol):
    name: str

    async def discover(self) -> list[DiscoveredRepo]:
        """Lista os repositórios desta fonte, já filtrados pelas regras da fonte."""
        ...
