"""Regras de nome de repositório de uma organização: prefixos, tokens sem assunto e sufixos de papel. Puro.

O núcleo não conhece as convenções de nome de nenhuma empresa: os valores vêm do perfil de organização ativo
(`profiles/`, `OrgProfile.naming`). O padrão (`NameRules()`) é genérico: nenhum prefixo sai do nome, nenhum
sufixo é tratado como papel e só os tokens genéricos do núcleo (`repo_card`) são ignorados.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class NameRules:
    """Convenções de nome de repo de uma organização (todas opcionais).

    - `repo_prefixes`: prefixos literais dos repos (ex.: `acme-`), tirados ao comparar o nome de um repo com o
      nome de um serviço (linker de serviço do OpenAPI).
    - `short_prefix_max`: no nome-base (dono no DDD, `ownership.base_name`), o primeiro pedaço do nome com até
      N letras é prefixo e sai (`ms-`, `lib-`). 0 = nunca.
    - `alias_prefix_max`: nos apelidos do nome do repo (`doc_links.repo_name_aliases`), o pedaço antes do
      primeiro `-` com até N letras é prefixo: o nome sem ele vira mais um apelido. 0 = nunca.
    - `generic_tokens`: tokens sem assunto nos nomes (nome da empresa...), somados aos genéricos do núcleo na
      busca de repos relacionados (`repo_card.name_tokens`).
    - `role_suffixes`: sufixos que não mudam o assunto do repo (`-frontend`, `-backend`...): saem do nome-base.
    - `service_suffixes`: sufixos tirados do fim do nome (sem separadores) do repo e do serviço ao comparar os
      dois no linker de serviço do OpenAPI (ex.: `service`, `backend`: `pedidosbackend` ≈ `pedidosservice`).
    """

    repo_prefixes: tuple[str, ...] = ()
    short_prefix_max: int = 0
    alias_prefix_max: int = 0
    generic_tokens: frozenset[str] = frozenset()
    role_suffixes: frozenset[str] = frozenset()
    service_suffixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.short_prefix_max < 0 or self.alias_prefix_max < 0:
            raise ValueError("tamanho máximo de prefixo não pode ser negativo")
        object.__setattr__(self, "repo_prefixes", tuple(dict.fromkeys(p.lower() for p in self.repo_prefixes if p)))
        object.__setattr__(self, "generic_tokens", frozenset(t.lower() for t in self.generic_tokens))
        object.__setattr__(self, "role_suffixes", frozenset(t.lower() for t in self.role_suffixes))
        object.__setattr__(self, "service_suffixes",
                           tuple(dict.fromkeys(s.lower() for s in self.service_suffixes if s)))

    def merge(self, other: NameRules) -> NameRules:
        """Regras de dois perfis ativos juntos: listas somadas (na ordem), limites de prefixo pelo maior."""
        return NameRules(
            repo_prefixes=(*self.repo_prefixes, *other.repo_prefixes),
            short_prefix_max=max(self.short_prefix_max, other.short_prefix_max),
            alias_prefix_max=max(self.alias_prefix_max, other.alias_prefix_max),
            generic_tokens=self.generic_tokens | other.generic_tokens,
            role_suffixes=self.role_suffixes | other.role_suffixes,
            service_suffixes=(*self.service_suffixes, *other.service_suffixes),
        )

    @staticmethod
    def combine(rules: Iterable[NameRules]) -> NameRules:
        out = NameRules()
        for r in rules:
            out = out.merge(r)
        return out
