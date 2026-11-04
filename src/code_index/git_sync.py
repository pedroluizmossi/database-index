"""Clona/atualiza repositórios git em REPOS_DIR.

O token do GitHub é passado só via `http.extraHeader` na linha de comando,
nunca gravado na URL do remoto nem no .git/config.
"""

from __future__ import annotations

import base64
import functools
import os
import shutil
import subprocess

from .config import REPOS_DIR, Repo


@functools.cache
def github_token() -> str | None:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        return token
    if shutil.which("gh"):
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def _git(*args: str, url: str | None = None) -> str:
    cmd = ["git"]
    token = github_token()
    if url and token and url.startswith("https://github.com/"):
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} falhou: {r.stderr.strip()}")
    return r.stdout.strip()


def sync_repo(repo: Repo) -> str | None:
    """Garante o clone atualizado (shallow) e retorna o commit HEAD.

    Repos com `path` local não são tocados; retorna o HEAD se for um repo git.
    """
    dest = repo.local_path
    if repo.url is None:
        if not dest.is_dir():
            raise RuntimeError(f"diretório não existe: {dest}")
        try:
            return _git("-C", str(dest), "rev-parse", "HEAD")
        except RuntimeError:
            return None

    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        args = ["clone", "--depth", "1", "--single-branch"]
        if repo.branch:
            args += ["--branch", repo.branch]
        _git(*args, repo.url, str(dest), url=repo.url)
    else:
        _git("-C", str(dest), "remote", "set-url", "origin", repo.url)
        _git("-C", str(dest), "fetch", "--depth", "1", "origin", repo.branch or "HEAD", url=repo.url)
        _git("-C", str(dest), "reset", "--hard", "FETCH_HEAD")
        _git("-C", str(dest), "clean", "-fdx")
    return _git("-C", str(dest), "rev-parse", "HEAD")


def remote_head(url: str, branch: str | None = None) -> str | None:
    """Commit atual do remoto sem clonar (git ls-remote)."""
    out = _git("ls-remote", url, branch or "HEAD", url=url)
    return out.split()[0] if out else None


def remove_clone(repo: Repo) -> None:
    if repo.url is not None and repo.local_path.is_dir():
        shutil.rmtree(repo.local_path)
