"""Clona/atualiza repositórios git em `repos_dir` (síncrono, via subprocess).

O token do GitHub é passado só via `http.extraheader` na linha de comando,
nunca gravado na URL do remoto nem no .git/config.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

from code_index.domain.models import Repo
from code_index.settings import Settings


def resolve_github_token(settings: Settings) -> str | None:
    """Token do Settings (GITHUB_TOKEN/GH_TOKEN); se vazio, tenta `gh auth token`."""
    if settings.github_token:
        return settings.github_token
    if shutil.which("gh"):
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


class GitClient:
    def __init__(self, token: str | None, repos_dir: Path) -> None:
        self._token = token
        self._repos_dir = repos_dir

    def _git(self, *args: str, url: str | None = None) -> str:
        cmd = ["git"]
        if url and self._token and url.startswith("https://github.com/"):
            basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
            cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
        cmd += list(args)
        # os.environ aqui é só repasse ao processo git (PATH, HOME, credenciais), não é configuração.
        r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]} falhou: {r.stderr.strip()}")
        return r.stdout.strip()

    def sync(self, repo: Repo) -> str | None:
        """Garante o clone atualizado (shallow) e retorna o commit HEAD.

        Repos com `path` local não são tocados; retorna o HEAD se for um repo git.
        """
        dest = repo.local_path(self._repos_dir)
        if repo.url is None:
            if not dest.is_dir():
                raise RuntimeError(f"diretório não existe: {dest}")
            try:
                return self._git("-C", str(dest), "rev-parse", "HEAD")
            except RuntimeError:
                return None

        self._repos_dir.mkdir(parents=True, exist_ok=True)
        if not (dest / ".git").is_dir():
            args = ["clone", "--depth", "1", "--single-branch"]
            if repo.branch:
                args += ["--branch", repo.branch]
            self._git(*args, repo.url, str(dest), url=repo.url)
        else:
            self._git("-C", str(dest), "remote", "set-url", "origin", repo.url)
            self._git("-C", str(dest), "fetch", "--depth", "1", "origin", repo.branch or "HEAD", url=repo.url)
            self._git("-C", str(dest), "reset", "--hard", "FETCH_HEAD")
            self._git("-C", str(dest), "clean", "-fdx")
        return self._git("-C", str(dest), "rev-parse", "HEAD")

    def remote_head(self, url: str, branch: str | None = None) -> str | None:
        """Commit atual do remoto sem clonar (git ls-remote)."""
        out = self._git("ls-remote", url, branch or "HEAD", url=url)
        return out.split()[0] if out else None

    def remove_clone(self, repo: Repo) -> None:
        path = repo.local_path(self._repos_dir)
        if repo.url is not None and path.is_dir():
            shutil.rmtree(path)
