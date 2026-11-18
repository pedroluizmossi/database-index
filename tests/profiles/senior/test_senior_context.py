"""Perfil senior: declarações de .sdl/.pdl no cabeçalho dos trechos e na tabela de símbolos."""

from __future__ import annotations

from code_index.indexing.context import Decl, declarations


SDL = """service email {
    query healthcheck {
    }
    action sendEmail {
    }
    record emailLog {
    }
}
"""


def test_sdl_pela_extensao_com_o_perfil() -> None:
    from code_index.profiles.registry import load_profiles

    types = load_profiles(["senior"]).file_types
    d = declarations(SDL, "", "email.sdl", types)
    assert names(d) == ["service email", "query healthcheck", "action sendEmail", "record emailLog"]
    assert d[0].kind == "container" and {x.kind for x in d[1:]} == {"member"}
    assert names(declarations(SDL, "", "EMAIL.PDL", types))[0] == "service email"
    assert declarations(SDL, "", "email.sdl") == []  # sem o perfil, o formato é desconhecido



def names(decls: list[Decl], kind: str | None = None) -> list[str]:
    return [d.name for d in decls if kind is None or d.kind == kind]

