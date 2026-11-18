"""Relações entre entidades (puro, sem I/O): chaves, tipos de relação e origens.

Todo vínculo do sistema é uma linha da tabela `relations`: (src, dst, tipo, origem) + estado.
- **Chave de entidade**: texto com prefixo do tipo (`repo:`, `doc:`, `op:`, `code:`, `dir:`, `dom:`,
  `svc:`). Sem tabela de entidades: a chave basta para ligar e para mostrar.
- **Origem**: quem criou a relação. Linkers determinísticos (menção, tag, SDL, handler...) recalculam as
  suas no fim de cada sync; pessoas e agents gravam as suas (manual, agent, llm).
- **Relação efetiva** (o que busca, X-Ray e MCP usam): status `active` e action `add`, menos as relações
  de origem contestável (heurística) que tenham uma remoção `active` para o mesmo par.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ------------------------------------------------------------------ chaves de entidade

REPO, DOC, OP, CODE = "repo", "doc", "op", "code"
DIR, DOM, SVC = "dir", "dom", "svc"   # pasta de um repo (monorepo); domínio e serviço do DDD


def repo_key(name: str) -> str:
    return f"{REPO}:{name}"


def doc_key(doc_repo: str, path: str) -> str:
    return f"{DOC}:{doc_repo}/{path}"


def op_key(domain: str, service: str, operation: str) -> str:
    return f"{OP}:{domain.lower()}/{service.lower()}/{operation}"


def code_key(repo: str, path: str, line: int) -> str:
    return f"{CODE}:{repo}/{path}:{int(line)}"


def dir_key(repo: str, path: str) -> str:
    """Pasta de um repo (sem barras nas pontas): 'dir:repo/modulos/folha'."""
    return f"{DIR}:{repo}/{path.strip('/')}"


def split_dir_key(key: str) -> tuple[str, str]:
    repo, _, path = key.removeprefix(f"{DIR}:").partition("/")
    return repo, path


def dom_key(domain: str) -> str:
    return f"{DOM}:{domain.lower()}"


def svc_key(domain: str, service: str) -> str:
    return f"{SVC}:{domain.lower()}/{service.lower()}"


def key_kind(key: str) -> str:
    return key.split(":", 1)[0]


def split_doc_key(key: str) -> tuple[str, str]:
    """'doc:docs__wiki/pt-br/x.md' → ('docs__wiki', 'pt-br/x.md')."""
    body = key.removeprefix(f"{DOC}:")
    doc_repo, _, path = body.partition("/")
    return doc_repo, path


def split_repo_key(key: str) -> str:
    return key.removeprefix(f"{REPO}:")


def split_code_key(key: str) -> tuple[str, str, int]:
    """'code:repo/caminho/A.java:17' → ('repo', 'caminho/A.java', 17)."""
    body, _, line = key.removeprefix(f"{CODE}:").rpartition(":")
    repo, _, path = body.partition("/")
    return repo, path, int(line)


# ------------------------------------------------------------------ tipos de relação

DOCUMENTS = "documents"            # doc → repo: a página documenta o repo
DESCRIBES = "describes"            # doc → op: a página de API descreve a operação
IMPLEMENTED_BY = "implemented_by"  # op → code: a operação é implementada por esta classe
RELATED_TO = "related_to"          # repo → repo: repos relacionados (cartão do repo)
BELONGS_TO = "belongs_to"          # repo|dir → svc|dom: dono do código no DDD (ver domain/ownership.py)
RELATION_TYPES = (DOCUMENTS, DESCRIBES, IMPLEMENTED_BY, RELATED_TO, BELONGS_TO)

# ------------------------------------------------------------------ origens


@dataclass(frozen=True)
class Origin:
    name: str
    label: str            # rótulo curto na interface
    description: str
    contestable: bool = False  # heurística: um agent pode sugerir remover (e a remoção aprovada a anula)
    stored: bool = False       # gravada por pessoa/agent (não é recalculada no sync)
    # declarada pelo próprio repo (ex.: o .sdl do repo no perfil senior): a interface avisa antes de uma atribuição
    # manual sobrepor um dono com esta origem
    authoritative: bool = False


# Origens do núcleo, da mais confiável para a menos. Os perfis de organização (profiles/) acrescentam as suas na
# carga (`register_origins`), cada grupo logo antes de uma origem já registrada: com o perfil `senior`, `sdl`,
# `declared`, `sibling` e `api_calls` entram antes de `usage` (a ordem de força do dono no DDD segue esta ordem
# global) e `handler` antes de `service`.
CORE_ORIGINS: tuple[Origin, ...] = (
    Origin("manual", "manual", "gravado por uma pessoa", stored=True),
    Origin("agent", "agent", "gravado por um agent com token", stored=True),
    Origin("llm", "LLM (aprovado)", "sugerido por LLM e aprovado por uma pessoa", stored=True),
    Origin("explicit", "regra", "regra --link da fonte de documentação"),
    Origin("source", "fonte", "declarado pela própria fonte (ex.: página de API → operação)"),
    Origin("usage", "uso da lib", "biblioteca usada só por repos deste domínio/serviço"),
    Origin("service", "nome do serviço", "nome do serviço de API igual ao nome do repo"),
    Origin("card", "cartão", "repos relacionados no cartão do repo"),
    Origin("tag", "tag", "tag da página igual a um apelido do repo", contestable=True),
    Origin("url", "link", "link para o repo no GitHub", contestable=True),
    Origin("mention", "menção", "termo do texto igual a um apelido forte do repo", contestable=True),
)
CORE_ORIGIN_NAMES = frozenset(o.name for o in CORE_ORIGINS)
# Só o núcleo grava por pessoa/agent: um perfil não declara origem gravada (o conjunto não muda na carga).
STORED = frozenset(o.name for o in CORE_ORIGINS if o.stored)
_ORIGIN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


@dataclass(frozen=True)
class OriginPlacement:
    """Origens de um perfil e onde entram na ordem global: logo antes de `before` (uma origem já registrada).
    Dois grupos antes da mesma origem ficam na ordem em que foram registrados (a dos perfis ativos)."""

    before: str
    origins: tuple[Origin, ...]


class _Registry:
    """Ordem global das origens: núcleo + as dos perfis carregados no processo. Uma origem de perfil continua
    registrada (rótulo, posição) mesmo que outro conjunto de perfis esteja ativo depois: linhas antigas no banco
    seguem com rótulo. Origem desconhecida (perfil que não foi carregado) também é aceita na leitura: fica no fim
    da ordem, sem rótulo próprio e não contestável."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.items: tuple[Origin, ...] = CORE_ORIGINS
        self.owners: dict[str, str] = {}   # origem de perfil → nome do perfil que a registrou
        self._index()

    def _index(self) -> None:
        self.by_name = {o.name: o for o in self.items}
        self.order = tuple(o.name for o in self.items)
        self.contestable = frozenset(o.name for o in self.items if o.contestable)

    def register(self, placement: OriginPlacement, profile: str) -> None:
        items = list(self.items)
        new: list[Origin] = []
        for o in placement.origins:
            if not _ORIGIN_NAME.match(o.name):
                raise ValueError(f"perfil {profile!r}: nome de origem inválido: {o.name!r}")
            if o.stored:
                raise ValueError(f"perfil {profile!r}: origem gravada (stored) é só do núcleo: {o.name!r}")
            known = self.by_name.get(o.name)
            if known is not None:
                if o.name in CORE_ORIGIN_NAMES or self.owners.get(o.name) != profile or known != o:
                    raise ValueError(f"perfil {profile!r}: origem já registrada: {o.name!r}")
                continue  # o mesmo perfil carregado de novo: nada muda
            new.append(o)
        if not new:
            return
        if placement.before not in self.by_name:
            raise ValueError(f"perfil {profile!r}: origem de referência desconhecida: {placement.before!r}")
        if len({o.name for o in new}) != len(new):
            raise ValueError(f"perfil {profile!r}: origem repetida")
        at = items.index(self.by_name[placement.before])
        self.items = (*items[:at], *new, *items[at:])
        self.owners.update({o.name: profile for o in new})
        self._index()


_REGISTRY = _Registry()


def register_origins(placement: OriginPlacement, profile: str) -> None:
    """Acrescenta as origens de um perfil à ordem global (registro dos perfis, na carga). Idempotente para o
    mesmo perfil; nome repetido de outro perfil ou do núcleo, origem gravada ou referência desconhecida →
    ValueError."""
    _REGISTRY.register(placement, profile)


def reset_origins() -> None:
    """Volta às origens do núcleo (testes; `profiles.registry.clear_cache`)."""
    _REGISTRY.reset()


def origins() -> tuple[Origin, ...]:
    """Todas as origens registradas (núcleo + perfis carregados), da mais confiável para a menos."""
    return _REGISTRY.items


def origin_order() -> tuple[str, ...]:
    return _REGISTRY.order


def origin_info(name: str) -> Origin | None:
    return _REGISTRY.by_name.get(name)


def origin_label(name: str) -> str:
    """Rótulo curto (o próprio nome, para origem desconhecida)."""
    o = _REGISTRY.by_name.get(name)
    return o.label if o else name


def origin_description(name: str) -> str:
    o = _REGISTRY.by_name.get(name)
    return o.description if o else name


def is_contestable(name: str) -> bool:
    return name in _REGISTRY.contestable


def contestable_origins() -> frozenset[str]:
    return _REGISTRY.contestable


def derived_origins() -> frozenset[str]:
    """Origens registradas recalculadas no sync (todas menos as gravadas)."""
    return frozenset(_REGISTRY.order) - STORED


def is_core_origin(name: str) -> bool:
    return name in CORE_ORIGIN_NAMES


def origin_rank(origin: str) -> int:
    order = _REGISTRY.order
    return order.index(origin) if origin in _REGISTRY.by_name else len(order)


def sort_origins(origins: Any) -> list[str]:
    return sorted(set(origins), key=origin_rank)


def origins_payload() -> list[dict[str, Any]]:
    """Origens para a API/interface (rótulo, ordem, se é contestável ou gravada)."""
    return [{"name": o.name, "label": o.label, "description": o.description, "contestable": o.contestable,
             "stored": o.stored, "authoritative": o.authoritative, "order": i} for i, o in enumerate(_REGISTRY.items)]


def origin_profile(name: str) -> str | None:
    """O perfil de organização que registrou a origem (None = núcleo ou desconhecida)."""
    return _REGISTRY.owners.get(name)


@dataclass(frozen=True)
class Relation:
    """Uma relação produzida por um linker (ou gravada)."""

    src: str
    dst: str
    type: str
    origin: str
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: str | None = None

    def __post_init__(self) -> None:
        if self.type not in RELATION_TYPES:
            raise ValueError(f"tipo de relação inválido: {self.type}")
        # Só a forma do nome: uma linha antiga de um perfil desligado (origem desconhecida agora) continua legível.
        # Quem produz relações (linkers) é conferido no rebuild_relations contra as origens registradas.
        if not _ORIGIN_NAME.match(self.origin):
            raise ValueError(f"origem inválida: {self.origin}")

    @property
    def contestable(self) -> bool:
        return is_contestable(self.origin)
