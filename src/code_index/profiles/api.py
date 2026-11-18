"""Superfície pública para quem escreve um perfil de organização (pacote de fora, com entry point no grupo
`code_index.profiles`). Um perfil importa só daqui: o resto do núcleo pode mudar de lugar sem quebrar o pacote.

    from code_index.profiles.api import BaseProfile, FileType, NameRules, ...

Contrato e ganchos: `profiles/base.py` (`OrgProfile`, `BaseProfile`). Registro: `profiles/registry.py`.
"""

from __future__ import annotations

from ..domain.api_impl import ImplConvention, ImplNames, match_handlers
from ..domain.contracts import (
    CALLABLE_GROUPS, CONTRACTS_SECTION, GROUP_FIELDS, ContractRoutes, ContractService, Operation,
)
from ..domain.documents import API_OPERATION, API_OVERVIEW, API_TYPES
from ..domain.file_types import DeclRule, FileType
from ..domain.naming import NameRules
from ..domain.ownership import (
    Assignment, Catalog, FactsByRepo, Owner, OwnByRepo, OwnershipRules, base_name, is_name, norm, repo_owners,
    resolve,
)
from ..domain.relations import (
    BELONGS_TO, DESCRIBES, DOCUMENTS, IMPLEMENTED_BY, RELATED_TO, Origin, OriginPlacement, Relation, code_key,
    dir_key, doc_key, op_key, repo_key,
)
from ..domain.repo_card import FactExtractor, is_test_path
from ..settings import Settings
from ..sources.base import Linker
from .base import BaseProfile, GuideExamples, OrgProfile

__all__ = [
    "API_OPERATION", "API_OVERVIEW", "API_TYPES", "Assignment", "BELONGS_TO", "BaseProfile", "CALLABLE_GROUPS",
    "CONTRACTS_SECTION", "Catalog", "ContractRoutes", "ContractService", "DESCRIBES", "DOCUMENTS", "DeclRule",
    "FactExtractor", "FactsByRepo", "FileType", "GROUP_FIELDS", "GuideExamples", "IMPLEMENTED_BY", "ImplConvention",
    "ImplNames", "Linker", "NameRules", "Operation", "OrgProfile", "Origin", "OriginPlacement", "OwnByRepo", "Owner",
    "OwnershipRules", "RELATED_TO", "Relation", "Settings", "base_name", "code_key", "dir_key", "doc_key",
    "is_name", "is_test_path", "match_handlers", "norm", "op_key", "repo_key", "repo_owners", "resolve",
]
