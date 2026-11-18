"""Perfis de organização: operação → implementação (`impl_convention`) e linkers próprios (dados sintéticos)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from code_index.domain.api_impl import NO_IMPL_CONVENTION, ImplConvention, match_handlers
from code_index.domain.relations import Origin, OriginPlacement, Relation, origin_order
from code_index.indexing.linkers import all_linkers, core_linkers
from code_index.interfaces.http.mcp import build_mcp, impl_tool_description
from code_index.interfaces.http.mcp_guide import GUIDE, INSTRUCTIONS, investigation_prompt, without_tools
from code_index.profiles import registry
from code_index.profiles.base import ActiveProfiles, BaseProfile
from code_index.profiles.registry import load_profiles
from code_index.services.base import prepare_profiles
from code_index.services.enrich import EnrichMixin
from code_index.sources.base import Linker


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


def impl_names(operation: str) -> list[str]:
    return [f"{operation}Impl", f"{operation}Service"]


CONV = ImplConvention(names=impl_names, rule="convenção Acme (`<op>Impl`)", symbol="classe Impl",
                      example_service="pedidos", example_operation="listarPedidos")


class ImplLinker:
    name = "acme-impl"
    origins = frozenset({"acme_impl"})

    async def run(self, conn: Any, settings: Any) -> list[Relation]:
        return []


class Acme(BaseProfile):
    name = "acme"
    label = "Acme"

    def origins(self) -> Sequence[OriginPlacement]:
        return (OriginPlacement(before="card", origins=(Origin("acme_impl", "impl", "classe <op>Impl"),)),)

    def impl_convention(self) -> ImplConvention | None:
        return CONV

    def linkers(self) -> Sequence[Linker]:
        return (ImplLinker(),)


def use_profiles(monkeypatch: pytest.MonkeyPatch, **objs: Any) -> None:
    class EP:
        def __init__(self, name: str, obj: Any) -> None:
            self.name, self.value, self._obj = name, f"teste:{name}", obj

        def load(self) -> Any:
            return self._obj

    monkeypatch.setattr(registry, "_entry_points", lambda: {n: EP(n, o) for n, o in objs.items()})
    registry.clear_cache()


# ------------------------------------------------------------------ domain: convenção e casamento

def test_match_handlers_usa_a_convencao_recebida_sem_diferenciar_maiusculas() -> None:
    services = [{"domain": "loja", "service": "pedidos", "queries": ["listarPedidos", "semImpl"],
                 "actions": ["criarPedido"], "signals": ["avisar"]}]
    classes = [("LISTARPEDIDOSIMPL", "src/test/ListarPedidosImpl.java", 1),
               ("ListarPedidosImpl", "src/main/ListarPedidosImpl.java", 4),
               ("CriarPedidoService", "src/main/CriarPedidoService.java", 2),
               ("AvisarHandler", "src/main/AvisarHandler.java", 3)]
    out = match_handlers(services, classes, impl_names)
    assert [(r["operation"], r["kind"], r["symbol"], r["path"]) for r in out] == [
        ("listarPedidos", "query", "ListarPedidosImpl", "src/main/ListarPedidosImpl.java"),
        ("criarPedido", "action", "CriarPedidoService", "src/main/CriarPedidoService.java"),
    ]
    assert match_handlers(services, classes, lambda op: []) == []


def test_combinacao_de_convencoes() -> None:
    assert ImplConvention.combine([]) is None
    assert ImplConvention.combine([CONV]) is CONV
    other = ImplConvention(names=lambda op: [f"{op}impl", f"{op}Handler"], rule="outra")
    both = ImplConvention.combine([CONV, other])
    assert both is not None and both.rule == CONV.rule and both.symbol == CONV.symbol
    assert both.names("x") == ["ximpl", "xservice", "xhandler"]  # na ordem, sem repetir


# ------------------------------------------------------------------ perfis: default e sintético

def test_default_nao_oferece_implementacoes_nem_linkers() -> None:
    active = load_profiles(["default"])
    assert active.impl_convention is None and active.linkers == ()
    assert BaseProfile().impl_convention() is None and tuple(BaseProfile().linkers()) == ()


def test_perfil_sintetico_traz_convencao_e_linker(monkeypatch: pytest.MonkeyPatch) -> None:
    use_profiles(monkeypatch, acme=Acme)
    active = load_profiles(["acme"])
    assert active.impl_convention is CONV
    assert [lk.name for lk in active.linkers] == ["acme-impl"]
    assert origin_order().index("acme_impl") == origin_order().index("card") - 1


def test_linker_com_origem_nao_declarada_falha_na_carga(monkeypatch: pytest.MonkeyPatch) -> None:
    class Sem(Acme):
        name = "sem"

        def origins(self) -> Sequence[OriginPlacement]:
            return ()

    class Repetido(Acme):
        name = "repetido"

        def linkers(self) -> Sequence[Linker]:
            return (ImplLinker(), ImplLinker())

    class Torto(Acme):
        name = "torto"

        def linkers(self) -> Sequence[Linker]:
            return (object(),)  # type: ignore[return-value]

    class ConvencaoErrada(Acme):
        name = "conv"

        def impl_convention(self) -> Any:
            return impl_names

    use_profiles(monkeypatch, sem=Sem, repetido=Repetido, torto=Torto, conv=ConvencaoErrada)
    with pytest.raises(ValueError, match="origens que o perfil não declara"):
        load_profiles(["sem"])
    with pytest.raises(ValueError, match="linker repetido"):
        load_profiles(["repetido"])
    with pytest.raises(TypeError, match="contrato Linker"):
        load_profiles(["torto"])
    with pytest.raises(TypeError, match="ImplConvention"):
        load_profiles(["conv"])


def test_linker_com_mesmo_nome_em_dois_perfis(monkeypatch: pytest.MonkeyPatch) -> None:
    class Outro(BaseProfile):
        name = "outro"

        def origins(self) -> Sequence[OriginPlacement]:
            return (OriginPlacement(before="card", origins=(Origin("outro_impl", "x", "x"),)),)

        def linkers(self) -> Sequence[Linker]:
            lk = ImplLinker()
            lk.origins = frozenset({"outro_impl"})
            return (lk,)

    use_profiles(monkeypatch, acme=Acme, outro=Outro)
    with pytest.raises(ValueError, match="já usado por outro perfil"):
        _ = load_profiles(["acme", "outro"]).linkers


def test_all_linkers_soma_nucleo_fontes_e_perfis(monkeypatch: pytest.MonkeyPatch) -> None:
    use_profiles(monkeypatch, acme=Acme)
    core = [lk.name for lk in core_linkers()]
    assert "handler" not in core
    names = [lk.name for lk in all_linkers([], load_profiles(["acme"]))]
    assert names == [*core, "acme-impl"]
    assert [lk.name for lk in all_linkers([], ActiveProfiles())] == core

    class Colide(Acme):
        name = "colide"

        def linkers(self) -> Sequence[Linker]:
            lk = ImplLinker()
            lk.name = "card"
            return (lk,)

    use_profiles(monkeypatch, colide=Colide)
    with pytest.raises(ValueError, match="já usado pelo núcleo"):
        all_linkers([], load_profiles(["colide"]))


# ------------------------------------------------------------------ serviço e interfaces

async def test_find_api_impl_sem_convencao_responde_vazio() -> None:
    class Relations:
        calls: list[str] = []

        async def find_impls(self, op: str, **kw: Any) -> list[dict[str, Any]]:
            self.calls.append(op)
            return [{"operation": op}]

    rel = Relations()
    sem = SimpleNamespace(profiles=ActiveProfiles(), _relations=rel)
    assert await EnrichMixin.find_api_impl(sem, "pedidos.listarPedidos") == []  # type: ignore[arg-type]
    com = SimpleNamespace(profiles=SimpleNamespace(impl_convention=CONV), _relations=rel)
    assert await EnrichMixin.find_api_impl(com, "pedidos.listarPedidos") == [{"operation": "listarPedidos"}]  # type: ignore[arg-type]
    assert NO_IMPL_CONVENTION.startswith("nenhum perfil")


def test_descricao_da_tool_vem_da_convencao() -> None:
    text = impl_tool_description(CONV)
    assert text.startswith("Onde uma operação da API é implementada: repo, classe Impl, arquivo e linha.\n")
    assert "Casamento pela convenção Acme (`<op>Impl`), sem LLM." in text
    assert "'pedidos.listarPedidos' ou 'POST /pedidos/queries/listarPedidos'" in text
    assert "(ex.: 'pedidos')" in text


def test_guia_sem_tools_nao_registradas() -> None:
    assert without_tools(INSTRUCTIONS, ()) == INSTRUCTIONS
    short = without_tools(INSTRUCTIONS, ("find_api_impl",))
    assert "find_api_impl" not in short and "find_repos" in short
    guide = without_tools(GUIDE, ("find_api_impl",))
    assert "find_api_impl" not in guide and "`find_repos`" in guide
    assert investigation_prompt("x", guide).startswith(guide)


@pytest.mark.parametrize(("profiles", "registered"), [("", False), ("default", False), ("acme", True)])
async def test_mcp_registra_find_api_impl_so_com_convencao(make_settings, monkeypatch: pytest.MonkeyPatch,
                                                           profiles: str, registered: bool) -> None:
    use_profiles(monkeypatch, acme=Acme)
    settings = make_settings(profiles=[p for p in profiles.split(",") if p], auth_secret="x" * 40)
    assert (prepare_profiles(settings).impl_convention is not None) is registered
    server = build_mcp(settings, lambda: None, None)  # type: ignore[arg-type]
    tools = {t.name for t in await server.list_tools()}
    assert ("find_api_impl" in tools) is registered
    assert "search_code" in tools
