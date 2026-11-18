"""Dono do código no DDD, núcleo: resolução por origem, uso de lib, escopo da busca e nome-base. O contrato das regras
de dono e a ordem com perfis (sintéticos) ficam em test_profile_ownership.py; as regras do perfil senior, em
tests/profiles/senior/. Dados sintéticos."""

from __future__ import annotations

import pytest

from code_index.domain.doc_links import manifest_dependencies
from code_index.domain.naming import NameRules
from code_index.domain import ownership as core
from code_index.domain.ownership import (
    Assignment, Owner, base_name, coverage, entity_key, owners_of, parse_owner, scope_for, split_entity, usage_owners,
)
from code_index.domain.relations import Relation, BELONGS_TO
from code_index.domain.repo_card import material_hash

# Ordem de força sintética (os nomes das origens são só texto aqui; sem depender do registro global).
ORDER = ("manual", "declared", "sibling", "api_calls", "usage")


def resolve(items):  # type: ignore[no-untyped-def]
    return core.resolve(items, ORDER)


ORG = "acme__"
# Regras de nome de um perfil (sintéticas): prefixo curto de até 3 letras e sufixos de papel.
RULES = NameRules(short_prefix_max=3, role_suffixes=frozenset({"frontend", "backend", "service", "web", "ui", "library"}))


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
    Relation("repo:r", "svc:hcm/payroll", BELONGS_TO, "declared")  # tipo válido e origem bem formada


@pytest.mark.parametrize("repo,base", [
    (f"{ORG}xyz-audit-frontend", "audit"),
    (f"{ORG}xyz-audit-backend", "audit"),
    (f"{ORG}xyz-audit-authorization-frontend", "audit_authorization"),
    (f"{ORG}xyz-blob-service", "blob"),
    ("projeto-local", "projeto_local"),
])
def test_nome_base(repo: str, base: str) -> None:
    assert base_name(repo, RULES) == base


def test_nome_base_sem_regras_de_perfil() -> None:
    assert base_name(f"{ORG}xyz-audit-frontend") == "xyz_audit_frontend"
    assert base_name("projeto-local") == "projeto_local"


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


def test_escopo_segue_a_pasta_mais_especifica() -> None:
    # repo de A; apps de B; apps/billing volta para A: o escopo concorda com owners_of em cada nível.
    r = resolve([
        Assignment("repo:r", Owner("a"), "manual"),
        Assignment("dir:r/apps", Owner("b"), "manual"),
        Assignment("dir:r/apps/billing", Owner("a"), "manual"),
    ])
    a, b = scope_for(r, "a"), scope_for(r, "b")
    for path in ("x.py", "apps/y.py", "apps/billing/z.py", "apps/billingx/w.py"):
        expected = owners_of(r, "r", path)[0].domain
        assert a.contains("r", path) == (expected == "a"), path
        assert b.contains("r", path) == (expected == "b"), path
    assert a.clauses() == [("r", None, ["apps"]), ("r", "apps/billing", [])]
    assert b.clauses() == [("r", "apps", ["apps/billing"])]


def test_cobertura() -> None:
    r = resolve([Assignment("repo:a", Owner("x"), "manual"), Assignment("repo:b", Owner("x", "y"), "declared")])
    assert coverage(["a", "b", "c", "d"], r, ORDER) == {
        "repos": 4, "owned": 2, "percent": 50, "by_origin": {"manual": 1, "declared": 1, "none": 2}}


def test_dependencias_dos_manifestos() -> None:
    pom = ("<project xmlns='http://maven.apache.org/POM/4.0.0'><artifactId>me</artifactId><dependencies>"
           "<dependency><artifactId>audit-library</artifactId></dependency>"
           "<dependency><artifactId>${x}</artifactId></dependency></dependencies></project>")
    assert manifest_dependencies("pom.xml", pom) == ["audit-library"]
    assert manifest_dependencies("package.json", '{"dependencies": {"@acme/ui": "1"}}') == ["@acme/ui", "ui"]
    assert manifest_dependencies("pom.xml", "<quebrado") == []


def test_secao_ownership_nao_muda_o_hash_do_cartao() -> None:
    base = {"facts": {"language": "java"}, "readme": "x"}
    assert material_hash(base) == material_hash({**base, "ownership": {"api_refs": {"a/b": 3}}})


