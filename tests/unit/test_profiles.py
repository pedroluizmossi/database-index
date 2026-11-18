"""Perfis de organização: contrato, registro (embutidos e entry points), combinação e perfil default (sintéticos; o
perfil senior tem os testes dele em tests/profiles/senior/)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import pytest
from cocoindex.resources.file import PatternFilePathMatcher

from code_index.domain.doc_links import repo_name_aliases
from code_index.domain.file_patterns import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, NOISE_EXCLUDE, TEST_EXCLUDE
from code_index.domain.models import Org, Repo, RepoState
from code_index.domain.naming import NameRules
from code_index.domain.ownership import base_name
from code_index.domain.repo_card import name_tokens, related_candidates
from code_index.profiles import registry
from code_index.profiles.base import ActiveProfiles, BaseProfile, OrgProfile
from code_index.profiles.registry import available_profiles, load_profiles
from code_index.settings import Settings
from code_index.sources.openapi_s3 import OpenApiPlugin, ServiceNameLinker, _pg_prefix_regex, _pg_suffix_regex


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


class Acme(BaseProfile):
    """Perfil sintético de teste."""

    name = "acme"
    label = "Acme"

    def naming(self) -> NameRules:
        return NameRules(repo_prefixes=("acme-",), short_prefix_max=2, generic_tokens=frozenset({"acme"}),
                         role_suffixes=frozenset({"frontend"}))

    def noise_exclude(self) -> Sequence[str]:
        return ("**/gerado/**", "**/node_modules")

    def settings_defaults(self) -> Mapping[str, Any]:
        return {"openapi_bucket_url": "https://acme.invalid/a"}


class Beta(BaseProfile):
    name = "beta"
    label = "Beta"

    def naming(self) -> NameRules:
        return NameRules(repo_prefixes=("bt-", "acme-"), alias_prefix_max=3, generic_tokens=frozenset({"beta"}))

    def noise_exclude(self) -> Sequence[str]:
        return ("**/legado/**", "**/gerado/**")

    def settings_defaults(self) -> Mapping[str, Any]:
        return {"openapi_bucket_url": "https://beta.invalid/b"}


class FakeEntryPoint:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self.value, self._obj = name, f"pacote_de_teste:{name}", obj

    def load(self) -> Any:
        return self._obj


def use_entry_points(monkeypatch: pytest.MonkeyPatch, **objs: Any) -> None:
    monkeypatch.setattr(registry, "_entry_points", lambda: {n: FakeEntryPoint(n, o) for n, o in objs.items()})
    registry.clear_cache()


# ------------------------------------------------------------------ registro

def test_embutidos_disponiveis_e_default_vazio() -> None:
    assert "default" in available_profiles()
    for names in ([], ["default"], ["", " default "]):
        active = load_profiles(names)
        assert active.profiles == () and active.names == ()


def test_perfil_desconhecido_falha_com_os_disponiveis() -> None:
    with pytest.raises(ValueError, match="desconhecido.*default"):
        load_profiles(["nao-existe"])
    with pytest.raises(ValueError, match="inválido"):
        load_profiles(["../x"])


def test_entry_point_carrega_classe_fabrica_ou_instancia(monkeypatch: pytest.MonkeyPatch) -> None:
    use_entry_points(monkeypatch, acme=Acme, beta=lambda: Beta())
    assert "acme" in available_profiles()
    active = load_profiles(["ACME", "beta", "acme", "default"])  # ordem pedida, sem repetir, sem o default
    assert active.names == ("acme", "beta")
    assert isinstance(active.profiles[0], OrgProfile)
    use_entry_points(monkeypatch, acme=Acme())
    assert load_profiles(["acme"]).names == ("acme",)


def test_entry_point_substitui_o_embutido(monkeypatch: pytest.MonkeyPatch) -> None:
    class OutroAcme(BaseProfile):
        name = "acme"
        label = "de fora"

    monkeypatch.setitem(registry._BUILTIN, "acme", f"{__name__}:Acme")  # um embutido sintético
    use_entry_points(monkeypatch)
    assert load_profiles(["acme"]).profiles[0].label == "Acme"
    use_entry_points(monkeypatch, acme=OutroAcme)
    assert load_profiles(["acme"]).profiles[0].label == "de fora"


def test_entry_point_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    use_entry_points(monkeypatch, quebrado=lambda: object())
    with pytest.raises(TypeError, match="contrato"):
        load_profiles(["quebrado"])
    use_entry_points(monkeypatch, outro=Acme)
    with pytest.raises(ValueError, match="se chama 'acme'"):
        load_profiles(["outro"])

    class Intruso(Acme):
        def settings_defaults(self) -> Mapping[str, Any]:
            return {"auth_secret": "x"}

    use_entry_points(monkeypatch, acme=Intruso)
    with pytest.raises(ValueError, match="auth_secret"):
        load_profiles(["acme"])


def test_settings_le_os_perfis_em_csv(clean_env, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def settings() -> Settings:
        return Settings(_env_file=None, base_dir=tmp_path)

    assert settings().profiles == ["senior"]  # padrão por enquanto: o índice existente não muda
    monkeypatch.setenv("CODE_INDEX_PROFILES", "default, acme")
    assert settings().profiles == ["default", "acme"]
    monkeypatch.setenv("CODE_INDEX_PROFILES", "")
    assert settings().profiles == []
    assert registry.active_profiles(settings()).profiles == ()


# ------------------------------------------------------------------ combinação

def test_combinacao_de_perfis() -> None:
    active = ActiveProfiles((Acme(), Beta()))
    n = active.naming
    assert n.repo_prefixes == ("acme-", "bt-")
    assert (n.short_prefix_max, n.alias_prefix_max) == (2, 3)
    assert n.generic_tokens == {"acme", "beta"} and n.role_suffixes == {"frontend"}
    assert active.noise_exclude == ("**/gerado/**", "**/node_modules", "**/legado/**")
    # o que já está no núcleo não se repete
    assert active.default_exclude == (*DEFAULT_EXCLUDE, "**/gerado/**", "**/legado/**")


def test_configuracao_do_ambiente_vence_o_perfil(make_settings) -> None:
    active = ActiveProfiles((Acme(), Beta()))
    assert active.setting(make_settings(), "openapi_bucket_url") == "https://acme.invalid/a"  # o primeiro
    assert active.setting(make_settings(openapi_bucket_url="https://env.invalid"), "openapi_bucket_url") \
        == "https://env.invalid"
    with pytest.raises(KeyError):
        active.setting(make_settings(), "database_url")


def test_bucket_vazio_no_ambiente_vale_o_perfil(make_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAPI_BUCKET_URL", " ")
    assert make_settings().openapi_bucket_url is None


# ------------------------------------------------------------------ perfil default: sem convenções

def test_default_sem_convencoes(make_settings) -> None:
    active = load_profiles(["default"])
    assert active.naming == NameRules()
    assert active.noise_exclude == () and active.default_exclude == tuple(DEFAULT_EXCLUDE)
    assert active.setting(make_settings(), "openapi_bucket_url") is None
    assert base_name("org__abc-pagamento-frontend", active.naming) == "abc_pagamento_frontend"
    assert [a for a, _ in repo_name_aliases("org__abc-pagamento", active.naming)] == [
        "org__abc-pagamento", "org/abc-pagamento", "abc-pagamento"]
    assert name_tokens("org__acme-pagamento", active.naming) == {"acme", "pagamento"}
    # o núcleo não traz URL de nenhuma empresa
    assert make_settings().openapi_bucket_url is None


def test_repo_recebe_o_exclude_padrao_dos_perfis() -> None:
    noisy = (*DEFAULT_EXCLUDE, "**/gerado/**")
    assert Repo(name="r", url="u").with_default_exclude(noisy).exclude == noisy
    proprio = Repo(name="r", url="u", exclude=("**/x",))
    assert proprio.with_default_exclude(noisy) is proprio  # exclude gravado na configuração fica
    docs = Repo(name="docs__w", url="u", kind="docs", exclude=("**/.*",))
    assert docs.with_default_exclude(noisy).exclude == ("**/.*",)


def state(name: str, *, org: str | None = None, url: str | None = None, path: str | None = None) -> RepoState:
    return RepoState(name=name, org=org, url=url, path=path, default_branch=None, pushed_at=None, size_kb=None,
                     ignored=False, indexed_commit=None, revision=None, indexed_pushed_at=None, indexed_at=None,
                     error=None, updated_at=datetime.now(timezone.utc))


def test_repo_for_row_usa_o_exclude_dos_perfis() -> None:
    from code_index.indexing.orchestrator import repo_for_row

    noisy = ActiveProfiles((Acme(),)).default_exclude
    org_row = state("o__r", org="o", url="https://x.invalid/r.git")
    repo = repo_for_row(org_row, [Org(name="o", extra_exclude=("**/tmp",))], {}, default_include=DEFAULT_INCLUDE,
                        default_exclude=noisy)
    assert repo.exclude == noisy and repo.all_excludes == [*noisy, "**/tmp"]
    avulso = state("local", path="/tmp/x")
    assert repo_for_row(avulso, [], {}, default_include=DEFAULT_INCLUDE,
                        default_exclude=tuple(DEFAULT_EXCLUDE)).exclude == tuple(DEFAULT_EXCLUDE)


def test_linker_de_servico_sem_prefixo_e_com_prefixo_escapado() -> None:
    assert _pg_prefix_regex([]) is None
    assert _pg_prefix_regex(["acme-"]) == "^(acme-)"
    assert _pg_prefix_regex(["a.b", "x|y"]) == r"^(a\.b|x\|y)"
    linkers = {lk.name: lk for lk in OpenApiPlugin().linkers(NameRules(repo_prefixes=("Acme-",)))}
    assert isinstance(linkers["openapi-service-name"], ServiceNameLinker)
    assert linkers["openapi-service-name"]._prefix_regex == "^(acme-)"
    assert ServiceNameLinker()._prefix_regex is None
    assert "openapi-sdl" not in linkers  # SDL → repo é linker do perfil senior
    # sufixos de serviço: só com perfil (sem eles, só o nome exato casa)
    assert _pg_suffix_regex([]) is None and ServiceNameLinker()._suffix_regex is None
    assert _pg_suffix_regex(["service", "a.b"]) == r"(service|a\.b)$"
    linkers = {lk.name: lk for lk in OpenApiPlugin().linkers(NameRules(service_suffixes=("Svc",)))}
    assert linkers["openapi-service-name"]._suffix_regex == "(svc)$"


# ------------------------------------------------------------------ perfil senior: os valores de antes


