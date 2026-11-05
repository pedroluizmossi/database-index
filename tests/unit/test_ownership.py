"""Dono do código no DDD: catálogo do SDL, inferência sem LLM, resolução por origem e escopo da busca."""

from __future__ import annotations

import pytest

from code_index.domain.doc_links import manifest_dependencies
from code_index.domain.ownership import (
    Assignment, Owner, api_call_owners, api_refs, base_name, build_catalog, coverage, declared, entity_key,
    owners_of, parse_owner, repo_owners, resolve, scope_for, siblings, split_entity, usage_owners,
)
from code_index.domain.relations import Relation, BELONGS_TO
from code_index.domain.repo_card import material_hash, parse_sdl_labels

ORG = "senior-sistemas__"
SDL = {
    f"{ORG}snr-audit-backend": [{"domain": "platform", "service": "audit", "path": "audit.sdl"}],
    f"{ORG}snr-authentication-backend": [{"domain": "platform", "service": "authentication", "path": "a.sdl"}],
    f"{ORG}snr-payroll": [{"domain": "hcm", "service": "payroll", "path": "payroll.sdl"}],
}


def catalog():
    return build_catalog(SDL, {f"{ORG}snr-audit-backend": {"domains": {"platform": "Plataforma"},
                                                           "services": {"audit": "Auditoria"}}})


def test_nomes_e_chaves() -> None:
    assert parse_owner("Platform/Blob-Service") == Owner("platform", "blob_service")
    assert parse_owner("hcm") == Owner("hcm")
    assert Owner("hcm", "payroll").key == "svc:hcm/payroll" and Owner("hcm").key == "dom:hcm"
    with pytest.raises(ValueError):
        parse_owner("../x")
    assert entity_key("r") == "repo:r" and entity_key("r", "/mod/a/") == "dir:r/mod/a"
    assert split_entity("dir:r/mod/a") == ("r", "mod/a") and split_entity("repo:r") == ("r", None)
    with pytest.raises(ValueError):
        entity_key("r", "../fora")
    Relation("repo:r", "svc:hcm/payroll", BELONGS_TO, "declared")  # tipo e origem registrados


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


@pytest.mark.parametrize("repo,base", [
    (f"{ORG}snr-audit-frontend", "audit"),
    (f"{ORG}snr-audit-backend", "audit"),
    (f"{ORG}snr-audit-authorization-frontend", "audit_authorization"),
    (f"{ORG}snr-blob-service", "blob"),
    ("projeto-local", "projeto_local"),
])
def test_nome_base(repo: str, base: str) -> None:
    assert base_name(repo) == base


def test_irmaos_herdam_e_nome_igual_ao_servico() -> None:
    decl = repo_owners(resolve(declared(SDL)))
    repos = [*SDL, f"{ORG}snr-audit-frontend", f"{ORG}snr-payroll-web", f"{ORG}hcm-payroll-ui", f"{ORG}snr-misc"]
    got = {a.entity: (a.owner.label, a.confidence) for a in siblings(repos, decl, catalog())}
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


def test_lib_usada_por_um_dominio() -> None:
    owners = {"a": [Owner("platform", "audit")], "b": [Owner("platform", "audit")], "c": [Owner("hcm", "payroll")],
              "d": [Owner("platform", "authentication")]}
    artifacts = {"audit-lib": ["audit-library"], "util": ["commons-x"], "plat-lib": ["plat-lib"]}
    deps = {"a": ["audit-library", "commons-x"], "b": ["audit-library"], "c": ["commons-x"],
            "d": ["plat-lib"], "a2": ["plat-lib"]}
    owners["a2"] = [Owner("platform", "audit")]
    got = {a.entity: a for a in usage_owners(deps, artifacts, owners)}
    assert got["repo:audit-lib"].owner == Owner("platform", "audit") and got["repo:audit-lib"].confidence == "alta"
    assert got["repo:plat-lib"].owner == Owner("platform")  # serviços diferentes do mesmo domínio
    assert "repo:util" not in got                            # dois domínios: sem dono


def test_resolucao_manual_vence_e_guarda_alternativas() -> None:
    items = [
        Assignment("repo:x", Owner("platform", "audit"), "sibling", "alta"),
        Assignment("repo:x", Owner("platform", "authentication"), "api_calls", "media"),
        Assignment("repo:y", Owner("hcm", "payroll"), "api_calls", "media"),
        Assignment("repo:y", Owner("hcm"), "manual"),
    ]
    r = resolve(items)
    assert r["repo:x"].owners == [Owner("platform", "audit")] and r["repo:x"].origin == "sibling"
    assert [a.owner for a in r["repo:x"].weaker] == [Owner("platform", "authentication")]
    assert r["repo:y"].owners == [Owner("hcm")] and r["repo:y"].origin == "manual"
    assert r["repo:x"].to_dict()["alternatives"] == [
        {"owner": "platform/authentication", "origin": "api_calls", "confidence": "media"}]


def test_escopo_com_pastas_de_monorepo() -> None:
    items = [
        Assignment("repo:auth", Owner("platform", "authentication"), "declared"),
        Assignment("repo:erp", Owner("hcm", "payroll"), "declared"),
        Assignment("repo:erp", Owner("hcm", "vacation"), "declared"),
        Assignment("dir:erp/payroll", Owner("hcm", "payroll"), "declared"),
        Assignment("dir:erp/vacation", Owner("hcm", "vacation"), "declared"),
        Assignment("repo:legado", Owner("fin"), "manual"),
        Assignment("dir:legado/folha", Owner("hcm", "payroll"), "manual"),
    ]
    r = resolve(items)
    payroll = scope_for(r, "hcm", "payroll")
    assert payroll.whole == {"erp"} and payroll.exclude == {"erp": ["vacation"]}
    assert payroll.include == {"legado": ["folha"]}
    assert payroll.contains("erp", "payroll/A.java") and payroll.contains("erp", "shared/U.java")
    assert not payroll.contains("erp", "vacation/B.java") and payroll.contains("legado", "folha/x.prg")
    assert not payroll.contains("legado", "contas/y.prg") and not payroll.contains("auth", "a.java")
    assert scope_for(r, "hcm").repos == {"erp", "legado"}
    assert scope_for(r, "nada").empty
    assert owners_of(r, "legado", "folha/sub/x.prg") == [Owner("hcm", "payroll")]
    assert owners_of(r, "legado", "contas/y.prg") == [Owner("fin")]
    assert owners_of(r, "sem-dono", "a") == []


def test_cobertura() -> None:
    r = resolve([Assignment("repo:a", Owner("x"), "manual"), Assignment("repo:b", Owner("x", "y"), "declared")])
    assert coverage(["a", "b", "c", "d"], r) == {
        "repos": 4, "owned": 2, "percent": 50, "by_origin": {"manual": 1, "declared": 1, "none": 2}}


def test_dependencias_dos_manifestos() -> None:
    pom = ("<project xmlns='http://maven.apache.org/POM/4.0.0'><artifactId>me</artifactId><dependencies>"
           "<dependency><artifactId>audit-library</artifactId></dependency>"
           "<dependency><artifactId>${x}</artifactId></dependency></dependencies></project>")
    assert manifest_dependencies("pom.xml", pom) == ["audit-library"]
    assert manifest_dependencies("package.json", '{"dependencies": {"@senior/ui": "1"}}') == ["@senior/ui", "ui"]
    assert manifest_dependencies("pom.xml", "<quebrado") == []


def test_secao_ownership_nao_muda_o_hash_do_cartao() -> None:
    base = {"facts": {"language": "java"}, "readme": "x"}
    assert material_hash(base) == material_hash({**base, "ownership": {"api_refs": {"a/b": 3}}})


def test_inferencia_completa_na_ordem() -> None:
    from code_index.domain.ownership import infer

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
    got = {(a.entity.split("__")[-1], a.owner.label, a.origin) for a in infer(repos, SDL, own, manual)}
    assert ("snr-audit-backend", "platform/audit", "declared") in got
    assert ("snr-audit-frontend", "platform/audit", "sibling") in got
    assert ("snr-portal", "platform/authentication", "api_calls") in got
    assert ("snr-audit-library", "platform/audit", "sibling") in got  # nome-base 'audit' já basta
    assert ("snr-trilha", "platform/audit", "usage") in got  # consumidores: um declarado, um irmão
    assert not [g for g in got if g[0] == "snr-audit-library" and g[2] == "usage"]  # irmão já é dono
    assert ("snr-fixo", "platform/audit", "api_calls") in got  # inferido mesmo com dono manual...
    final = resolve([*infer(repos, SDL, own, manual), *manual])
    assert final[f"repo:{ORG}snr-fixo"].owners == [Owner("hcm")]  # ...mas o manual vence na resolução
