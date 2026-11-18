"""SDL e PDL da plataforma: os arquivos de contrato (`.sdl`) e de propriedades (`.pdl`) dos serviços.

- tipos de arquivo: `**/*.sdl` e `**/*.pdl` entram na indexação, com linguagem própria (o detector do CocoIndex
  não os conhece), máscara de configuração e declarações (`service`, `query`, `action`...) no cabeçalho dos
  trechos e na tabela de símbolos;
- extratores de fatos: `.sdl` → serviços de contrato (`domain/contracts.ContractService`, seção `sdl`);
  `.pdl` → propriedades configuráveis (seção `properties`); as linhas de cada um
  no texto da tarefa do cartão.

Puro (sem I/O): recebe o texto dos arquivos.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from code_index.profiles.api import CONTRACTS_SECTION, ContractService, DeclRule, FileType

# ------------------------------------------------------------------ tipos de arquivo

# Declarações de .sdl/.pdl para o cabeçalho dos trechos e a tabela de símbolos.
_SDL_DECL = re.compile(r"^[ \t]*(query|action|signal|event|record|entity|enumeration|enum|service)\s+([A-Za-z_]\w*)",
                       re.MULTILINE)
_DECLS = DeclRule(_SDL_DECL, containers=frozenset({"service"}))

FILE_TYPES = (
    FileType(language="sdl", extensions=("sdl",), globs=("**/*.sdl",), config=True, declarations=_DECLS),
    FileType(language="pdl", extensions=("pdl",), globs=("**/*.pdl",), config=True, declarations=_DECLS),
)

# ------------------------------------------------------------------ parsers

_SDL_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_SDL_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_SDL_DOMAIN = re.compile(r"^\s*domain\s+([A-Za-z_][\w-]*)", re.M)
_SDL_ITEM = re.compile(
    r"(?<![\w.:])(?:(public|private)\s+)?(?:fixed\s+)?(service|query|action|signal|event|entity)\s+([A-Za-z_]\w*)\s*(?:\([^)]*\)\s*)?\{",
)


def parse_sdl(text: str, path: str = "") -> list[ContractService]:
    """`domain x` + `service y { ... }` → operações, eventos e entidades. Arquivo sem domain/service → []."""
    clean = _SDL_COMMENT.sub("", _SDL_STRING.sub('""', text))
    dm = _SDL_DOMAIN.search(clean)
    if not dm:
        return []
    domain = dm.group(1)
    out: list[ContractService] = []
    cur: dict[str, Any] | None = None

    def close() -> None:
        if cur is not None:
            out.append(ContractService(
                domain=domain, service=cur["service"], path=path, queries=tuple(cur["queries"]),
                actions=tuple(cur["actions"]), signals=tuple(cur["signals"]), events=tuple(cur["events"]),
                entities=tuple(cur["entities"]), private=tuple(cur["private"]),
            ))

    for m in _SDL_ITEM.finditer(clean):
        visibility, kind, name = m.groups()
        if kind == "service":
            close()
            cur = {"service": name, "queries": [], "actions": [], "signals": [], "events": [],
                   "entities": [], "private": []}
            continue
        if cur is None:
            continue
        bucket = {"query": "queries", "action": "actions", "signal": "signals", "event": "events",
                  "entity": "entities"}[kind]
        if name not in cur[bucket]:
            cur[bucket].append(name)
        if visibility == "private" and kind in ("query", "action", "signal"):
            cur["private"].append(name)
    close()
    return out


_SDL_DOMAIN_LABEL = re.compile(r'^\s*domain\s+([A-Za-z_][\w-]*)\s*\(\s*"([^"\n]*)"', re.M)
_SDL_SERVICE_LABEL = re.compile(r'(?<![\w.:])service\s+([A-Za-z_]\w*)\s*\(\s*"([^"\n]*)"')


def parse_sdl_labels(text: str) -> dict[str, dict[str, str]]:
    """Rótulos legíveis do `.sdl`: `domain platform ("Plataforma")`, `service blob ("Arquivos")`."""
    clean = _SDL_COMMENT.sub("", text)
    return {
        "domains": {m.group(1).lower(): m.group(2).strip() for m in _SDL_DOMAIN_LABEL.finditer(clean) if m.group(2).strip()},
        "services": {m.group(1).lower(): m.group(2).strip() for m in _SDL_SERVICE_LABEL.finditer(clean) if m.group(2).strip()},
    }


_PDL_GROUP = re.compile(r"(?<![\w.])properties\s+([\w.-]+)\s*\{")
_PDL_PROP = re.compile(r"(?<![\w.])property\s+([\w.-]+)\s*\{([^{}]*)\}")
_PDL_TYPE = re.compile(r"^\s*type\s*:\s*(\w+)", re.M)
PDL_MAX_PROPS = 60


def parse_pdl(text: str, path: str = "") -> dict[str, Any] | None:
    """Propriedades configuráveis (`properties grupo { property nome { type: ... } }`). Sem nada → None."""
    clean = _SDL_COMMENT.sub("", _SDL_STRING.sub('""', text))
    props = []
    for m in _PDL_PROP.finditer(clean):
        t = _PDL_TYPE.search(m.group(2))
        props.append(f"{m.group(1)}:{t.group(1)}" if t else m.group(1))
    if not props:
        return None
    group = _PDL_GROUP.search(clean)
    return {"path": path, "group": group.group(1) if group else "",
            "properties": list(dict.fromkeys(props))[:PDL_MAX_PROPS]}



# ------------------------------------------------------------------ extratores de fatos

# Os .sdl/.pdl ficam em pastas de módulo (multi-módulo): a coleta desce até esta profundidade.
SDL_DEPTH = 4
_SDL_GROUP_LABELS = (("queries", "queries"), ("actions", "actions"), ("signals", "signals"),
                     ("eventos", "events"), ("entidades", "entities"))


class SdlExtractor:
    """`.sdl` → serviços de contrato (seção `sdl`). Os rótulos de domínio/serviço vão para o dono (ownership.py)."""

    section = CONTRACTS_SECTION
    max_depth = SDL_DEPTH
    label = ".sdl"

    def matches(self, name: str) -> bool:
        return name.endswith(".sdl")

    def extract(self, text: str, path: str) -> list[dict[str, Any]]:
        return [s.to_dict() for s in parse_sdl(text, path)]

    def card_lines(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        lines: list[str] = []
        for s in items:
            lines.append(f"  {s['path']}: domain={s['domain']} service={s['service']}")
            for label, key in _SDL_GROUP_LABELS:
                if s.get(key):
                    lines.append(f"     {label}: " + ", ".join(s[key]))
            if s.get("private"):
                lines.append("     (uso interno/private: " + ", ".join(s["private"]) + ")")
        return lines


class PdlExtractor:
    """`.pdl` → propriedades configuráveis do serviço (seção `properties`)."""

    section = "properties"
    max_depth = SDL_DEPTH
    label = ".pdl"

    def matches(self, name: str) -> bool:
        return name.endswith(".pdl")

    def extract(self, text: str, path: str) -> list[dict[str, Any]]:
        parsed = parse_pdl(text, path)
        return [parsed] if parsed else []

    def card_lines(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        lines: list[str] = []
        for pr in items:
            lines.append(f"  {pr['path']}: propriedades configuráveis de {pr['group'] or '?'}")
            lines.append("     " + ", ".join(pr["properties"]))
        return lines


FACT_EXTRACTORS = (SdlExtractor(), PdlExtractor())
