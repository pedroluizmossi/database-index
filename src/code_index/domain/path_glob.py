"""Globs de caminho (pura). Uma só semântica para a busca (SQL) e para o grep (Python).

    *        qualquer coisa dentro de um nível (não atravessa '/')
    ?        um caractere (não '/')
    **       qualquer coisa, atravessando diretórios
    **/      zero ou mais diretórios: 'src/**/*.ts' casa 'src/main.ts' e 'src/app/x.ts'
    sem '/'  casa o nome em qualquer pasta: '*.ts' casa 'a/b/c.ts' (como no .gitignore)
    pasta    'src/app' casa também o que está dentro dela ('src/app/x.ts')

A regex gerada usa só o que o Python e o Postgres (operador ~) entendem igual.
"""

from __future__ import annotations

import re

_SPECIAL = set(".^$+{}[]()|\\")


def _escape(ch: str) -> str:
    return "\\" + ch if ch in _SPECIAL else ch


def glob_regex(glob: str) -> str:
    """Regex ancorada (^...$) equivalente ao glob de caminho."""
    g = glob.strip().lstrip("/")
    if not g:
        raise ValueError("glob de caminho vazio")
    out: list[str] = []
    i = 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(_escape(g[i]))
            i += 1
    prefix = "^" if "/" in g.rstrip("/") else "^(?:.*/)?"
    return prefix + "".join(out).rstrip("/") + "(?:/.*)?$"


def glob_matcher(glob: str | None):  # type: ignore[no-untyped-def]
    """Função caminho → bool (sem glob, casa tudo)."""
    if not glob:
        return lambda _p: True
    rx = re.compile(glob_regex(glob))
    return lambda p: rx.match(p) is not None
