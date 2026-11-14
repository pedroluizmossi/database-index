"""Descoberta de repos de conta de usuário: privados só quando a conta é a do dono do token."""

from __future__ import annotations

import httpx
import pytest

from code_index.domain.models import Org
from code_index.infra import github
from code_index.infra.github import GitHubClient

REPO = {"name": "segredo", "clone_url": "https://github.com/eu/segredo.git", "size": 10,
        "default_branch": "main", "pushed_at": None, "language": "Python"}


def _client(monkeypatch: pytest.MonkeyPatch, login: str | None) -> list[str]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        path = request.url.path
        if path.startswith("/orgs/"):
            return httpx.Response(404)
        if path == "/user":
            return httpx.Response(200, json={"login": login}) if login else httpx.Response(401)
        return httpx.Response(200, json=[REPO])

    real = httpx.Client

    def fake(*args: object, **kwargs: object) -> httpx.Client:
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(github.httpx, "Client", fake)
    return calls


def test_conta_do_token_usa_user_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _client(monkeypatch, "Eu")
    repos = GitHubClient("tok").list_org_repos(Org(name="eu"))
    assert [r.url for r in repos] == [REPO["clone_url"]]
    assert any("/user/repos" in c and "affiliation=owner" in c for c in calls)
    assert not any("/users/eu/repos" in c for c in calls)


def test_outra_conta_usa_users_publicos(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _client(monkeypatch, "outra")
    GitHubClient("tok").list_org_repos(Org(name="eu"))
    assert any("/users/eu/repos" in c for c in calls)
    assert not any("/user/repos" in c for c in calls)


def test_sem_token_nao_consulta_user(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _client(monkeypatch, "eu")
    GitHubClient(None).list_org_repos(Org(name="eu"))
    assert not any(c.endswith("/user") for c in calls)
    assert any("/users/eu/repos" in c for c in calls)
