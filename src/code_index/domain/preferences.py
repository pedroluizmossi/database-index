"""Preferências por pessoa: **registro único** das configs (puro, sem I/O).

Cada config é declarada uma vez em `PREFS`; a API e a UI leem daqui. O banco guarda só o que a pessoa
mudou (`user_settings`), e o padrão é resolvido na leitura. Assim:
  - config nova       = entrada nova em `PREFS` (sem migration);
  - mudar o padrão    = editar o registro (só afeta quem não escolheu);
  - remover           = tirar do registro (linhas órfãs são ignoradas);
  - renomear/mudar tipo = migration de dados.

Preferência **não é autorização**: nada aqui concede acesso. Scopes vêm de `domain/auth`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

AppliesTo = Literal["ui", "search"]  # "ui": só a interface web; "search": vale também para os tokens da pessoa


@dataclass(frozen=True)
class Pref:
    key: str
    label: str
    default: Any
    parse: Callable[[Any], Any]          # valida e normaliza; levanta ValueError
    applies_to: AppliesTo = "ui"
    choices: tuple[str, ...] | None = None
    choice_labels: tuple[str, ...] | None = None   # rótulos das opções, na mesma ordem
    minimum: int | None = None
    maximum: int | None = None

    @property
    def type(self) -> str:
        return "choice" if self.choices else "bool" if isinstance(self.default, bool) else (
            "int" if isinstance(self.default, int) else "text")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type, "default": self.default,
                               "applies_to": self.applies_to}
        if self.choices:
            labels = self.choice_labels or self.choices
            out["choices"] = [{"value": v, "label": lb} for v, lb in zip(self.choices, labels, strict=True)]
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        return out


def choice(key: str, label: str, choices: tuple[str, ...], default: str, *, labels: tuple[str, ...] | None = None,
           applies_to: AppliesTo = "ui") -> Pref:
    def parse(value: Any) -> str:
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"{key}: use um de {', '.join(choices)}")
        return value

    return Pref(key, label, default, parse, applies_to, choices=choices, choice_labels=labels)


def integer(key: str, label: str, default: int, *, minimum: int, maximum: int,
            applies_to: AppliesTo = "ui") -> Pref:
    def parse(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{key}: use um inteiro entre {minimum} e {maximum}")
        return value

    return Pref(key, label, default, parse, applies_to, minimum=minimum, maximum=maximum)


def boolean(key: str, label: str, default: bool, *, applies_to: AppliesTo = "ui") -> Pref:
    def parse(value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{key}: use true ou false")
        return value

    return Pref(key, label, default, parse, applies_to)


def _registry(*prefs: Pref) -> dict[str, Pref]:
    return {p.key: p for p in prefs}


PREFS: dict[str, Pref] = _registry(
    choice("ui.theme", "Tema da interface", ("system", "light", "dark"), "system",
           labels=("Do sistema", "Claro", "Escuro")),
    integer("ui.results_per_page", "Resultados por busca", 8, minimum=1, maximum=50),  # faixa do top_k da API
)


def validate_changes(
    changes: Mapping[str, Any], prefs: Mapping[str, Pref] = PREFS,
) -> tuple[dict[str, Any], list[str]]:
    """Separa um PATCH em (valores a gravar, chaves a apagar). `None` = voltar ao padrão.
    Chave desconhecida ou valor inválido → ValueError (nada é gravado)."""
    to_set: dict[str, Any] = {}
    to_unset: list[str] = []
    for key, value in changes.items():
        pref = prefs.get(key)
        if pref is None:
            raise ValueError(f"configuração desconhecida: {key}")
        if value is None:
            to_unset.append(key)
        else:
            to_set[key] = pref.parse(value)
    return to_set, to_unset


def resolve(
    stored: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
    prefs: Mapping[str, Pref] = PREFS,
) -> dict[str, Any]:
    """Padrão do código → valor da pessoa → parâmetro explícito da chamada.
    Valor guardado que não vale mais (config mudou) cai no padrão; chave fora do registro é ignorada.
    Override explícito inválido → ValueError."""
    out: dict[str, Any] = {}
    for key, pref in prefs.items():
        value = pref.default
        if key in stored:
            try:
                value = pref.parse(stored[key])
            except ValueError:
                pass
        if overrides and overrides.get(key) is not None:
            value = pref.parse(overrides[key])
        out[key] = value
    return out


def schema(prefs: Mapping[str, Pref] = PREFS) -> list[dict[str, Any]]:
    return [p.to_dict() for p in prefs.values()]
