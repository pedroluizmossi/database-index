"""`repo estimate`: travas contra gastar a cota do GitHub (dry-run, limite, cache por push, reserva)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from code_index.domain.models import DiscoveredRepo
from code_index.infra.github import RepoTree
from code_index.settings import Settings

pytestmark = pytest.mark.integration


class FakeGitHub:
    def __init__(self, remaining: int = 5000) -> None:
        self.remaining = remaining
        self.calls: list[str] = []

    def rate_remaining(self) -> int:
        return self.remaining

    def repo_tree(self, slug: str, ref: str) -> tuple[RepoTree, int]:
        self.calls.append(slug)
        self.remaining -= 1
        return RepoTree([("src/a.py", 10), ("src/b.py", 10), ("tests/test_a.py", 10)], slug.endswith("big")), self.remaining


def _found(name: str) -> DiscoveredRepo:
    return DiscoveredRepo(name=f"acme__{name}", url=f"https://github.com/acme/{name}.git", default_branch="main",
                          pushed_at=datetime(2026, 1, 1, tzinfo=UTC), size_kb=10, language="python")


async def test_travas_da_estimativa(int_settings: Settings, monkeypatch: Any) -> None:
    from code_index.services import open_service

    async with open_service(int_settings) as svc:
        await svc._repos.reconcile_org("acme", [_found("api"), _found("big")])
        gh = FakeGitHub()
        monkeypatch.setattr(svc._indexer, "_github", gh)

        dry = await svc.estimate_files(dry_run=True)
        assert dry["calls"] == 2 and gh.calls == []                       # dry-run não gasta cota

        first = await svc.estimate_files(limit=1)
        assert first["calls"] == 1 and first["left"] == 1 and gh.calls == ["acme/api"]
        assert first["estimated"] == [{"name": "acme__api", "files": 2, "truncated": False}]

        second = await svc.estimate_files()
        assert second["cached"] == 1 and gh.calls == ["acme/api", "acme/big"]  # mesmo push: não conta de novo
        assert (await svc.estimate_files())["calls"] == 0 and len(gh.calls) == 2

        rows = {r.name: r for r in await svc.list_repos()}
        assert rows["acme__big"].files_estimate == 2 and rows["acme__big"].files_estimate_truncated

        gh.remaining = int_settings.github_rate_reserve                  # cota na reserva: nada sai
        res = await svc.estimate_files(force=True)
        assert res["calls"] == 0 and res["stopped"] and len(gh.calls) == 2

        with pytest.raises(ValueError):
            await svc.estimate_files(limit=int_settings.github_estimate_max + 1)
