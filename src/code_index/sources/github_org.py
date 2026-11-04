"""Fonte de repositórios: uma org (ou usuário) do GitHub, descoberta pela API."""

from __future__ import annotations

import asyncio

from code_index.domain.models import DiscoveredRepo, Org
from code_index.infra.github import GitHubClient
from code_index.sources.base import RepoSource


class GitHubOrgSource(RepoSource):
    def __init__(self, org: Org, client: GitHubClient) -> None:
        self._org = org
        self._client = client
        self.name = org.name

    async def discover(self) -> list[DiscoveredRepo]:
        # O cliente HTTP é síncrono: roda em thread para não travar o loop.
        return await asyncio.to_thread(self._client.list_org_repos, self._org)
