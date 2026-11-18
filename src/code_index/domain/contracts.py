"""Contratos de API declarados nos repos (puro, sem I/O).

Um contrato descreve as operações que um serviço expõe, no formato genérico domínio/serviço/grupo/nome. Quem
lê o arquivo de contrato (ex.: um IDL próprio da empresa) é um extrator de fatos de um perfil de organização
(`domain/repo_card.FactExtractor`); o núcleo só conhece este formato.

Nos fatos do cartão (`repo_cards.material`), os serviços de contrato ficam na chave `sdl` (nome histórico,
mantido por compatibilidade com o material já gravado e com a interface), um objeto por serviço com as
operações agrupadas: `{domain, service, path, queries, actions, signals, events, entities, private}`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from functools import cached_property
from typing import Any, Literal

# Chave dos serviços de contrato em RepoFacts / material.facts (compatibilidade: nome histórico).
CONTRACTS_SECTION = "sdl"

Group = Literal["query", "action", "signal", "event", "entity"]
# grupo → campo do serviço (ordem de exibição)
GROUP_FIELDS: dict[str, str] = {
    "query": "queries", "action": "actions", "signal": "signals", "event": "events", "entity": "entities",
}
# grupos que são operações chamáveis (os outros são eventos e entidades)
CALLABLE_GROUPS = ("query", "action", "signal")


@dataclass(frozen=True)
class Operation:
    """Uma operação (ou evento/entidade) declarada no contrato de um serviço."""

    domain: str
    service: str
    group: Group
    name: str
    private: bool = False   # uso interno (não faz parte da API pública)
    path: str = ""          # arquivo do contrato no repo


@dataclass(frozen=True)
class ContractService:
    """Um serviço declarado num contrato, com as operações agrupadas (formato gravado nos fatos)."""

    domain: str
    service: str
    path: str
    queries: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    private: tuple[str, ...] = ()  # operações marcadas como de uso interno

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    def operations(self) -> Iterator[Operation]:
        private = set(self.private)
        for group, attr in GROUP_FIELDS.items():
            for name in getattr(self, attr):
                yield Operation(self.domain, self.service, group, name,  # type: ignore[arg-type]
                                private=group in CALLABLE_GROUPS and name in private, path=self.path)

    @classmethod
    def from_operations(cls, domain: str, service: str, path: str, ops: Iterable[Operation]) -> ContractService:
        """Agrupa operações de um serviço (ordem de chegada, sem repetir o nome dentro do grupo)."""
        groups: dict[str, list[str]] = {attr: [] for attr in GROUP_FIELDS.values()}
        private: list[str] = []
        for op in ops:
            bucket = groups[GROUP_FIELDS[op.group]]
            if op.name not in bucket:
                bucket.append(op.name)
            if op.private and op.group in CALLABLE_GROUPS:
                private.append(op.name)
        return cls(domain=domain, service=service, path=path, private=tuple(private),
                   **{k: tuple(v) for k, v in groups.items()})

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ContractService:
        """Inverso de `to_dict` (tolerante: campos ausentes ficam vazios)."""
        return cls(domain=str(raw.get("domain") or ""), service=str(raw.get("service") or ""),
                   path=str(raw.get("path") or ""),
                   **{k: tuple(str(x) for x in raw.get(k) or ())
                      for k in (*GROUP_FIELDS.values(), "private")})


def contracts_payload(services: Iterable[Mapping[str, Any]], format_of: Callable[[str], str]) -> list[dict[str, Any]]:
    """Serviços de contrato no formato genérico da API/interface: `{format, path, domain, service, groups, private}`.
    `format`: rótulo do formato do arquivo (`format_of(path)`, ex.: ".sdl"); `groups`: grupo (`query`, `action`,
    `signal`, `event`, `entity`) → nomes, só os grupos com algum, na ordem de `GROUP_FIELDS`."""
    out: list[dict[str, Any]] = []
    for raw in services:
        s = ContractService.from_dict(raw)
        out.append({"format": format_of(s.path), "path": s.path, "domain": s.domain, "service": s.service,
                    "groups": {g: list(getattr(s, attr)) for g, attr in GROUP_FIELDS.items() if getattr(s, attr)},
                    "private": list(s.private)})
    return out


@dataclass(frozen=True)
class ContractRoutes:
    """Rotas HTTP das operações de contrato: `/<serviço>/<segmento>/<operação>`, com `segments` = (segmento da
    rota, grupo), ex.: `(("queries", "query"),)`. É convenção de quem publica a API (perfil de organização, gancho
    `contract_routes`); vazio = nenhuma rota é reconhecida como operação (a página fica só com método e caminho)."""

    segments: tuple[tuple[str, Group], ...] = ()

    def __post_init__(self) -> None:
        for segment, group in self.segments:
            if group not in CALLABLE_GROUPS or not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
                raise ValueError(f"rota de contrato inválida: {segment!r} → {group!r}")

    def __bool__(self) -> bool:
        return bool(self.segments)

    @cached_property
    def _pattern(self) -> re.Pattern[str] | None:
        if not self.segments:
            return None
        alternatives = "|".join(re.escape(s) for s, _ in self.segments)
        return re.compile(r"^/([^/\s]+)/(" + alternatives + r")/([A-Za-z_]\w*)$")

    def operation(self, http_path: str, service: str) -> tuple[Group, str] | None:
        """`/pedidos/queries/listarPedidos` (do próprio `service`) → (`query`, `listarPedidos`); senão None."""
        if self._pattern is None:
            return None
        m = self._pattern.match(http_path)
        if not m or m.group(1).lower() != service.lower():
            return None
        return dict(self.segments)[m.group(2)], m.group(3)

    @staticmethod
    def combine(routes: Iterable[ContractRoutes]) -> ContractRoutes:
        """Rotas de vários perfis: segmentos somados na ordem (segmento repetido: vale o do primeiro)."""
        out: dict[str, Group] = {}
        for r in routes:
            for segment, group in r.segments:
                out.setdefault(segment, group)
        return ContractRoutes(tuple(out.items()))


def operations(services: Iterable[Mapping[str, Any]]) -> list[Operation]:
    """Operações de uma lista de serviços no formato dos fatos (`facts["sdl"]`)."""
    return [op for raw in services for op in ContractService.from_dict(raw).operations()]
