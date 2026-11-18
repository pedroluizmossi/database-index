"""Fixtures compartilhadas. Unitários não usam rede nem banco; integração fica em tests/integration/.

Perfis de organização: `--profiles default` (ou CODE_INDEX_TEST_PROFILES=default) roda a suíte com esses perfis nos
Settings dos testes (`make_settings`, `int_settings`); sem a opção, vale o padrão do Settings. Teste que depende de
um perfil ativo leva `@pytest.mark.profile("nome")` e é pulado quando o perfil não está ativo ou instalado. Os
testes do comportamento de um perfil ficam em tests/profiles/<nome>/ (saem junto com o pacote do perfil)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from code_index.settings import Settings

# Variáveis lidas pelo Settings. Removidas nos testes para não vazar do .env ou do shell.
SETTINGS_ENV = (
    "CODE_INDEX_HOME", "POSTGRES_URL", "EMBED_MODEL", "EMBED_DIMENSIONS", "EMBED_MAX_INFLIGHT",
    "RERANK_MODEL", "RERANK_CANDIDATES", "GITHUB_TOKEN", "GH_TOKEN",
    "CODE_INDEX_REPOS_DIR", "COCOINDEX_DB", "CODE_INDEX_BATCH",
    "CODE_INDEX_HTTP_HOST", "CODE_INDEX_HTTP_PORT", "CODE_INDEX_PUBLIC_URL", "CODE_INDEX_ALLOWED_HOSTS",
    "CODE_INDEX_AUTH_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ALLOWED_DOMAIN", "GOOGLE_ALLOWED_EMAILS",
    "CODE_INDEX_ADMIN_EMAILS", "CODE_INDEX_SESSION_TTL_HOURS", "CODE_INDEX_TOKEN_TTL_DAYS",
    "CODE_INDEX_RATE_READ_PER_MIN", "CODE_INDEX_RATE_SEARCH_PER_MIN", "CODE_INDEX_RATE_SYNC_PER_HOUR",
    "CODE_INDEX_URL", "CODE_INDEX_TOKEN", "CODE_INDEX_CREDENTIALS", "CODE_INDEX_CLI_MODE", "CODE_INDEX_REMOTE_TIMEOUT",
    "XDG_CONFIG_HOME", "CODE_INDEX_PROFILES", "OPENAPI_BUCKET_URL",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--profiles", default=None,
                     help="perfis de organização dos Settings dos testes (CSV, ex.: default); padrão: o do Settings")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "profile(name): precisa do perfil de organização ativo nos Settings dos testes")


def requested_profiles(config: pytest.Config) -> list[str] | None:
    """Perfis pedidos para a rodada (`--profiles` ou CODE_INDEX_TEST_PROFILES); None = padrão do Settings."""
    raw = config.getoption("--profiles") or os.environ.get("CODE_INDEX_TEST_PROFILES")
    if raw is None:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def run_profiles(config: pytest.Config) -> list[str]:
    """Perfis efetivos dos Settings dos testes nesta rodada."""
    wanted = requested_profiles(config)
    if wanted is not None:
        return wanted
    factory = Settings.model_fields["profiles"].default_factory
    return list(factory()) if factory is not None else []  # type: ignore[call-arg]


def pytest_runtest_setup(item: pytest.Item) -> None:
    from code_index.profiles.registry import available_profiles

    for mark in item.iter_markers("profile"):
        name = mark.args[0]
        if name not in available_profiles():
            pytest.skip(f"perfil {name!r} não instalado")
        if name not in run_profiles(item.config):
            pytest.skip(f"perfil {name!r} fora dos perfis desta rodada (--profiles)")


@pytest.fixture(scope="session")
def profiles_option(pytestconfig: pytest.Config) -> list[str] | None:
    """Perfis pedidos para a rodada (None = padrão do Settings)."""
    return requested_profiles(pytestconfig)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tira do ambiente as variáveis de configuração."""
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_settings(tmp_path, clean_env, profiles_option) -> Callable[..., Settings]:
    """Fábrica de Settings isolado: não lê .env e todos os caminhos ficam em tmp_path."""

    def _make(**overrides) -> Settings:
        base = Path(overrides.pop("base_dir", tmp_path / "home"))
        base.mkdir(parents=True, exist_ok=True)
        values: dict = {
            "_env_file": None,
            "base_dir": base,
            "repos_dir": base / ".repos",
            "cocoindex_db": base / ".cocoindex",
        }
        if profiles_option is not None:
            values["profiles"] = profiles_option
        values.update(overrides)
        return Settings(**values)

    return _make
