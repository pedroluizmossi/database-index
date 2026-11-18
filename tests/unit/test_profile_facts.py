"""Perfis de organização, etapa dos tipos de arquivo e dos fatos: `FileTypes`, include padrão, contratos genéricos,
seções de fatos plugáveis (`FactExtractor`), coleta do material e o perfil senior com os valores de antes.
Dados sintéticos."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from code_index.domain.contracts import ContractService, Operation, operations
from code_index.domain.file_patterns import DEFAULT_INCLUDE, default_include
from code_index.domain.file_types import NO_FILE_TYPES, DeclRule, FileType, FileTypes
from code_index.domain.models import DocSource, Repo
from code_index.domain.repo_card import FactExtractor, RepoFacts, build_facts, render_card_task
from code_index.indexing.content import file_language, redact
from code_index.indexing.context import chunk_header, declarations
from code_index.indexing.enrich import collect_material
from code_index.profiles import registry
from code_index.profiles.base import ActiveProfiles, BaseProfile
from code_index.profiles.registry import load_profiles


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


# ------------------------------------------------------------------ perfil sintético

_IDL_DECL = re.compile(r"^[ \t]*(api|call)\s+(\w+)", re.MULTILINE)
IDL = FileType(language="acmeidl", extensions=("aidl",), globs=("**/*.aidl",), config=True,
               declarations=DeclRule(_IDL_DECL, containers=frozenset({"api"})))
CFG = FileType(language="acmecfg", extensions=("acfg", "acfg2"), globs=("**/*.acfg", "**/*.acfg2"))


class IdlExtractor:
    """`*.aidl`: `api <serviço>` + `call <operação>` → serviço de contrato (seção `sdl`)."""

    section = "sdl"
    max_depth = 1

    def matches(self, name: str) -> bool:
        return name.endswith(".aidl")

    def extract(self, text: str, path: str) -> list[dict[str, Any]]:
        api = re.search(r"^api\s+(\w+)", text, re.M)
        if not api:
            return []
        ops = [Operation("acme", api.group(1), "query", m) for m in re.findall(r"call\s+(\w+)", text)]
        return [ContractService.from_operations("acme", api.group(1), path, ops).to_dict()]

    def card_lines(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        return [f"  contrato {s['service']}: " + ", ".join(s["queries"]) for s in items]


class FlagsExtractor:
    """`flags.txt` → seção própria `flags` (fora das que o núcleo conhece)."""

    section = "flags"
    max_depth = 0

    def matches(self, name: str) -> bool:
        return name == "flags.txt"

    def extract(self, text: str, path: str) -> list[dict[str, Any]]:
        return [{"path": path, "flags": text.split()}]

    def card_lines(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        return ["  flags: " + ", ".join(f for it in items for f in it["flags"])]


class Acme(BaseProfile):
    name = "acme"
    label = "Acme"

    def file_types(self) -> Sequence[FileType]:
        return (IDL, CFG)

    def fact_extractors(self) -> Sequence[FactExtractor]:
        return (IdlExtractor(), FlagsExtractor())


class Other(BaseProfile):
    name = "other"
    label = "Outro"

    def file_types(self) -> Sequence[FileType]:
        return (FileType(language="outro", extensions=("aidl", "oth"), globs=("**/*.aidl", "**/*.oth")),)


def use_entry_points(monkeypatch: pytest.MonkeyPatch, **objs: Any) -> None:
    class EP:
        def __init__(self, name: str, obj: Any) -> None:
            self.name, self.value, self._obj = name, f"pacote_de_teste:{name}", obj

        def load(self) -> Any:
            return self._obj

    monkeypatch.setattr(registry, "_entry_points", lambda: {n: EP(n, o) for n, o in objs.items()})
    registry.clear_cache()


# ------------------------------------------------------------------ tipos de arquivo

def test_file_types_combinados_primeiro_perfil_vence() -> None:
    types = ActiveProfiles((Acme(), Other())).file_types
    assert types.language("x/Contrato.AIDL") == "acmeidl"     # extensão sem diferenciar maiúsculas
    assert types.language("a.oth") == "outro" and types.language("a.acfg2") == "acmecfg"
    assert types.language("semextensao") == "" and types.for_extension("aidl") is IDL
    assert types.config_languages == {"acmeidl"}
    assert types.globs == ("**/*.aidl", "**/*.acfg", "**/*.acfg2", "**/*.oth")
    assert NO_FILE_TYPES.language("a.aidl") == "" and NO_FILE_TYPES.globs == ()


def test_file_type_invalido() -> None:
    with pytest.raises(ValueError):
        FileType(language="x", extensions=(".x",), globs=())
    with pytest.raises(ValueError):
        FileType(language="", extensions=("x",), globs=())
    with pytest.raises(ValueError):
        FileType(language="x", extensions=("X",), globs=())


def test_linguagem_mascara_e_declaracoes_pelo_tipo_do_perfil() -> None:
    types = ActiveProfiles((Acme(),)).file_types
    assert file_language("svc.aidl", types) == "acmeidl" and file_language("svc.aidl") == ""
    assert file_language("App.java", types) == "java"  # o detector do CocoIndex vem primeiro
    text = "api pedidos\n  call listar\n  call criar\ntoken: abcdef123456\n"
    assert redact(text, "acmeidl", types)[0].endswith("token: ***\n")
    assert redact(text, "acmecfg", types)[0].endswith("token: abcdef123456\n")  # não é config
    decls = declarations(text, "acmeidl", "svc.aidl", types)
    assert [(d.line, d.kind, d.name) for d in decls] == [
        (1, "container", "api pedidos"), (2, "member", "call listar"), (3, "member", "call criar")]
    assert chunk_header("o__r", "svc.aidl", "acmeidl", decls, 3, 3) == \
        "# o__r/svc.aidl (acmeidl)\n# em: api pedidos · declara: call criar"
    assert declarations(text, "", "svc.acfg", types) == []  # tipo sem regra de declarações


# ------------------------------------------------------------------ include padrão

def test_include_padrao_com_os_globs_do_perfil_em_posicao_fixa() -> None:
    inc = list(ActiveProfiles((Acme(),)).default_include)
    assert inc == default_include(["**/*.aidl", "**/*.acfg", "**/*.acfg2"])
    at = DEFAULT_INCLUDE.index("**/*.properties")
    assert inc[at:at + 3] == ["**/*.aidl", "**/*.acfg", "**/*.acfg2"] and inc[at + 3] == "**/*.properties"
    assert [g for g in inc if g not in ("**/*.aidl", "**/*.acfg", "**/*.acfg2")] == DEFAULT_INCLUDE
    assert default_include(["**/*.java", "**/*.x", "**/*.x"]) == default_include(["**/*.x"])  # sem repetir
    assert ActiveProfiles().default_include == tuple(DEFAULT_INCLUDE)


def test_repo_recebe_o_include_padrao_dos_perfis() -> None:
    inc = ActiveProfiles((Acme(),)).default_include
    assert Repo(name="r", url="u").with_default_include(inc).include == inc
    proprio = Repo(name="r", url="u", include=("**/*.py",))
    assert proprio.with_default_include(inc) is proprio
    docs = DocSource(name="w", url="https://wiki.invalid").to_repo()
    assert docs.with_default_include(inc).include == ("**/*.md",)


# ------------------------------------------------------------------ contratos

def test_contrato_generico_ida_e_volta() -> None:
    ops = [Operation("d", "s", "query", "listar"), Operation("d", "s", "action", "criar", private=True),
           Operation("d", "s", "query", "listar"), Operation("d", "s", "event", "criado", private=True),
           Operation("d", "s", "entity", "pedido")]
    svc = ContractService.from_operations("d", "s", "c/s.idl", ops)
    assert svc.to_dict() == {"domain": "d", "service": "s", "path": "c/s.idl", "queries": ["listar"],
                             "actions": ["criar"], "signals": [], "events": ["criado"], "entities": ["pedido"],
                             "private": ["criar"]}  # evento não é operação privada
    assert ContractService.from_dict(svc.to_dict()) == svc
    got = operations([svc.to_dict()])
    assert [(o.group, o.name, o.private, o.path) for o in got] == [
        ("query", "listar", False, "c/s.idl"), ("action", "criar", True, "c/s.idl"),
        ("event", "criado", False, "c/s.idl"), ("entity", "pedido", False, "c/s.idl")]
    assert list(ContractService.from_dict({"service": "x"}).operations()) == []


# ------------------------------------------------------------------ fatos

def test_repo_facts_mantem_sdl_e_properties_e_achata_secoes_novas() -> None:
    facts = RepoFacts(language="go")
    assert facts.to_dict() == {"language": "go", "catalog": {}, "artifacts": [], "sdl": [], "properties": [],
                               "modules": [], "has": []}
    facts.section("flags").append({"a": 1})
    assert facts.section("sdl") is facts.sdl and facts.section("properties") is facts.properties
    assert facts.to_dict()["flags"] == [{"a": 1}] and "extra" not in facts.to_dict()
    with pytest.raises(ValueError, match="reservada"):
        facts.section("modules")


def test_build_facts_e_texto_do_cartao_pelos_extratores() -> None:
    files = {"api/pedidos.aidl": 'api pedidos "Pedidos"\ncall listar\ncall criar\n', "flags.txt": "beta rapido",
             "package.json": '{"name": "@acme/pedidos"}'}
    extractors = ActiveProfiles((Acme(),)).fact_extractors
    facts = build_facts(files, list(files), "typescript", extractors).to_dict()
    assert facts["sdl"] == [{"domain": "acme", "service": "pedidos", "path": "api/pedidos.aidl",
                             "queries": ["listar", "criar"], "actions": [], "signals": [], "events": [],
                             "entities": [], "private": []}]
    assert facts["flags"] == [{"path": "flags.txt", "flags": ["beta", "rapido"]}]
    assert "@acme/pedidos" in facts["artifacts"]
    # sem extratores: nenhum arquivo de interesse do perfil vira fato
    plain = build_facts(files, list(files), "typescript").to_dict()
    assert plain["sdl"] == [] and "flags" not in plain
    material = {"facts": facts, "readme": "", "symbols": [], "suggested_files": []}
    text = render_card_task(task_id="t", repo="o__r", commit=None, remaining=0, material=material, candidates=[],
                            extractors=extractors)
    assert text.index("  contrato pedidos: listar, criar") < text.index("  flags: beta, rapido") \
        < text.index("README (sem badges)")
    bare = render_card_task(task_id="t", repo="o__r", commit=None, remaining=0, material=material, candidates=[])
    assert "contrato pedidos" not in bare


def test_coleta_do_material_com_os_extratores(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "pedidos.aidl").write_text('api pedidos "Pedidos"\ncall listar\n')
    (tmp_path / "api" / "fundo").mkdir()
    (tmp_path / "api" / "fundo" / "longe.aidl").write_text("api longe\ncall x\n")  # passa de max_depth
    (tmp_path / "flags.txt").write_text("a b")
    symbols = [("api/pedidos.aidl", "api pedidos", 1, "container"), ("src/Main.java", "Main", 3, "container")]
    extractors = ActiveProfiles((Acme(),)).fact_extractors
    m = collect_material(tmp_path, language="java", symbols=symbols, extractors=extractors)
    assert [s["service"] for s in m["facts"]["sdl"]] == ["pedidos"]
    assert m["facts"]["flags"] == [{"path": "flags.txt", "flags": ["a", "b"]}]
    assert m["symbols"] == ["Main"]  # declarações do arquivo do extrator não são classes do código
    assert set(m["ownership"]) == {"artifacts", "dependencies"}  # sem regras de dono: só as chaves do núcleo
    plain = collect_material(tmp_path, language="java", symbols=symbols)
    assert plain["facts"]["sdl"] == [] and "api pedidos" in plain["symbols"]
    assert set(plain["ownership"]) == {"artifacts", "dependencies"}


# ------------------------------------------------------------------ registro

def test_registro_valida_os_extratores(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reservada(FlagsExtractor):
        section = "has"

    class ComReservada(Acme):
        def fact_extractors(self) -> Sequence[FactExtractor]:
            return (Reservada(),)

    class ComObjeto(Acme):
        def fact_extractors(self) -> Sequence[Any]:
            return (object(),)

    use_entry_points(monkeypatch, acme=ComReservada)
    with pytest.raises(ValueError, match="reservada"):
        load_profiles(["acme"])
    use_entry_points(monkeypatch, acme=ComObjeto)
    with pytest.raises(TypeError, match="FactExtractor"):
        load_profiles(["acme"])
    use_entry_points(monkeypatch, acme=Acme)
    assert [x.section for x in load_profiles(["acme"]).fact_extractors] == ["sdl", "flags"]


# ------------------------------------------------------------------ default e senior

def test_default_sem_tipos_nem_extratores() -> None:
    active = load_profiles(["default"])
    assert active.file_types == FileTypes() and active.fact_extractors == ()
    assert active.default_include == tuple(DEFAULT_INCLUDE)
    assert "**/*.sdl" not in DEFAULT_INCLUDE and "**/*.pdl" not in DEFAULT_INCLUDE


