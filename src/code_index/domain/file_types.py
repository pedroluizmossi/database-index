"""Tipos de arquivo que um perfil de organização acrescenta ao núcleo (puro, sem I/O).

O núcleo conhece as linguagens do detector do CocoIndex (tree-sitter) e as regras de declaração de
`indexing/context.py`. Um formato próprio de uma empresa (ex.: um arquivo de contrato de API) chega por aqui:
os globs que entram na indexação, a linguagem pela extensão, se é um arquivo de configuração (máscara de
`chave: valor` sem aspas) e a regra das declarações que vão para o cabeçalho do trecho e para a tabela de
símbolos. Os perfis entregam `FileType`s (`OrgProfile.file_types`); o núcleo recebe o conjunto combinado
(`FileTypes`) como dado.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property


@dataclass(frozen=True)
class DeclRule:
    """Declarações de um tipo de arquivo: `pattern` (multilinha) com dois grupos, a palavra-chave e o nome.
    O nome da declaração é `"<palavra> <nome>"` e ela fica na linha onde o casamento começa; palavras-chave em
    `containers` envolvem as outras (ex.: `service`), as demais são membros."""

    pattern: re.Pattern[str]
    containers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FileType:
    """Um formato de arquivo de um perfil."""

    language: str                       # nome da linguagem (coluna `language` dos trechos e símbolos)
    extensions: tuple[str, ...]         # sem ponto; comparadas em minúsculas
    globs: tuple[str, ...]              # padrões somados ao include padrão dos repos
    config: bool = False                # `chave: valor` literal: a máscara de configuração vale para ele
    declarations: DeclRule | None = None

    def __post_init__(self) -> None:
        if not self.language or not self.extensions:
            raise ValueError("tipo de arquivo sem linguagem ou sem extensão")
        if any(e.startswith(".") or e != e.lower() for e in self.extensions):
            raise ValueError(f"tipo de arquivo {self.language!r}: extensões sem ponto e em minúsculas")


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@dataclass(frozen=True)
class FileTypes:
    """Os tipos de arquivo dos perfis ativos, combinados. Vazio = só o que o núcleo conhece.

    Extensão repetida: vale o primeiro tipo que a declara (ordem dos perfis em CODE_INDEX_PROFILES)."""

    types: tuple[FileType, ...] = field(default=())

    @classmethod
    def combine(cls, groups: Iterable[Iterable[FileType]]) -> FileTypes:
        return cls(tuple(t for group in groups for t in group))

    @cached_property
    def _by_extension(self) -> dict[str, FileType]:
        out: dict[str, FileType] = {}
        for t in self.types:
            for ext in t.extensions:
                out.setdefault(ext, t)
        return out

    def for_extension(self, ext: str) -> FileType | None:
        """O tipo que declara a extensão (sem ponto; sem diferenciar maiúsculas), ou None."""
        return self._by_extension.get(ext.lower())

    def for_filename(self, filename: str) -> FileType | None:
        """O tipo do arquivo pela extensão (sem diferenciar maiúsculas), ou None."""
        return self.for_extension(_extension(filename))

    def language(self, filename: str) -> str:
        """Linguagem do arquivo pela extensão ("" se nenhum tipo a declara)."""
        t = self.for_filename(filename)
        return t.language if t else ""

    @cached_property
    def config_languages(self) -> frozenset[str]:
        return frozenset(t.language for t in self.types if t.config)

    @cached_property
    def globs(self) -> tuple[str, ...]:
        """Globs de inclusão de todos os tipos, na ordem, sem repetir."""
        return tuple(dict.fromkeys(g for t in self.types for g in t.globs))


NO_FILE_TYPES = FileTypes()
