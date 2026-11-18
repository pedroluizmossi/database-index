"""Perfil senior: regras de nome, ruído, bucket do OpenAPI, handlers e origens com os valores de antes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath

import pytest
from cocoindex.resources.file import PatternFilePathMatcher

from code_index.domain.doc_links import repo_name_aliases
from code_index.domain.file_patterns import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, NOISE_EXCLUDE, TEST_EXCLUDE
from code_index.domain.ownership import base_name
from code_index.domain.relations import origin_order, origins_payload
from code_index.domain.repo_card import name_tokens, related_candidates
from code_index.interfaces.http.mcp import build_mcp
from code_index.profiles import registry
from code_index.profiles.registry import load_profiles
from code_index.services.base import prepare_profiles
from code_index.sources.openapi_s3 import OpenApiPlugin


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


def test_senior_mesmas_regras_de_nome_de_antes() -> None:
    n = load_profiles(["senior"]).naming
    assert n.repo_prefixes == ("snr-",)
    assert (n.short_prefix_max, n.alias_prefix_max) == (3, 4)
    assert n.generic_tokens == {"senior", "snr"}
    assert n.role_suffixes == {
        "frontend", "front", "backend", "back", "web", "ui", "app", "service", "services", "api", "server", "client",
        "lib", "library", "sdk", "core", "impl", "bff", "angular", "react", "vue", "java", "node", "go", "py",
    }
    assert base_name("org__snr-audit-authorization-frontend", n) == "audit_authorization"
    assert base_name("org__ms-blob-service", n) == "blob"
    assert [a for a, _ in repo_name_aliases("org__snr-email-backend", n)][-1] == "email-backend"
    assert [a for a, _ in repo_name_aliases("org__abcd-x", n)][-1] == "x"  # até 4 letras nos apelidos
    assert "senior" not in name_tokens("org__senior-pagamento", n)
    out = related_candidates("o__senior-email", ["o__senior-agenda", "o__email-web"], {}, naming=n)
    assert out == ["o__email-web"]


def test_senior_mesmo_exclude_padrao_de_antes() -> None:
    vendors = ["H2", "h2", "Oracle", "oracle", "SqlServer", "SQLServer", "sqlserver", "MySql", "MySQL", "mysql"]
    antes = DEFAULT_EXCLUDE + [f"**/{d}/**/{v}" for d in ("migration", "migrations") for v in vendors]
    active = load_profiles(["senior"])
    assert list(active.default_exclude) == antes  # mesma ordem: a chave de memo do CocoIndex não muda
    assert set(TEST_EXCLUDE) <= set(antes) and set(NOISE_EXCLUDE) <= set(antes)
    matcher = PatternFilePathMatcher(included_patterns=DEFAULT_INCLUDE, excluded_patterns=list(active.default_exclude))

    def indexed(rel: str) -> bool:
        path = PurePosixPath(rel)
        return all(matcher.is_dir_included(p) for p in path.parents if p != PurePosixPath(".")) \
            and matcher.is_file_included(path)

    for rel in ("db/migration/H2/V1.sql", "src/migration/a/Oracle/x.sql", "db/migrations/mysql/a.sql",
                "db/migration/SqlServer/V2.sql"):
        assert not indexed(rel)
    assert indexed("db/migration/postgresql/V1.sql")


def test_senior_padrao_do_bucket_openapi(make_settings) -> None:
    active = load_profiles(["senior"])
    assert active.setting(make_settings(), "openapi_bucket_url") == \
        "https://s3.amazonaws.com/br-com-senior-ecossistema-api"
    assert active.setting(make_settings(openapi_bucket_url="https://outro.invalid"), "openapi_bucket_url") == \
        "https://outro.invalid"



def test_senior_mesmo_linker_de_servico_de_antes() -> None:
    n = load_profiles(["senior"]).naming
    assert n.service_suffixes == ("service", "backend")
    linker = {lk.name: lk for lk in OpenApiPlugin().linkers(n)}["openapi-service-name"]
    assert linker._prefix_regex == "^(snr-)" and linker._suffix_regex == "(service|backend)$"  # type: ignore[attr-defined]


def test_senior_mantem_handler_e_origens_na_ordem_de_antes() -> None:
    active = load_profiles(["senior"])
    c = active.impl_convention
    assert c is not None and c.names("login__9")[0] == "loginv9handler"
    assert [lk.name for lk in active.linkers] == ["handler", "openapi-sdl"]
    assert origin_order() == ("manual", "agent", "llm", "explicit", "source", "sdl", "declared", "sibling",
                              "api_calls", "usage", "handler", "service", "card", "tag", "url", "mention")
    registry.clear_cache()
    assert {"sdl", "handler"}.isdisjoint(origin_order())  # sem perfil, não são do núcleo



async def test_mcp_registra_find_api_impl_com_o_senior(make_settings) -> None:
    settings = make_settings(profiles=["senior"], auth_secret="x" * 40)
    assert prepare_profiles(settings).impl_convention is not None
    tools = {t.name for t in await build_mcp(settings, lambda: None, None).list_tools()}  # type: ignore[arg-type]
    assert "find_api_impl" in tools


def test_senior_so_declared_e_autoritativa() -> None:
    # o aviso de "a atribuição manual vai sobrepor" na interface vale só para o dono declarado pelo próprio repo
    load_profiles(["senior"])
    assert {o["name"] for o in origins_payload() if o["authoritative"]} == {"declared"}
