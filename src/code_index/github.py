"""Descoberta de repositórios de uma org (ou usuário) do GitHub via REST API."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import datetime

import httpx

from .config import Org
from .git_sync import github_token

API = "https://api.github.com"


@dataclass(frozen=True)
class DiscoveredRepo:
    name: str               # nome no índice: <org>__<repo>
    url: str                # clone_url https
    default_branch: str
    pushed_at: datetime | None
    size_kb: int
    language: str | None    # linguagem principal segundo o GitHub


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = github_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _paginate(client: httpx.Client, url: str) -> list[dict]:
    out: list[dict] = []
    next_url: str | None = url
    while next_url:
        r = client.get(next_url)
        if r.status_code == 404:
            raise LookupError(next_url)
        r.raise_for_status()
        out.extend(r.json())
        next_url = r.links.get("next", {}).get("url")
    return out


def list_org_repos(org: Org) -> list[DiscoveredRepo]:
    """Lista os repositórios visíveis para o token, já aplicando os filtros da org."""
    with httpx.Client(headers=_headers(), timeout=60) as client:
        try:
            raw = _paginate(client, f"{API}/orgs/{org.name}/repos?per_page=100&type=all")
        except LookupError:  # não é org: tenta como usuário
            raw = _paginate(client, f"{API}/users/{org.name}/repos?per_page=100&type=owner")

    out = []
    for r in raw:
        name = r["name"]
        if r.get("disabled") or (r.get("archived") and not org.include_archived):
            continue
        if r.get("fork") and not org.include_forks:
            continue
        if org.match and not any(fnmatch.fnmatch(name, g) for g in org.match):
            continue
        if any(fnmatch.fnmatch(name, g) for g in org.skip):
            continue
        if org.languages and (r.get("language") or "none").lower() not in {x.lower() for x in org.languages}:
            continue
        if org.max_size_mb is not None and r.get("size", 0) > org.max_size_mb * 1024:
            continue
        if not r.get("size"):  # repo vazio, nada para clonar
            continue
        pushed = r.get("pushed_at")
        out.append(DiscoveredRepo(
            name=org.repo_name(name),
            url=r["clone_url"],
            default_branch=r.get("default_branch") or "main",
            pushed_at=datetime.fromisoformat(pushed.replace("Z", "+00:00")) if pushed else None,
            size_kb=int(r.get("size") or 0),
            language=r.get("language"),
        ))
    return out
