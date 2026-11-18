"""Perfis de organização na interface: exemplos do guia do MCP (`guide_examples`), contratos no formato genérico
do cartão (`contracts`), perfis ativos e o perfil de cada origem (dados sintéticos)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from code_index.domain.contracts import CONTRACTS_SECTION, contracts_payload
from code_index.domain.relations import Origin, OriginPlacement
from code_index.domain.repo_card import FactExtractor
from code_index.interfaces.http.mcp import build_mcp
from code_index.interfaces.http.mcp_guide import GUIDE, INSTRUCTIONS, with_examples
from code_index.profiles import registry
from code_index.profiles.base import ActiveProfiles, BaseProfile, GuideExamples, contract_formats
from code_index.profiles.registry import load_profiles
from code_index.services.base import ServiceBase, prepare_profiles
from code_index.services.enrich import EnrichMixin

PLACEHOLDER = re.compile(r"\{ex:([a-z_]+)\}")

EXAMPLES = GuideExamples(
    repo="acme-loja-api", repo_filter="loja", domain="comercio", service="carrinho",
    operation_key="op:comercio/carrinho/fecharPedido", api_operation="carrinho.fecharPedido",
    text_key="comercio.carrinho.erro", contract="IDL", catalog_source="do IDL", origins="manual, idl, tag",
    facts="catalog-info, serviços do .idl, pastas",
)


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


class IdlExtractor:
    """Contratos num formato fictício (`.idl`), com rótulo."""

    section = CONTRACTS_SECTION
    max_depth = 2
    label = ".idl"

    def matches(self, name: str) -> bool:
        return name.endswith(".idl")

    def extract(self, text: str, path: str) -> list[dict[str, Any]]:
        return []

    def card_lines(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        return []


class ProtoExtractor(IdlExtractor):
    """Outro formato de contrato, sem rótulo (vale a extensão)."""

    label = ""

    def matches(self, name: str) -> bool:
        return name.endswith(".proto")


class Acme(BaseProfile):
    name = "acme"
    label = "Acme"

    def origins(self) -> Sequence[OriginPlacement]:
        return (OriginPlacement(before="usage", origins=(Origin("idl", "IDL", "declarado no .idl"),)),)

    def fact_extractors(self) -> Sequence[FactExtractor]:
        return (IdlExtractor(), ProtoExtractor())

    def guide_examples(self) -> GuideExamples | None:
        return EXAMPLES


class Beta(BaseProfile):
    name = "beta"
    label = "Beta"

    def guide_examples(self) -> GuideExamples | None:
        return GuideExamples(repo="beta-repo")


class FakeEntryPoint:
    def __init__(self, name: str, obj: Any) -> None:
        self.name, self.value, self._obj = name, f"pacote_de_teste:{name}", obj

    def load(self) -> Any:
        return self._obj


def use_profiles(monkeypatch: pytest.MonkeyPatch, **objs: Any) -> None:
    monkeypatch.setattr(registry, "_entry_points", lambda: {n: FakeEntryPoint(n, o) for n, o in objs.items()})
    registry.clear_cache()


# ------------------------------------------------------------------ exemplos do guia

def test_marcadores_do_guia_tem_exemplo() -> None:
    used = set(PLACEHOLDER.findall(INSTRUCTIONS + GUIDE))
    assert used and used <= set(GuideExamples().as_dict())


def test_exemplos_padrao_sao_genericos() -> None:
    text = " ".join(GuideExamples().as_dict().values()).lower()
    for word in ("snr", "senior", "sdl", "platform"):
        assert word not in text
    rendered = with_examples(INSTRUCTIONS + GUIDE, GuideExamples().as_dict())
    assert "{ex:" not in rendered and "pedidos-backend" in rendered


def test_with_examples_troca_e_recusa_marcador_sem_valor() -> None:
    assert with_examples("repo {ex:repo} e {ex:repo}", {"repo": "x"}) == "repo x e x"
    assert with_examples('{"results": [...]} {ex:<campo>}', {}) == '{"results": [...]} {ex:<campo>}'
    with pytest.raises(KeyError, match="nada"):
        with_examples("{ex:nada}", {"repo": "x"})


def test_with_examples_quebra_so_a_linha_trocada_que_passa_da_largura() -> None:
    text = "    origem ({ex:origins}).\n    linha longa sem marcador " + "x" * 40
    out = with_examples(text, {"origins": "manual, regra, fonte, tag"}, width=30)
    assert out.split("\n") == ["    origem (manual, regra,", "    fonte, tag).", "    linha longa sem marcador " + "x" * 40]
    assert with_examples(text, {"origins": "a"}, width=30).split("\n")[0] == "    origem (a)."


def test_exemplos_do_primeiro_perfil_que_oferece(monkeypatch: pytest.MonkeyPatch) -> None:
    use_profiles(monkeypatch, acme=Acme, beta=Beta)
    assert ActiveProfiles().guide_examples == GuideExamples()
    assert load_profiles(["beta", "acme"]).guide_examples.repo == "beta-repo"
    assert load_profiles(["acme", "beta"]).guide_examples == EXAMPLES


def test_registro_recusa_exemplos_fora_do_contrato(monkeypatch: pytest.MonkeyPatch) -> None:
    class Ruim(BaseProfile):
        name = "ruim"

        def guide_examples(self) -> Any:
            return {"repo": "x"}

    use_profiles(monkeypatch, ruim=Ruim)
    with pytest.raises(TypeError, match="GuideExamples"):
        load_profiles(["ruim"])


async def test_mcp_usa_os_exemplos_do_perfil(monkeypatch: pytest.MonkeyPatch, make_settings) -> None:
    use_profiles(monkeypatch, acme=Acme)
    settings = make_settings(profiles=["acme"], auth_secret="x" * 40)
    assert prepare_profiles(settings).guide_examples == EXAMPLES
    server = build_mcp(settings, lambda: None, None)  # type: ignore[arg-type]
    # espaços normalizados: a linha com exemplo que passa da largura é quebrada (`with_examples(width=...)`)
    tools = {t.name: re.sub(r"\s+", " ", t.description or "") for t in await server.list_tools()}
    assert all("{ex:" not in d for d in tools.values())
    assert "'acme-loja-api'" in tools["related"] and "(manual, idl, tag...)" in tools["related"]
    assert '"comercio"' in tools["search_code"] and '"carrinho"' in tools["search_code"]
    assert '("comercio/carrinho")' in tools["list_repos"]
    assert "(do IDL e os definidos pela equipe)" in tools["list_domains"]
    assert "operações IDL" in tools["find_repos"] and "(catalog-info, serviços do .idl, pastas)" in tools["repo_card"]
    assert "{ex:" not in (server.instructions or "") and "operações IDL" in (server.instructions or "")
    guide = (await server.get_prompt("guia_de_uso")).messages[0].content
    assert "`acme-loja-api`, `op:comercio/carrinho/fecharPedido`" in getattr(guide, "text", "")


async def test_mcp_sem_perfil_sem_nomes_da_organizacao(make_settings) -> None:
    settings = make_settings(profiles=[], auth_secret="x" * 40)
    server = build_mcp(settings, lambda: None, None)  # type: ignore[arg-type]
    text = (server.instructions or "") + " ".join(t.description or "" for t in await server.list_tools())
    assert "{ex:" not in text and "pedidos-backend" in text
    for word in ("snr-", "senior", ".sdl", "SDL"):
        assert word not in text


# ------------------------------------------------------------------ contratos e perfis no cartão

SERVICES = [
    {"domain": "comercio", "service": "carrinho", "path": "api/carrinho.idl", "queries": ["listarItens"],
     "actions": ["fecharPedido"], "signals": [], "events": ["pedidoFechado"], "entities": [],
     "private": ["fecharPedido"]},
    {"domain": "comercio", "service": "frete", "path": "frete.proto"},
]


def test_contracts_payload_formato_generico() -> None:
    out = contracts_payload(SERVICES, lambda p: "<" + p + ">")
    assert out[0] == {"format": "<api/carrinho.idl>", "path": "api/carrinho.idl", "domain": "comercio",
                      "service": "carrinho",
                      "groups": {"query": ["listarItens"], "action": ["fecharPedido"], "event": ["pedidoFechado"]},
                      "private": ["fecharPedido"]}
    assert out[1]["groups"] == {} and out[1]["private"] == []
    assert list(out[0]["groups"]) == ["query", "action", "event"]


def test_formato_do_contrato_e_perfis_ativos(monkeypatch: pytest.MonkeyPatch) -> None:
    use_profiles(monkeypatch, acme=Acme, beta=Beta)
    active = load_profiles(["acme", "beta"])
    assert active.contract_format("api/carrinho.idl") == ".idl"
    assert active.contract_format("frete.proto") == ".proto"     # extrator sem rótulo: a extensão
    assert active.contract_format("docs/LEIAME") == ""
    assert ActiveProfiles().contract_format("x/y.idl") == ".idl"
    assert contract_formats(Acme().fact_extractors()) == (".idl",)
    assert active.describe() == [
        {"name": "acme", "label": "Acme", "origins": ["idl"], "contract_formats": [".idl"]},
        {"name": "beta", "label": "Beta", "origins": [], "contract_formats": []},
    ]


async def test_origens_trazem_o_perfil(monkeypatch: pytest.MonkeyPatch) -> None:
    use_profiles(monkeypatch, acme=Acme)
    load_profiles(["acme"])
    by_name = {o["name"]: o for o in await ServiceBase.relation_origins(None)}  # type: ignore[arg-type]
    assert by_name["idl"]["profile"] == "acme" and by_name["idl"]["label"] == "IDL"
    assert by_name["idl"]["order"] == by_name["usage"]["order"] - 1
    assert by_name["manual"]["profile"] is None and by_name["usage"]["profile"] is None


async def test_cartao_expoe_contracts_e_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    use_profiles(monkeypatch, acme=Acme)
    facts = {"sdl": SERVICES, "properties": []}

    class Enrich:
        async def get(self, name: str) -> dict[str, Any]:
            return {"commit": "abc", "material": {"facts": facts}, "card": None, "card_input_hash": None,
                    "input_hash": "h", "card_agent": None, "card_at": None, "facts_at": None}

    class Relations:
        async def impls_for_repo(self, name: str) -> list[Any]:
            return []

        async def related_repos(self, name: str) -> list[Any]:
            return []

    async def resolve(names: list[str]) -> list[str]:
        return names

    svc = SimpleNamespace(profiles=load_profiles(["acme"]), _enrich=Enrich(), _relations=Relations(),
                          _resolve_repos=resolve)
    info = await EnrichMixin.repo_card(svc, "acme-loja-api")  # type: ignore[arg-type]
    assert info is not None
    assert info["facts"] is facts                                       # compatibilidade: sdl/properties como antes
    assert [c["format"] for c in info["contracts"]] == [".idl", ".proto"]
    assert info["contracts"][0]["groups"]["action"] == ["fecharPedido"]
    assert info["profiles"] == [{"name": "acme", "label": "Acme", "origins": ["idl"], "contract_formats": [".idl"]}]
