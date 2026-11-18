"""Perfil senior: dono no DDD (catálogo do SDL, declared/sibling/api_calls), a ordem das origens de antes e a
combinação com outro perfil (sintético)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any

import pytest

from code_index.domain import ownership as core
from code_index.domain import relations
from code_index.domain.naming import NameRules
from code_index.domain.ownership import (
    Assignment, Catalog, FactsByRepo, Owner, OwnByRepo, OwnershipRules, derived_owner_origins, infer, owner_origins,
    repo_owners,
)
from code_index.domain.relations import Origin, OriginPlacement, repo_key
from code_index.indexing.enrich import collect_material
from code_index.indexing.linkers import OwnershipLinker
from code_index.profiles import registry
from code_index.profiles.base import BaseProfile
from code_index.profiles.registry import load_profiles
from code_index.profiles.senior import SeniorProfile
from code_index.profiles.senior.ownership import (
    SeniorOwnership, api_call_owners, api_refs, build_catalog, declared, siblings,
)
from code_index.profiles.senior.sdl import parse_sdl_labels

SENIOR = (SeniorOwnership(),)

ORDER = owner_origins(SENIOR)  # manual, declared, sibling, api_calls, usage


def resolve(items):  # type: ignore[no-untyped-def]
    """Resolução com a ordem das origens do perfil senior (sem depender do registro global)."""
    return core.resolve(items, ORDER)



ORG = "senior-sistemas__"

RULES = NameRules(short_prefix_max=3, role_suffixes=frozenset({"frontend", "backend", "service", "web", "ui", "library"}))

SDL = {
    f"{ORG}snr-audit-backend": [{"domain": "platform", "service": "audit", "path": "audit.sdl"}],
    f"{ORG}snr-authentication-backend": [{"domain": "platform", "service": "authentication", "path": "a.sdl"}],
    f"{ORG}snr-payroll": [{"domain": "hcm", "service": "payroll", "path": "payroll.sdl"}],
}


def catalog():
    return build_catalog(SDL, {f"{ORG}snr-audit-backend": {"domains": {"platform": "Plataforma"},
                                                           "services": {"audit": "Auditoria"}}})


def test_catalogo_com_rotulos() -> None:
    c = catalog()
    assert c.domains == {"platform": "Plataforma", "hcm": ""}
    assert c.services[("platform", "audit")].label == "Auditoria"
    assert c.has(Owner("hcm", "payroll")) and c.has(Owner("hcm")) and not c.has(Owner("hcm", "vacation"))


def test_rotulos_do_sdl() -> None:
    text = 'domain platform ("Plataforma")\n// service velho ("x")\nservice blob_service ("Arquivos") {\n}'
    assert parse_sdl_labels(text) == {"domains": {"platform": "Plataforma"}, "services": {"blob_service": "Arquivos"}}


def test_declarado_e_monorepo_por_pasta() -> None:
    sdl = {**SDL, "erp": [{"domain": "hcm", "service": "payroll", "path": "payroll/p.sdl"},
                          {"domain": "hcm", "service": "vacation", "path": "vacation/v.sdl"}]}
    got = {(a.entity, a.owner.label) for a in declared(sdl)}
    assert ("repo:erp", "hcm/payroll") in got and ("repo:erp", "hcm/vacation") in got
    assert ("dir:erp/payroll", "hcm/payroll") in got and ("dir:erp/vacation", "hcm/vacation") in got
    # um serviço só: sem divisão por pasta
    assert not [a for a in declared(SDL) if a.entity.startswith("dir:")]


def test_irmaos_herdam_e_nome_igual_ao_servico() -> None:
    decl = repo_owners(resolve(declared(SDL)))
    repos = [*SDL, f"{ORG}snr-audit-frontend", f"{ORG}snr-payroll-web", f"{ORG}hcm-payroll-ui", f"{ORG}snr-misc"]
    got = {a.entity: (a.owner.label, a.confidence) for a in siblings(repos, decl, catalog(), naming=RULES)}
    assert got[f"repo:{ORG}snr-audit-frontend"] == ("platform/audit", "alta")
    assert got[f"repo:{ORG}snr-payroll-web"] == ("hcm/payroll", "alta")
    assert f"repo:{ORG}snr-misc" not in got and f"repo:{ORG}snr-audit-backend" not in got
    assert got[f"repo:{ORG}hcm-payroll-ui"] == ("hcm/payroll", "alta")  # prefixo curto 'hcm' sai do nome-base


def test_rotas_chamadas() -> None:
    text = "get(`platform/audit/queries/x`); post('platform/authentication/actions/login'); 'outra/coisa/aqui/'"
    assert api_refs(text) == {"platform/audit": 1, "platform/authentication": 1}


def test_dono_pelas_chamadas_de_api_ignora_servico_utilitario() -> None:
    c = build_catalog({**SDL, "th": [{"domain": "platform", "service": "translation_hub", "path": "t.sdl"}]})
    refs = {f"front{i}": {"platform/translation_hub": 5} for i in range(6)}  # todo mundo chama tradução
    refs["front-auth"] = {"platform/authentication": 3, "platform/translation_hub": 9, "nao/existe": 50}
    refs["front-dividido"] = {"platform/audit": 2, "hcm/payroll": 2, "platform/authentication": 1}
    refs["front-pouco"] = {"hcm/payroll": 1}
    got = {a.entity: a for a in api_call_owners(refs, c)}
    assert got["repo:front-auth"].owner == Owner("platform", "authentication")
    assert got["repo:front-auth"].confidence == "alta"
    assert "repo:front-dividido" not in got and "repo:front-pouco" not in got
    assert got["repo:front0"].owner == Owner("platform", "translation_hub")  # só chama esse: vale


def test_inferencia_completa_na_ordem() -> None:
    repos = [*SDL, f"{ORG}snr-audit-frontend", f"{ORG}snr-portal", f"{ORG}snr-audit-library", f"{ORG}snr-trilha",
             f"{ORG}snr-fixo"]
    own = {
        f"{ORG}snr-portal": {"api_refs": {"platform/authentication": 4}},
        f"{ORG}snr-audit-library": {"artifacts": ["audit-library"]},
        f"{ORG}snr-trilha": {"artifacts": ["trilha-lib"]},
        f"{ORG}snr-audit-backend": {"dependencies": ["audit-library", "trilha-lib"],
                                    "sdl_labels": {"services": {"audit": "Auditoria"}}},
        f"{ORG}snr-audit-frontend": {"dependencies": ["trilha-lib"]},
        f"{ORG}snr-fixo": {"api_refs": {"platform/audit": 9}},
    }
    manual = [Assignment(f"repo:{ORG}snr-fixo", Owner("hcm"), "manual")]
    facts = {r: {"sdl": v} for r, v in SDL.items()}
    got = {(a.entity.split("__")[-1], a.owner.label, a.origin)
           for a in infer(repos, facts, own, manual, rules=SENIOR, naming=RULES)}
    assert ("snr-audit-backend", "platform/audit", "declared") in got
    assert ("snr-audit-frontend", "platform/audit", "sibling") in got
    assert ("snr-portal", "platform/authentication", "api_calls") in got
    assert ("snr-audit-library", "platform/audit", "sibling") in got  # nome-base 'audit' já basta
    assert ("snr-trilha", "platform/audit", "usage") in got  # consumidores: um declarado, um irmão
    assert not [g for g in got if g[0] == "snr-audit-library" and g[2] == "usage"]  # irmão já é dono
    assert ("snr-fixo", "platform/audit", "api_calls") in got  # inferido mesmo com dono manual...
    final = resolve([*infer(repos, facts, own, manual, rules=SENIOR, naming=RULES), *manual])
    assert final[f"repo:{ORG}snr-fixo"].owners == [Owner("hcm")]  # ...mas o manual vence na resolução



# ------------------------------------------------------------------ ordem global e outro perfil junto

# A ordem global de antes da refatoração (com o perfil senior): não pode mudar.
ORDEM_DE_ANTES = ("manual", "agent", "llm", "explicit", "source", "sdl", "declared", "sibling", "api_calls", "usage",
                  "handler", "service", "card", "tag", "url", "mention")

# sdl e handler (operação → handler, SDL → página de API) também são do perfil senior
NUCLEO = tuple(o for o in ORDEM_DE_ANTES if o not in ("sdl", "declared", "sibling", "api_calls", "handler"))


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


def use_entry_points(monkeypatch: pytest.MonkeyPatch, **objs: Any) -> None:
    class EP:
        def __init__(self, name: str, obj: Any) -> None:
            self.name, self.value, self._obj = name, f"pacote_de_teste:{name}", obj

        def load(self) -> Any:
            return self._obj

    monkeypatch.setattr(registry, "_entry_points", lambda: {n: EP(n, o) for n, o in objs.items()})
    registry.clear_cache()


class OwnersFileRules:
    """`OWNERS` na raiz com 'dominio/servico' → origem `owners_file`. Catálogo: os serviços citados nesses arquivos."""

    origins: tuple[str, ...] = ("owners_file",)
    fact_sections: tuple[str, ...] = ()
    material_keys: tuple[str, ...] = ("owners_file",)

    def collect(self, paths: Sequence[str], files: Mapping[str, str],
                read: Callable[[str], str | None]) -> dict[str, Any]:
        text = read("OWNERS") if "OWNERS" in paths else None
        return {"owners_file": [x for x in (text or "").split() if "/" in x]}

    def catalog(self, facts: FactsByRepo, own: OwnByRepo) -> Catalog:
        c = Catalog()
        for repo, o in sorted(own.items()):
            for item in o.get("owners_file") or []:
                d, s = item.split("/", 1)
                c.add(Owner(d, s), repo=repo, label=f"rótulo {s}")
        return c

    def assign(self, repos: AbstractSet[str], facts: FactsByRepo, own: OwnByRepo, catalog: Catalog,
               naming: NameRules) -> list[Assignment]:
        out = []
        for repo, o in sorted(own.items()):
            for item in o.get("owners_file") or []:
                if repo in repos:
                    out.append(Assignment(repo_key(repo), Owner(*item.split("/", 1)), "owners_file", "alta"))
        return out


OWNERS_ORIGIN = Origin("owners_file", "arquivo OWNERS", "dono declarado no arquivo OWNERS do repo")


class Acme(BaseProfile):
    name = "acme"
    label = "Acme"

    def origins(self) -> Sequence[OriginPlacement]:
        return (OriginPlacement(before="usage", origins=(OWNERS_ORIGIN,)),)

    def ownership(self) -> Sequence[OwnershipRules]:
        return (OwnersFileRules(),)



def test_senior_mesma_ordem_global_e_do_dono_de_antes() -> None:
    assert relations.origin_order() == NUCLEO  # sem perfil carregado: só o núcleo
    active = load_profiles(["senior"])
    assert relations.origin_order() == ORDEM_DE_ANTES
    assert active.owner_origins == ("manual", "declared", "sibling", "api_calls", "usage")
    assert derived_owner_origins(active.ownership_rules) == {"declared", "sibling", "api_calls", "usage"}
    assert active.origin_names == {"sdl", "declared", "sibling", "api_calls", "handler"}
    payload = {o["name"]: o for o in relations.origins_payload()}
    assert payload["declared"]["label"] == "SDL do repo" and not payload["declared"]["contestable"]
    assert relations.sort_origins(["usage", "zz", "api_calls", "manual", "declared"]) == [
        "manual", "declared", "api_calls", "usage", "zz"]
    load_profiles(["senior"])  # carregar de novo não duplica
    assert relations.origin_order() == ORDEM_DE_ANTES


def test_ordem_de_forca_manual_declared_sibling_api_usage() -> None:
    order = load_profiles(["senior"]).owner_origins
    chain = [("manual", Owner("m")), ("declared", Owner("d", "x")), ("sibling", Owner("s", "x")),
             ("api_calls", Owner("a", "x")), ("usage", Owner("u"))]
    for i, (origin, owner) in enumerate(chain):
        items = [Assignment("repo:r", o, name) for name, o in chain[i:]]
        got = core.resolve(items, order)
        assert got["repo:r"].origin == origin and got["repo:r"].owners == [owner]
        assert [a.origin for a in got["repo:r"].weaker] == [name for name, _ in chain[i + 1:]]


def test_usage_usa_os_donos_do_perfil() -> None:
    rules = load_profiles(["senior"]).ownership_rules
    facts = {"o__pedidos-backend": {"sdl": [{"domain": "loja", "service": "pedidos", "path": "p.sdl"}]}}
    own: dict[str, Any] = {
        "o__pedidos-web": {"dependencies": ["comum-lib"]},              # irmão → loja/pedidos
        "o__vitrine": {"api_refs": {"loja/pedidos": 5}, "dependencies": ["comum-lib"]},  # chamadas → loja/pedidos
        "o__comum": {"artifacts": ["comum-lib"]},
    }
    repos = ["o__pedidos-backend", "o__pedidos-web", "o__vitrine", "o__comum"]
    naming = NameRules(role_suffixes=frozenset({"backend", "web"}))
    got = {(a.entity, a.owner.label, a.origin) for a in infer(repos, facts, own, rules=rules, naming=naming)}
    assert got == {("repo:o__pedidos-backend", "loja/pedidos", "declared"),
                   ("repo:o__pedidos-web", "loja/pedidos", "sibling"),
                   ("repo:o__vitrine", "loja/pedidos", "api_calls"),
                   ("repo:o__comum", "loja/pedidos", "usage")}
    # sem as regras do perfil, os consumidores não têm dono: nenhuma inferência
    assert infer(repos, facts, own, naming=naming) == []
    # ... a não ser o dono manual, que conta para o uso de lib
    manual = [Assignment("repo:o__vitrine", Owner("financeiro"), "manual")]
    assert [(a.entity, a.owner.label, a.origin) for a in infer(repos, facts, own, manual)] == [
        ("repo:o__comum", "financeiro", "usage")]


def test_perfil_ficticio_com_outra_origem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from code_index.profiles.senior import SeniorProfile

    use_entry_points(monkeypatch, acme=Acme, senior=SeniorProfile)
    active = load_profiles(["senior", "acme"])
    assert relations.origin_order()[6:11] == ("declared", "sibling", "api_calls", "owners_file", "usage")
    assert active.owner_origins == ("manual", "declared", "sibling", "api_calls", "owners_file", "usage")

    # coleta: cada perfil grava as suas chaves no material, as do núcleo continuam
    (tmp_path / "OWNERS").write_text("loja/estoque\n")
    m = collect_material(tmp_path, language=None, symbols=[], extractors=active.fact_extractors,
                         ownership=active.ownership_rules)
    assert set(m["ownership"]) == {"artifacts", "dependencies", "sdl_labels", "api_refs", "owners_file"}
    assert m["ownership"]["owners_file"] == ["loja/estoque"]

    facts = {"o__pedidos": {"sdl": [{"domain": "loja", "service": "pedidos", "path": "p.sdl"}]}}
    own = {"o__deposito": {"owners_file": ["loja/estoque"]},
           "o__vitrine": {"owners_file": ["loja/estoque"], "api_refs": {"loja/pedidos": 4}},
           "o__lib": {"artifacts": ["lib-x"]}, "o__app": {"dependencies": ["lib-x"]},
           "o__app2": {"owners_file": ["loja/estoque"], "dependencies": ["lib-x"]}}
    repos = ["o__pedidos", *own]
    catalog = core.build_catalog(active.ownership_rules, facts, own)
    assert catalog.has(Owner("loja", "pedidos")) and catalog.services[("loja", "estoque")].label == "rótulo estoque"

    found = infer(repos, facts, own, rules=active.ownership_rules)
    final = core.resolve(found, active.owner_origins)
    assert (final["repo:o__deposito"].origin, final["repo:o__deposito"].owners) == (
        "owners_file", [Owner("loja", "estoque")])
    assert final["repo:o__vitrine"].origin == "api_calls"  # a origem do senior vem antes da do acme
    assert [a.origin for a in final["repo:o__vitrine"].weaker] == ["owners_file"]
    assert (final["repo:o__lib"].origin, final["repo:o__lib"].owners) == ("usage", [Owner("loja", "estoque")])

    # só o acme: a origem dele fica entre manual e usage
    registry.clear_cache()
    alone = load_profiles(["acme"])
    assert alone.owner_origins == ("manual", "owners_file", "usage")
    assert relations.origin_order() == (*NUCLEO[:5], "owners_file", *NUCLEO[5:])
    linker = OwnershipLinker(alone.naming, alone.ownership_rules)
    assert linker.origins == {"owners_file", "usage"}


def test_origem_conhecida_de_perfil_inativo_continua_com_rotulo() -> None:
    load_profiles(["senior"])
    active = load_profiles(["default"])  # outro conjunto ativo no mesmo processo
    assert relations.origin_info("declared") is not None and "declared" not in active.owner_origins
    assert relations.origin_label("sibling") == "repo irmão"



def test_origem_repetida_entre_perfis_e_erro(monkeypatch: pytest.MonkeyPatch) -> None:
    class RepeteSenior(Acme):
        def origins(self) -> Sequence[OriginPlacement]:
            return (OriginPlacement("usage", (Origin("declared", "x", "y"), OWNERS_ORIGIN)),)

    use_entry_points(monkeypatch, acme=RepeteSenior, senior=SeniorProfile)
    with pytest.raises(ValueError, match="já registrada"):
        load_profiles(["senior", "acme"])
