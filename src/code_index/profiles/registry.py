"""Registro dos perfis de organização (ver profiles/base.py).

Fontes, em ordem de precedência:
1. entry points do grupo `code_index.profiles` (pacote instalado à parte; o nome do entry point é o nome do
   perfil e o objeto é a classe, uma função sem argumentos ou a instância). Um entry point com o nome de um
   perfil embutido o substitui: é assim que um perfil sai deste repositório sem mudar a configuração;
2. perfis embutidos (`_BUILTIN`), importados pelo caminho só quando pedidos: o núcleo nunca importa um perfil
   concreto diretamente.

A carga é memoizada pelo nome (perfis são imutáveis e o conjunto instalado não muda com o processo no ar); nos
testes, `clear_cache()`. Carregar um perfil registra as origens dele na ordem global (`domain/relations`); elas
continuam registradas (rótulo e posição para as linhas já gravadas) mesmo que outro conjunto de perfis fique
ativo depois no mesmo processo. `clear_cache()` volta às origens do núcleo.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from functools import lru_cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from ..settings import Settings
from ..domain.api_impl import ImplConvention
from ..domain.contracts import ContractRoutes
from ..domain.ownership import CORE_MATERIAL_KEYS, OwnershipRules
from ..domain.relations import OriginPlacement, register_origins, reset_origins
from ..domain.repo_card import CORE_FACT_KEYS, FactExtractor
from .base import PROFILE_SETTINGS, ActiveProfiles, GuideExamples, OrgProfile

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "code_index.profiles"
DEFAULT = "default"

# nome → "módulo:objeto". O perfil da Senior fica num pacote próprio (profiles/senior) para sair do repo depois.
_BUILTIN: dict[str, str] = {
    DEFAULT: "code_index.profiles.default:DefaultProfile",
    "senior": "code_index.profiles.senior:SeniorProfile",
}


def _entry_points() -> dict[str, EntryPoint]:
    return {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}


def available_profiles() -> list[str]:
    """Nomes de todos os perfis que podem ser ativados (embutidos e entry points)."""
    return sorted(set(_BUILTIN) | set(_entry_points()))


def _instantiate(obj: Any, name: str) -> OrgProfile:
    if isinstance(obj, type) or (callable(obj) and not isinstance(obj, OrgProfile)):
        obj = obj()
    if not isinstance(obj, OrgProfile):
        raise TypeError(f"perfil {name!r}: o objeto carregado não segue o contrato OrgProfile")
    if obj.name != name:
        raise ValueError(f"perfil {name!r}: o objeto carregado se chama {obj.name!r}")
    extra = set(obj.settings_defaults()) - PROFILE_SETTINGS
    if extra:
        raise ValueError(f"perfil {name!r}: padrões de configuração não suportados: {', '.join(sorted(extra))}")
    for x in obj.fact_extractors():
        if not isinstance(x, FactExtractor):
            raise TypeError(f"perfil {name!r}: extrator de fatos fora do contrato FactExtractor: {x!r}")
        if x.section in CORE_FACT_KEYS:
            raise ValueError(f"perfil {name!r}: seção de fatos reservada ao núcleo: {x.section!r}")
    declared: set[str] = set()
    for g in obj.origins():
        if not isinstance(g, OriginPlacement):
            raise TypeError(f"perfil {name!r}: origens fora do contrato OriginPlacement: {g!r}")
        declared |= {o.name for o in g.origins}
    for rules in obj.ownership():
        if not isinstance(rules, OwnershipRules):
            raise TypeError(f"perfil {name!r}: regras de dono fora do contrato OwnershipRules: {rules!r}")
        undeclared = [o for o in rules.origins if o not in declared]
        if undeclared:
            raise ValueError(f"perfil {name!r}: regras de dono com origens que o perfil não declara: "
                             f"{', '.join(undeclared)}")
        reserved = sorted(set(rules.material_keys) & CORE_MATERIAL_KEYS)
        if reserved:
            raise ValueError(f"perfil {name!r}: chaves do material reservadas ao núcleo: {', '.join(reserved)}")
    convention = obj.impl_convention()
    if convention is not None and not isinstance(convention, ImplConvention):
        raise TypeError(f"perfil {name!r}: convenção de implementação fora do contrato ImplConvention: {convention!r}")
    if not isinstance(obj.contract_routes(), ContractRoutes):
        raise TypeError(f"perfil {name!r}: rotas de contrato fora do contrato ContractRoutes")
    examples = obj.guide_examples()
    if examples is not None and not isinstance(examples, GuideExamples):
        raise TypeError(f"perfil {name!r}: exemplos do guia fora do contrato GuideExamples: {examples!r}")
    names: set[str] = set()
    for linker in obj.linkers():
        lname, origins = getattr(linker, "name", None), getattr(linker, "origins", None)
        if not isinstance(lname, str) or not lname or not isinstance(origins, frozenset) \
                or not callable(getattr(linker, "run", None)):
            raise TypeError(f"perfil {name!r}: linker fora do contrato Linker: {linker!r}")
        if lname in names:
            raise ValueError(f"perfil {name!r}: linker repetido: {lname!r}")
        names.add(lname)
        undeclared = sorted(origins - declared)
        if undeclared:
            raise ValueError(f"perfil {name!r}: linker {lname!r} com origens que o perfil não declara: "
                             f"{', '.join(undeclared)}")
    return obj


def _register(profile: OrgProfile) -> OrgProfile:
    """Registra as origens do perfil na ordem global (erro de nome/posição → ValueError na carga)."""
    for g in profile.origins():
        register_origins(g, profile.name)
    return profile


@lru_cache(maxsize=None)
def _load_one(name: str) -> OrgProfile:
    eps = _entry_points()
    if name in eps:
        if name in _BUILTIN:
            log.info("perfil %r: usando o do entry point %s (no lugar do embutido)", name, eps[name].value)
        return _register(_instantiate(eps[name].load(), name))
    if name in _BUILTIN:
        module, _, attr = _BUILTIN[name].partition(":")
        return _register(_instantiate(getattr(importlib.import_module(module), attr), name))
    raise ValueError(f"perfil de organização desconhecido: {name!r} (disponíveis: {', '.join(available_profiles())})")


@lru_cache(maxsize=None)
def _load(names: tuple[str, ...]) -> ActiveProfiles:
    wanted = [n for n in dict.fromkeys(x.strip().lower() for x in names) if n and n != DEFAULT]
    for n in wanted:
        if not n.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"nome de perfil inválido: {n!r}")
    return ActiveProfiles(tuple(_load_one(n) for n in wanted))


def load_profiles(names: Sequence[str]) -> ActiveProfiles:
    """Perfis ativos, na ordem pedida. Vazio ou só `default` = nenhum perfil (comportamento genérico).
    Nome desconhecido → ValueError (com os disponíveis)."""
    return _load(tuple(names))


def clear_cache() -> None:
    """Esquece os perfis já carregados e as origens que eles registraram (testes que mexem nos entry points ou
    nos embutidos)."""
    _load.cache_clear()
    _load_one.cache_clear()
    reset_origins()


_warned_default = False


def active_profiles(settings: Settings) -> ActiveProfiles:
    """Perfis ativos segundo `CODE_INDEX_PROFILES` (`Settings.profiles`). Sem a variável definida, vale o padrão do
    `Settings` (por enquanto `senior`, que vai virar `default`): avisa uma vez no log."""
    global _warned_default
    if "profiles" not in settings.model_fields_set and not _warned_default:
        _warned_default = True
        log.info("CODE_INDEX_PROFILES não definido: usando o padrão %s (vai mudar para 'default'; defina a "
                 "variável para fixar)", ",".join(settings.profiles) or "default")
    return load_profiles(settings.profiles)
