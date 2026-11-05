"""Tokenização para busca em código (pura). A mesma regra vale na indexação e na consulta.

Identificadores viram o termo inteiro + as partes:
    HmacServiceImpl  -> hmacserviceimpl hmac service impl
    get_user_name    -> get_user_name get user name
    XMLHttpRequest   -> xmlhttprequest xml http request
Assim a busca acha tanto o nome exato quanto pedaços dele ("Hmac" acha HmacServiceImpl).

Palavras de comentários e textos perdem os acentos ("validação" -> "validacao"), para a busca
achar o texto escrito com ou sem acento. A consulta também procura a forma acentuada (o que foi
indexado antes desta regra) e o singular de palavras no plural ("tokens" acha "token").
"""

from __future__ import annotations

import re
import unicodedata
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


def fold(word: str) -> str:
    """Sem acentos: 'validação' -> 'validacao'."""
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


_STOP_FOLDED = frozenset(fold(w) for w in STOPWORDS)
# plural -> singular (formas com e sem acento); o resto perde só o "s" final
_PLURALS = (("ções", "ção"), ("coes", "cao"), ("ões", "ão"), ("oes", "ao"), ("ães", "ão"), ("aes", "ao"), ("ies", "y"))


def singular(word: str) -> str | None:
    """Singular provável de uma palavra (pt/en), ou None se não parecer plural."""
    for suffix, repl in _PLURALS:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + repl
    if len(word) >= 5 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return None


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
            terms.append(fold(word.lower()))
        if len(terms) >= MAX_TERMS:
            break
    return " ".join(terms[:MAX_TERMS])


def query_terms(query: str) -> list[str]:
    """Termos de uma consulta: mesma tokenização, sem stopwords e sem repetição (ordem preservada).

    Em frases, cada palavra vem também na forma acentuada original (casa o que foi indexado
    antes de os acentos saírem) e no singular, quando parece plural.
    """
    natural = query_kind(query) == "natural"
    accented = {fold(w.lower()): w.lower() for w in _WORD.findall(query) if fold(w.lower()) != w.lower()}
    out: list[str] = []

    def add(term: str | None) -> None:
        if term and len(term) >= 2 and fold(term) not in _STOP_FOLDED and term not in out:
            out.append(term)

    for term in code_terms(query).split():
        add(term)
        add(accented.get(term))
        if natural:
            add(singular(term))
            add(singular(accented[term]) if term in accented else None)
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

    Frases: o vetor entende a intenção; termos reforçam. Identificadores: o termo exato manda.
    Calibrado com `code-index eval --grid` (evals/queries.json): em frases, 1.0/0.7 acertou mais
    na 1ª posição que 1.0/0.4; dar mais peso aos termos que ao vetor piorou bastante.
    """
    return (0.6, 1.0) if kind == "identifier" else (1.0, 0.7)
