"""Fixtures compartilhadas. Unitários não usam rede nem banco; integração fica em tests/integration/."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from code_index.settings import Settings

# Variáveis lidas pelo Settings. Removidas nos testes para não vazar do .env ou do shell.
SETTINGS_ENV = (
    "CODE_INDEX_HOME", "POSTGRES_URL", "EMBED_MODEL", "EMBED_DIMENSIONS", "EMBED_MAX_INFLIGHT",
    "EMBED_API_BASE", "EMBED_API_KEY", "LOCAL_EMBED_MODEL", "LOCAL_EMBED_REVISION",
    "RERANK_MODEL", "RERANK_CANDIDATES", "GITHUB_TOKEN", "GH_TOKEN", "CODE_INDEX_REPOS",
    "CODE_INDEX_REPOS_DIR", "COCOINDEX_DB", "CODE_INDEX_BATCH",
    "CODE_INDEX_HTTP_HOST", "CODE_INDEX_HTTP_PORT", "CODE_INDEX_PUBLIC_URL", "CODE_INDEX_ALLOWED_HOSTS",
    "CODE_INDEX_AUTH_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ALLOWED_DOMAIN", "GOOGLE_ALLOWED_EMAILS",
    "CODE_INDEX_ADMIN_EMAILS", "CODE_INDEX_SESSION_TTL_HOURS", "CODE_INDEX_TOKEN_TTL_DAYS",
    "CODE_INDEX_RATE_READ_PER_MIN", "CODE_INDEX_RATE_SEARCH_PER_MIN", "CODE_INDEX_RATE_SYNC_PER_HOUR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tira do ambiente as variáveis de configuração."""
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_settings(tmp_path, clean_env) -> Callable[..., Settings]:
    """Fábrica de Settings isolado: não lê .env e todos os caminhos ficam em tmp_path."""

    def _make(**overrides) -> Settings:
        base = Path(overrides.pop("base_dir", tmp_path / "home"))
        base.mkdir(parents=True, exist_ok=True)
        values: dict = {
            "_env_file": None,
            "base_dir": base,
            "repos_file": base / "repos.toml",
            "repos_dir": base / ".repos",
            "cocoindex_db": base / ".cocoindex",
            "lock_file": base / ".code-index.lock",
        }
        values.update(overrides)
        return Settings(**values)

    return _make
