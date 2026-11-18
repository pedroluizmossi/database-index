"""Contrato do perfil de organização (no mesmo espírito de sources/base.py).

Um perfil reúne as convenções de uma empresa que o núcleo não deve conhecer: regras de nome dos repos, ruído
próprio a excluir da indexação e padrões de configuração (ex.: o bucket do catálogo OpenAPI). O núcleo funciona
sem nenhum perfil (`default`: comportamento genérico); os perfis ativos vêm de `CODE_INDEX_PROFILES`
(`Settings.profiles`) e são carregados pelo registro (`profiles/registry.py`: embutidos e o grupo de entry
points `code_index.profiles`).

Perfil novo = uma subclasse de `BaseProfile` (sobrescreve só o que precisa) registrada no registro ou exposta
num entry point. Os valores que o `domain` usa são dados puros (`domain.naming.NameRules`, listas de globs): o
domain continua sem importar nada daqui.

Ganchos: `naming`, `noise_exclude`, `settings_defaults`, `file_types` (formatos próprios: globs, linguagem,
máscara de configuração e declarações), `fact_extractors` (arquivos de interesse → seções dos fatos do cartão e
as linhas delas no texto da tarefa), `origins` (origens de relação próprias e a posição delas na ordem global de
`domain/relations`, registradas na carga), `ownership` (dono no DDD: catálogo, inferências e a coleta delas,
`domain/ownership.OwnershipRules`), `impl_convention` (operação de contrato → classe que a implementa,
`domain/api_impl.ImplConvention`; sem nenhuma, `find_api_impl` fica de fora), `contract_routes` (rotas HTTP das
operações de contrato: a fonte OpenAPI liga página → operação), `linkers` (relações próprias,
somadas às do núcleo e das fontes) e `guide_examples` (exemplos do guia e das descrições das tools do MCP).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from functools import cached_property
from typing import Any, Protocol, runtime_checkable

from ..domain.api_impl import ImplConvention
from ..domain.contracts import CONTRACTS_SECTION, ContractRoutes
from ..domain.file_patterns import default_exclude, default_include
from ..domain.file_types import FileType, FileTypes
from ..domain.naming import NameRules
from ..domain.ownership import OwnershipRules, owner_origins
from ..domain.relations import OriginPlacement
from ..domain.repo_card import FactExtractor
from ..settings import Settings
from ..sources.base import Linker


@dataclass(frozen=True)
class GuideExamples:
    """Exemplos do guia do MCP e das descrições das tools (`interfaces/http/mcp_guide.py`, `mcp.py`): o texto do
    núcleo tem marcadores `{ex:<campo>}` e cada campo vem daqui. Os padrões são genéricos (fictícios); um perfil
    troca pelos nomes reais da organização. Só texto: não muda o comportamento das tools.

    - `repo`: nome de um repo (`repo_card`, `related`); `repo_filter`: trecho de nome (`list_repos(name=...)`);
    - `domain`/`service`: nomes técnicos do DDD (`search_code(domain=..., service=...)`, `list_repos(domain=...)`);
    - `operation_key`: chave de operação (`op:<domínio>/<serviço>/<operação>`); `api_operation`:
      `<serviço>.<operação>` (`find_api_impl`);
    - `text_key`: chave de i18n/config (`grep_code`);
    - `contract`: completa "operações ..." (ex.: "de contrato", "SDL"); `catalog_source`: de onde vem o catálogo
      do DDD, completa "Domínios e serviços do DDD (... e os definidos pela equipe)";
    - `origins`: exemplos de origem de relação (`related`); `facts`: o que entra nos fatos do cartão (`repo_card`);
    - `impl_symbol`: o que `find_api_impl` devolve como implementação (ex.: "classe", "handler").
    """

    repo: str = "pedidos-backend"
    repo_filter: str = "pedidos"
    domain: str = "vendas"
    service: str = "pedidos"
    operation_key: str = "op:vendas/pedidos/listarPedidos"
    api_operation: str = "pedidos.listarPedidos"
    text_key: str = "vendas.pedidos.erro_resumo"
    contract: str = "de contrato"
    catalog_source: str = "dos perfis de organização"
    origins: str = "manual, regra, fonte, tag, link, menção"
    facts: str = "catalog-info, manifestos, contratos de API lidos pelos perfis, pastas"
    impl_symbol: str = "classe"

    def as_dict(self) -> dict[str, str]:
        return {f.name: str(getattr(self, f.name)) for f in fields(self)}


@runtime_checkable
class OrgProfile(Protocol):
    name: str                  # nome em CODE_INDEX_PROFILES
    label: str

    def naming(self) -> NameRules:
        """Convenções de nome dos repos (prefixos, tokens sem assunto, sufixos de papel)."""
        ...

    def noise_exclude(self) -> Sequence[str]:
        """Globs de ruído próprio da organização, somados ao exclude padrão dos repos."""
        ...

    def settings_defaults(self) -> Mapping[str, Any]:
        """Padrões de campos do `Settings` que ficam vazios no núcleo (ex.: `openapi_bucket_url`). O ambiente
        (.env) sempre vence o padrão do perfil."""
        ...

    def file_types(self) -> Sequence[FileType]:
        """Formatos de arquivo próprios da organização: globs somados ao include padrão, linguagem pela extensão,
        máscara de configuração e regra das declarações (cabeçalho dos trechos e tabela de símbolos)."""
        ...

    def fact_extractors(self) -> Sequence[FactExtractor]:
        """Extratores dos fatos do cartão (`domain/repo_card.FactExtractor`): quais arquivos ler, a seção que
        preenchem (ex.: `sdl`, os serviços de contrato) e as linhas no texto da tarefa. Rodam na ordem."""
        ...

    def origins(self) -> Sequence[OriginPlacement]:
        """Origens de relação do perfil (rótulo, descrição, se é contestável) e onde entram na ordem global
        (`before`: logo antes de uma origem já registrada). Registradas na carga do perfil; nunca gravadas
        (`stored`), nunca com o nome de outra. Toda origem que os linkers/regras do perfil produzem vem daqui."""
        ...

    def ownership(self) -> Sequence[OwnershipRules]:
        """Regras do dono no DDD (`domain/ownership.OwnershipRules`): catálogo de domínios/serviços, inferências
        com as suas origens (entre `manual` e `usage`, na ordem declarada) e a coleta que elas usam."""
        ...

    def impl_convention(self) -> ImplConvention | None:
        """Convenção operação de contrato → classe que a implementa (`domain/api_impl.ImplConvention`: nomes
        candidatos e os textos das interfaces), ou None. Sem nenhum perfil com convenção, a tool MCP
        `find_api_impl` não é registrada e a rota responde vazio."""
        ...

    def contract_routes(self) -> ContractRoutes:
        """Rotas HTTP das operações de contrato (`domain/contracts.ContractRoutes`: `/<serviço>/<segmento>/<op>`).
        A fonte OpenAPI usa para dizer que uma página descreve uma operação (`kind`/`operation`); vazio = nenhuma."""
        ...

    def linkers(self) -> Sequence[Linker]:
        """Linkers próprios do perfil (`sources/base.Linker`), somados aos do núcleo e aos das fontes no fim de
        cada sync. Toda origem que eles produzem é declarada em `origins`; nome único entre todos os linkers."""
        ...

    def guide_examples(self) -> GuideExamples | None:
        """Exemplos do guia do MCP e das descrições das tools com os nomes da organização (`GuideExamples`), ou
        None (fica o exemplo genérico do núcleo). Vale o do primeiro perfil ativo que oferece."""
        ...


class BaseProfile:
    """Perfil sem convenção nenhuma: base para os perfis concretos (sobrescrevem só o que precisam)."""

    name = "default"
    label = "Genérico (sem convenções de organização)"

    def naming(self) -> NameRules:
        return NameRules()

    def noise_exclude(self) -> Sequence[str]:
        return ()

    def settings_defaults(self) -> Mapping[str, Any]:
        return {}

    def file_types(self) -> Sequence[FileType]:
        return ()

    def fact_extractors(self) -> Sequence[FactExtractor]:
        return ()

    def origins(self) -> Sequence[OriginPlacement]:
        return ()

    def ownership(self) -> Sequence[OwnershipRules]:
        return ()

    def impl_convention(self) -> ImplConvention | None:
        return None

    def contract_routes(self) -> ContractRoutes:
        return ContractRoutes()

    def linkers(self) -> Sequence[Linker]:
        return ()

    def guide_examples(self) -> GuideExamples | None:
        return None


def _ext(name: str) -> str:
    return "." + name.rsplit(".", 1)[1] if "." in name.strip(".") else ""


def format_label(extractor: FactExtractor) -> str:
    """O `label` opcional de um extrator (rótulo do formato na interface, ex.: ".sdl"); sem ele, vazio."""
    value = getattr(extractor, "label", None)
    return value if isinstance(value, str) else ""


def contract_formats(extractors: Sequence[FactExtractor]) -> tuple[str, ...]:
    """Rótulos dos formatos de contrato lidos pelos extratores (os que têm `label`)."""
    return tuple(dict.fromkeys(format_label(x) for x in extractors
                               if x.section == CONTRACTS_SECTION and format_label(x)))


# Campos do Settings que um perfil pode preencher (vazios no núcleo). Outro campo no perfil é erro de carga.
PROFILE_SETTINGS = frozenset({"openapi_bucket_url"})


@dataclass(frozen=True)
class ActiveProfiles:
    """Os perfis ativos, combinados na ordem de CODE_INDEX_PROFILES. Imutável; vazio = comportamento genérico.

    Combinação: regras de nome somadas (`NameRules.merge`), ruído concatenado sem repetir, o padrão de cada
    configuração vem do primeiro perfil que o define, tipos de arquivo combinados (extensão repetida: vale o do
    primeiro perfil), extratores de fatos, regras de dono e linkers concatenados na ordem, convenções de
    implementação combinadas (`ImplConvention.combine`)."""

    profiles: tuple[OrgProfile, ...] = field(default=())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.profiles)

    @cached_property
    def naming(self) -> NameRules:
        return NameRules.combine(p.naming() for p in self.profiles)

    @cached_property
    def noise_exclude(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(g for p in self.profiles for g in p.noise_exclude()))

    @cached_property
    def file_types(self) -> FileTypes:
        return FileTypes.combine(p.file_types() for p in self.profiles)

    @cached_property
    def default_include(self) -> tuple[str, ...]:
        """Include padrão dos repos com os perfis ativos (núcleo + globs dos tipos de arquivo)."""
        return tuple(default_include(self.file_types.globs))

    @cached_property
    def fact_extractors(self) -> tuple[FactExtractor, ...]:
        return tuple(x for p in self.profiles for x in p.fact_extractors())

    @cached_property
    def ownership_rules(self) -> tuple[OwnershipRules, ...]:
        return tuple(r for p in self.profiles for r in p.ownership())

    @cached_property
    def owner_origins(self) -> tuple[str, ...]:
        """Origens do dono, da mais forte para a mais fraca: manual, as das regras dos perfis, usage."""
        return owner_origins(self.ownership_rules)

    @cached_property
    def origin_names(self) -> frozenset[str]:
        """Origens de relação declaradas pelos perfis ativos."""
        return frozenset(o.name for p in self.profiles for g in p.origins() for o in g.origins)

    @cached_property
    def impl_convention(self) -> ImplConvention | None:
        """Convenção operação → implementação dos perfis ativos (None = nenhum perfil oferece)."""
        return ImplConvention.combine(c for p in self.profiles if (c := p.impl_convention()) is not None)

    @cached_property
    def contract_routes(self) -> ContractRoutes:
        """Rotas das operações de contrato dos perfis ativos (segmento repetido: vale o do primeiro perfil)."""
        return ContractRoutes.combine(p.contract_routes() for p in self.profiles)

    @cached_property
    def linkers(self) -> tuple[Linker, ...]:
        """Linkers dos perfis ativos, na ordem dos perfis (nome repetido entre perfis → ValueError)."""
        out: dict[str, Linker] = {}
        for p in self.profiles:
            for linker in p.linkers():
                if linker.name in out:
                    raise ValueError(f"perfil {p.name!r}: linker com nome já usado por outro perfil: {linker.name!r}")
                out[linker.name] = linker
        return tuple(out.values())

    @cached_property
    def guide_examples(self) -> GuideExamples:
        """Exemplos do guia do MCP: os do primeiro perfil ativo que oferece; sem nenhum, os genéricos do núcleo."""
        for p in self.profiles:
            examples = p.guide_examples()
            if examples is not None:
                return examples
        return GuideExamples()

    def contract_format(self, path: str) -> str:
        """Rótulo do formato de um arquivo de contrato (o `label` do primeiro extrator de contratos que lê o
        arquivo, ex.: ".sdl"); sem rótulo, a extensão do arquivo."""
        name = path.rsplit("/", 1)[-1]
        for x in self.fact_extractors:
            if x.section == CONTRACTS_SECTION and x.matches(name):
                return format_label(x) or _ext(name)
        return _ext(name)

    def describe(self) -> list[dict[str, Any]]:
        """Perfis ativos para a API/interface: nome, rótulo, origens que declaram (rótulos em
        GET /api/v1/relations/origins) e formatos de contrato que leem."""
        return [{"name": p.name, "label": p.label,
                 "origins": [o.name for g in p.origins() for o in g.origins],
                 "contract_formats": list(contract_formats(p.fact_extractors()))} for p in self.profiles]

    @cached_property
    def default_exclude(self) -> tuple[str, ...]:
        """Exclude padrão dos repos com os perfis ativos (núcleo + ruído dos perfis)."""
        return tuple(default_exclude(self.noise_exclude))

    def setting(self, settings: Settings, name: str) -> Any:
        """Valor efetivo de uma configuração que o perfil pode preencher: o do ambiente, se definido; senão o
        padrão do primeiro perfil que o traz; senão None."""
        if name not in PROFILE_SETTINGS:
            raise KeyError(f"configuração sem padrão de perfil: {name}")
        value = getattr(settings, name)
        if value is not None:
            return value
        for p in self.profiles:
            defaults = p.settings_defaults()
            if defaults.get(name) is not None:
                return defaults[name]
        return None
