"""Convenções da Senior: nomes dos repos (`snr-`), ruído das migrations, o bucket do catálogo OpenAPI, os
arquivos SDL/PDL (tipos de arquivo e extratores de fatos, em `sdl.py`), o dono no DDD (catálogo do SDL e as
inferências declared/sibling/api_calls, em `ownership.py`), operação → handler (`api_impl.py`) e os linkers
handler/openapi-sdl (`linkers.py`) e os exemplos do guia do MCP (repos e operações reais da plataforma)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from code_index.profiles.api import (
    BaseProfile, ContractRoutes, FactExtractor, FileType, GuideExamples, ImplConvention, Linker, NameRules,
    OriginPlacement, OwnershipRules,
)

from .api_impl import CONTRACT_ROUTES, IMPL_CONVENTION
from .linkers import LINK_ORIGINS, SDL_ORIGINS, HandlerLinker, SdlLinker
from .ownership import ORIGINS, SeniorOwnership
from .sdl import FACT_EXTRACTORS, FILE_TYPES

# Repos da plataforma: 'snr-<assunto>-<papel>' (ex.: snr-email-backend). Prefixo curto: snr-, ms-, lib-...
NAMING = NameRules(
    repo_prefixes=("snr-",),       # linker de serviço do OpenAPI: 'snr-email' ≈ serviço 'email'
    short_prefix_max=3,            # nome-base do dono no DDD
    alias_prefix_max=4,            # apelidos do nome do repo (doc_links)
    generic_tokens=frozenset({"senior", "snr"}),
    # Sufixos que não mudam o "assunto" do repo: x-frontend, x-backend e x-service falam do mesmo serviço.
    role_suffixes=frozenset({
        "frontend", "front", "backend", "back", "web", "ui", "app", "service", "services", "api", "server", "client",
        "lib", "library", "sdk", "core", "impl", "bff", "angular", "react", "vue", "java", "node", "go", "py",
    }),
    # linker de serviço do OpenAPI: 'email-backend' ≈ serviço 'emailservice' (sem o sufixo, os nomes batem)
    service_suffixes=("service", "backend"),
)

# Migrations duplicadas por banco (mantemos só PostgreSql).
_DB_VENDORS = ["H2", "h2", "Oracle", "oracle", "SqlServer", "SQLServer", "sqlserver", "MySql", "MySQL", "mysql"]
NOISE_EXCLUDE = (
    *(f"**/{d}/**/{v}" for d in ("migration", "migrations") for v in _DB_VENDORS),
    "**/*-integrated-test",  # módulos de teste integrado do legado (asas-*-integrated-test): fora do TEST_EXCLUDE
)

# Catálogo público das especificações OpenAPI da plataforma (<domínio>_<serviço>.swagger.yaml).
OPENAPI_BUCKET_URL = "https://s3.amazonaws.com/br-com-senior-ecossistema-api"


# Exemplos do guia do MCP e das descrições das tools (os textos de antes da separação em perfis).
GUIDE_EXAMPLES = GuideExamples(
    repo="snr-email-backend",
    repo_filter="email",
    domain="platform",
    service="authentication",
    operation_key="op:platform/email/sendEmail",
    api_operation="email.listBlacklistedEmails",
    text_key="platform.authorization.error_summary",
    contract="SDL",
    catalog_source="do SDL",
    origins="manual, sdl, handler, tag, menção, repo irmão",
    facts="catalog-info, operações e eventos do .sdl, propriedades do .pdl, pastas",
    impl_symbol="handler",
)


class SeniorProfile(BaseProfile):
    name = "senior"
    label = "Senior Sistemas (plataforma SeniorX)"

    def naming(self) -> NameRules:
        return NAMING

    def noise_exclude(self) -> Sequence[str]:
        return NOISE_EXCLUDE

    def settings_defaults(self) -> Mapping[str, Any]:
        return {"openapi_bucket_url": OPENAPI_BUCKET_URL}

    def file_types(self) -> Sequence[FileType]:
        return FILE_TYPES

    def fact_extractors(self) -> Sequence[FactExtractor]:
        return FACT_EXTRACTORS

    def origins(self) -> Sequence[OriginPlacement]:
        # ordem de registro: `sdl` e depois as do dono (ambos antes de `usage`), `handler` antes de `service`
        return (SDL_ORIGINS, ORIGINS, LINK_ORIGINS)

    def ownership(self) -> Sequence[OwnershipRules]:
        return (_OWNERSHIP,)

    def impl_convention(self) -> ImplConvention | None:
        return IMPL_CONVENTION

    def contract_routes(self) -> ContractRoutes:
        return CONTRACT_ROUTES

    def linkers(self) -> Sequence[Linker]:
        return (HandlerLinker(IMPL_CONVENTION.names), SdlLinker())

    def guide_examples(self) -> GuideExamples | None:
        return GUIDE_EXAMPLES


_OWNERSHIP = SeniorOwnership()
