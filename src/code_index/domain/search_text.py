"""Tokenização para busca em código (pura). A mesma regra vale na indexação e na consulta.

Identificadores viram o termo inteiro + as partes:
    HmacServiceImpl  -> hmacserviceimpl hmac service impl
    get_user_name    -> get_user_name get user name
    XMLHttpRequest   -> xmlhttprequest xml http request
Assim a busca acha tanto o nome exato quanto pedaços dele ("Hmac" acha HmacServiceImpl).
"""

from __future__ import annotations

import re
from typing import Literal

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Partes de um identificador: siglas (XML), palavras capitalizadas (Http), minúsculas e números.
_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_WORD = re.compile(r"\w+", re.UNICODE)
MAX_TERMS = 6000  # trechos têm ~1.500 caracteres; isso só corta casos patológicos

STOPWORDS = frozenset("""
a o e é de da do das dos em no na nos nas um uma uns umas para por com sem que se como onde qual quais
ao aos à às ou mais menos ser está são foi feito feita sobre entre seu sua seus suas isso este esta
the an and or of to in on for with from by is are be was how where what which when does do this that
""".split())

QueryKind = Literal["identifier", "natural"]


def split_identifier(ident: str) -> list[str]:
    """Partes minúsculas de um identificador (camelCase, PascalCase, snake_case, SIGLAS)."""
    parts: list[str] = []
    for chunk in ident.split("_"):
        parts.extend(p.lower() for p in _PARTS.findall(chunk))
    return [p for p in parts if len(p) >= 2 or p.isdigit()]


def _ident_terms(ident: str) -> list[str]:
    full = ident.lower().strip("_")
    parts = split_identifier(ident)
    out = [full] if len(full) >= 2 else []
    if len(parts) > 1 or (parts and parts[0] != full):
        out.extend(parts)
    return out


def code_terms(text: str) -> str:
    """Texto de busca de um trecho: cada identificador inteiro + suas partes, na ordem em que aparecem
    (mantém a frequência dos termos, que o BM25 usa). Palavras acentuadas de comentários entram como estão."""
    terms: list[str] = []
    for m in _WORD.finditer(text):
        word = m.group(0)
        if _IDENT.fullmatch(word):
            terms.extend(_ident_terms(word))
        elif len(word) >= 2 and not word.isdigit():
            terms.append(word.lower())
        if len(terms) >= MAX_TERMS:
            break
    return " ".join(terms[:MAX_TERMS])


def query_terms(query: str) -> list[str]:
    """Termos de uma consulta: mesma tokenização, sem stopwords e sem repetição (ordem preservada)."""
    out: list[str] = []
    for term in code_terms(query).split():
        if term not in STOPWORDS and term not in out and (len(term) >= 2):
            out.append(term)
    return out


def query_kind(query: str) -> QueryKind:
    """'identifier' quando a consulta parece um nome de código (HmacServiceImpl, get_user,
    pkg.Classe, Classe::metodo, metodo()); 'natural' para frases."""
    q = query.strip()
    if not q or " " in q:
        return "natural"
    if re.search(r"[a-z][A-Z]|_|\.|::|\(|\)", q) or q.isupper():
        return "identifier"
    return "natural"


def fusion_weights(kind: QueryKind) -> tuple[float, float]:
    """(peso da busca semântica, peso da busca por termos) na fusão RRF.

    Frases: o vetor entende a intenção; termos só desempatam. Identificadores: o termo exato manda.
    """
    return (0.6, 1.0) if kind == "identifier" else (1.0, 0.4)
