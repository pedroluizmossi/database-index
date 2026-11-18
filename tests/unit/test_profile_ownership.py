"""Perfis de organização, etapa do dono no DDD: contrato `OwnershipRules`, origens registradas pelo perfil (ordem
global e ordem de força do dono), um perfil fictício com outra origem e o núcleo sem perfil (linhas antigas com
origem desconhecida). Dados sintéticos; o perfil senior tem os testes dele em tests/profiles/senior/."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any

import pytest

from code_index.domain import relations
from code_index.domain.naming import NameRules
from code_index.domain.ownership import (
    Assignment, Catalog, FactsByRepo, Owner, OwnByRepo, OwnershipRules, build_catalog, derived_owner_origins,
    infer, owner_origins, resolve,
)
from code_index.domain.relations import BELONGS_TO, Origin, OriginPlacement, Relation, repo_key
from code_index.indexing.enrich import collect_material
from code_index.indexing.linkers import OwnershipLinker, _check_origins
from code_index.profiles import registry
from code_index.profiles.base import BaseProfile
from code_index.profiles.registry import load_profiles

# A ordem global das origens do núcleo (sem perfil carregado).
NUCLEO = ("manual", "agent", "llm", "explicit", "source", "usage", "service", "card", "tag", "url", "mention")


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


# ------------------------------------------------------------------ perfil fictício: dono pelo arquivo OWNERS

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


# ------------------------------------------------------------------ ordem das origens


# ------------------------------------------------------------------ perfil fictício com outra origem


def test_perfil_ficticio_entre_manual_e_usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_entry_points(monkeypatch, acme=Acme)
    assert relations.origin_order() == NUCLEO  # sem perfil carregado: só o núcleo
    active = load_profiles(["acme"])
    assert active.owner_origins == ("manual", "owners_file", "usage")
    assert derived_owner_origins(active.ownership_rules) == {"owners_file", "usage"}
    assert relations.origin_order() == (*NUCLEO[:5], "owners_file", *NUCLEO[5:])
    load_profiles(["acme"])  # carregar de novo não duplica
    assert relations.origin_order() == (*NUCLEO[:5], "owners_file", *NUCLEO[5:])
    assert OwnershipLinker(active.naming, active.ownership_rules).origins == {"owners_file", "usage"}

    # ordem de força: manual > a do perfil > usage (ordem explícita dos perfis ativos)
    chain = [("manual", Owner("m")), ("owners_file", Owner("d", "x")), ("usage", Owner("u"))]
    for i, (origin, owner) in enumerate(chain):
        items = [Assignment("repo:r", o, name) for name, o in chain[i:]]
        got = resolve(items, active.owner_origins)
        assert got["repo:r"].origin == origin and got["repo:r"].owners == [owner]

    # coleta: o perfil grava as suas chaves no material, as do núcleo continuam
    (tmp_path / "OWNERS").write_text("loja/estoque\n")
    m = collect_material(tmp_path, language=None, symbols=[], extractors=active.fact_extractors,
                         ownership=active.ownership_rules)
    assert set(m["ownership"]) == {"artifacts", "dependencies", "owners_file"}
    own = {"o__deposito": {"owners_file": ["loja/estoque"]}, "o__lib": {"artifacts": ["lib-x"]},
           "o__app": {"owners_file": ["loja/estoque"], "dependencies": ["lib-x"]}}
    catalog = build_catalog(active.ownership_rules, {}, own)
    assert catalog.services[("loja", "estoque")].label == "rótulo estoque"
    final = resolve(infer(list(own), {}, own, rules=active.ownership_rules), active.owner_origins)
    assert (final["repo:o__deposito"].origin, final["repo:o__lib"].origin) == ("owners_file", "usage")
    assert final["repo:o__lib"].owners == [Owner("loja", "estoque")]


def test_registro_valida_origens_e_regras(monkeypatch: pytest.MonkeyPatch) -> None:
    class SemDeclarar(Acme):
        def origins(self) -> Sequence[OriginPlacement]:
            return ()

    class Gravada(Acme):
        def origins(self) -> Sequence[OriginPlacement]:
            return (OriginPlacement("usage", (Origin("owners_file", "x", "y", stored=True),)),)

    class ChaveDoNucleo(Acme):
        def ownership(self) -> Sequence[OwnershipRules]:
            rules = OwnersFileRules()
            rules.material_keys = ("dependencies",)
            return (rules,)

    class RepeteNucleo(Acme):
        def origins(self) -> Sequence[OriginPlacement]:
            return (OriginPlacement("usage", (Origin("card", "x", "y"), OWNERS_ORIGIN)),)

    class ReferenciaDesconhecida(Acme):
        def origins(self) -> Sequence[OriginPlacement]:
            return (OriginPlacement("nao_existe", (OWNERS_ORIGIN,)),)

    class RegraForaDoContrato(Acme):
        def ownership(self) -> Sequence[Any]:
            return (object(),)

    for cls, err, match in ((SemDeclarar, ValueError, "não declara"), (Gravada, ValueError, "gravada"),
                            (ChaveDoNucleo, ValueError, "reservadas"),
                            (ReferenciaDesconhecida, ValueError, "referência desconhecida"),
                            (RegraForaDoContrato, TypeError, "OwnershipRules")):
        use_entry_points(monkeypatch, acme=cls)
        with pytest.raises(err, match=match):
            load_profiles(["acme"])
    use_entry_points(monkeypatch, acme=RepeteNucleo)
    with pytest.raises(ValueError, match="já registrada"):
        load_profiles(["acme"])


def test_regra_nao_produz_origem_alheia() -> None:
    class Alheia(OwnersFileRules):
        def assign(self, *a: Any, **k: Any) -> list[Assignment]:
            return [Assignment("repo:x", Owner("d"), "declared")]

    with pytest.raises(ValueError, match="fora das suas"):
        infer(["x"], {}, {}, rules=(Alheia(),))


def test_linker_so_produz_origens_registradas() -> None:
    class Falso:
        name = "falso"
        origins = frozenset({"inventada"})

        async def run(self, conn: Any, settings: Any) -> list[Relation]:
            return []

    with pytest.raises(ValueError, match="inventada"):
        _check_origins(Falso(), [Relation("repo:a", "dom:b", BELONGS_TO, "inventada")])
    with pytest.raises(ValueError, match="usage"):
        _check_origins(Falso(), [Relation("repo:a", "dom:b", BELONGS_TO, "usage")])
    _check_origins(OwnershipLinker(), [Relation("repo:a", "dom:b", BELONGS_TO, "usage")])


# ------------------------------------------------------------------ sem perfil


def test_origem_conhecida_de_perfil_inativo_continua_com_rotulo(monkeypatch: pytest.MonkeyPatch) -> None:
    use_entry_points(monkeypatch, acme=Acme)
    load_profiles(["acme"])
    active = load_profiles(["default"])  # outro conjunto ativo no mesmo processo
    assert relations.origin_info("owners_file") is not None and "owners_file" not in active.owner_origins
    assert relations.origin_label("owners_file") == "arquivo OWNERS"


def test_sem_perfil_linhas_antigas_continuam_legiveis() -> None:
    active = load_profiles(["default"])
    assert active.owner_origins == ("manual", "usage") and active.ownership_rules == ()
    assert relations.origin_order() == NUCLEO
    # linha antiga de um perfil que saiu: a relação valida, a resolução a põe depois de usage
    old = Relation("repo:r", "svc:a/b", BELONGS_TO, "declared")
    assert not old.contestable and relations.origin_label("declared") == "declared"
    items = [Assignment("repo:r", Owner("a", "b"), "declared"), Assignment("repo:r", Owner("u"), "usage")]
    assert resolve(items, active.owner_origins)["repo:r"].origin == "usage"
    assert resolve(items[:1], active.owner_origins)["repo:r"].owners == [Owner("a", "b")]
    assert build_catalog(active.ownership_rules, {}, {}).domains == {}


