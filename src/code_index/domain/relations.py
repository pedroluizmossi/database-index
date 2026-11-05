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


ORIGINS: tuple[Origin, ...] = (  # da mais confiável para a menos
    Origin("manual", "manual", "gravado por uma pessoa", stored=True),
    Origin("agent", "agent", "gravado por um agent com token", stored=True),
    Origin("llm", "LLM (aprovado)", "sugerido por LLM e aprovado por uma pessoa", stored=True),
    Origin("explicit", "regra", "regra --link da fonte de documentação"),
    Origin("source", "fonte", "declarado pela própria fonte (ex.: página de API → operação)"),
    Origin("sdl", "SDL", "domain/service declarados no .sdl do repo"),
    Origin("declared", "SDL do repo", "domínio/serviço declarados no .sdl do próprio repo (ou da pasta)"),
    Origin("sibling", "repo irmão", "mesmo nome-base de um repo com SDL (ex.: x-frontend e x-backend)"),
    Origin("api_calls", "chamadas de API", "o código chama, na maioria, rotas domínio/serviço deste serviço"),
    Origin("usage", "uso da lib", "biblioteca usada só por repos deste domínio/serviço"),
    Origin("handler", "handler", "classe <operação>Handler (convenção do SDK)"),
    Origin("service", "nome do serviço", "nome do serviço de API igual ao nome do repo"),
    Origin("card", "cartão", "repos relacionados no cartão do repo"),
    Origin("tag", "tag", "tag da página igual a um apelido do repo", contestable=True),
    Origin("url", "link", "link para o repo no GitHub", contestable=True),
    Origin("mention", "menção", "termo do texto igual a um apelido forte do repo", contestable=True),
)
ORIGIN_BY_NAME = {o.name: o for o in ORIGINS}
ORIGIN_ORDER = tuple(o.name for o in ORIGINS)
CONTESTABLE = frozenset(o.name for o in ORIGINS if o.contestable)
STORED = frozenset(o.name for o in ORIGINS if o.stored)
DERIVED = frozenset(o.name for o in ORIGINS if not o.stored)


def origin_rank(origin: str) -> int:
    return ORIGIN_ORDER.index(origin) if origin in ORIGIN_BY_NAME else len(ORIGIN_ORDER)


def sort_origins(origins: Any) -> list[str]:
    return sorted(set(origins), key=origin_rank)


def origins_payload() -> list[dict[str, Any]]:
    """Origens para a API/interface (rótulo, ordem, se é contestável ou gravada)."""
    return [{"name": o.name, "label": o.label, "description": o.description, "contestable": o.contestable,
             "stored": o.stored, "order": i} for i, o in enumerate(ORIGINS)]


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
        if self.origin not in ORIGIN_BY_NAME:
            raise ValueError(f"origem inválida: {self.origin}")

    @property
    def contestable(self) -> bool:
        return self.origin in CONTESTABLE
