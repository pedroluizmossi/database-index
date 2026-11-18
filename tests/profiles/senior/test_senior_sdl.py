"""Perfil senior: SDL e PDL (parsers, tipos de arquivo, extratores de fatos e texto da tarefa do cartão)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from code_index.domain.file_patterns import DEFAULT_INCLUDE
from code_index.domain.repo_card import build_facts, render_card_task
from code_index.profiles import registry
from code_index.profiles.registry import load_profiles
from code_index.profiles.senior.sdl import FACT_EXTRACTORS, parse_pdl, parse_sdl


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


CATALOG = """apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: email-backend
spec:
  owner: arquitetura
"""


SDL = '''domain platform

service email {
    // comentário com query falsa { }
    endpoints {
        "Query para o SRE: query fake {"
        private query healthcheck {
            in {}
            out { status: upDown }
        }
        "Envio do e-mail"
        private action sendEmail {
            in { query: string }
        }
        public query listBlacklistedEmails { in {} out {} }
        action testEmail { in {} out {} }
        signal configChanged { in {} }
    }
    events {
        public event emailSent { payload: string }
        event emailSentError { }
    }
    fixed entity emailLog { id: string }
    record content { body: string }
}
'''


def test_parse_sdl_operacoes_eventos_entidades() -> None:
    [s] = parse_sdl(SDL, "email.sdl")
    assert (s.domain, s.service, s.path) == ("platform", "email", "email.sdl")
    assert s.queries == ("healthcheck", "listBlacklistedEmails")
    assert s.actions == ("sendEmail", "testEmail")
    assert s.signals == ("configChanged",)
    assert s.events == ("emailSent", "emailSentError")
    assert s.entities == ("emailLog",)
    assert s.private == ("healthcheck", "sendEmail")


def test_parse_sdl_sem_domain_ou_service() -> None:
    assert parse_sdl("service x { query a { } }") == []
    assert parse_sdl("domain d\nrecord r { }") == []


def test_parse_sdl_varios_services() -> None:
    out = parse_sdl('domain hcm ("RH")\nservice a ( "Rótulo" ) { query q1 { } }\nservice b { action a1 { } }')
    assert [(s.service, s.queries, s.actions) for s in out] == [("a", ("q1",), ()), ("b", (), ("a1",))]



def test_build_facts() -> None:
    files = {
        "catalog-info.yaml": CATALOG,
        "email.sdl": SDL,
        "pom.xml": "<project><artifactId>email-parent</artifactId></project>",
        "java/impl/pom.xml": "<project><artifactId>email-impl</artifactId></project>",
    }
    paths = ["README.md", "java/impl/src/main/A.java", "java/impl/src/main/resources/db/migration/V1.sql",
             "java/impl/src/test/ATest.java", ".github/workflows/ci.yml", "Dockerfile"]
    facts = build_facts(files, paths, "java", FACT_EXTRACTORS).to_dict()
    assert facts["language"] == "java"
    assert facts["catalog"]["owner"] == "arquitetura"
    assert facts["artifacts"] == ["email-parent", "email-impl"] or facts["artifacts"] == ["email-impl", "email-parent"]
    assert [s["service"] for s in facts["sdl"]] == ["email"]
    assert set(facts["has"]) >= {"db-migrations", "docker", "ci-github"}


def test_render_card_task_tem_instrucoes_antes_dos_dados() -> None:
    facts = build_facts({"email.sdl": SDL}, ["email.sdl"], "java", FACT_EXTRACTORS).to_dict()
    text = render_card_task(task_id="t1", repo="o__snr-email-backend", commit="abcdef123", remaining=3,
                            material={"facts": facts, "readme": "# Email", "symbols": ["SendEmailHandler"],
                                      "suggested_files": ["java/A.java"]},
                            candidates=["o__snr-email-frontend"], extractors=FACT_EXTRACTORS)
    assert text.index("INSTRUÇÕES") < text.index(" DADOS ") < text.index("README")
    assert "enrich submit t1" in text and "commit abcdef1" in text
    assert "domain=platform service=email" in text and "SendEmailHandler" in text
    assert "related_repos lista" in text and "grep -E 'Foo|Bar'" in text
    assert '"properties"' not in text  # schema compacto, não o JSON Schema inteiro
    assert "sem SDL, classes de nome genérico" in text  # o formato de contrato do perfil (texto de antes)



PDL = """properties platform.email {
    property smtp.oauth.provider {
         type: string
         label: "x { y }"
         regEx: "(Google|Microsoft)"
    }
    property smtp.throttling.minute { type: integer
        defaultValue: "0" }
    property smtp.host { transient: true }
}
"""


def test_parse_pdl() -> None:
    assert parse_pdl(PDL, "email.pdl") == {
        "path": "email.pdl", "group": "platform.email",
        "properties": ["smtp.oauth.provider:string", "smtp.throttling.minute:integer", "smtp.host"],
    }
    assert parse_pdl("nada aqui") is None
    facts = build_facts({"email.pdl": PDL}, ["email.pdl"], None, FACT_EXTRACTORS).to_dict()
    assert facts["properties"][0]["group"] == "platform.email"



def test_senior_mesmo_include_de_antes() -> None:
    antes = DEFAULT_INCLUDE[:DEFAULT_INCLUDE.index("**/*.properties")] + ["**/*.sdl", "**/*.pdl"] \
        + DEFAULT_INCLUDE[DEFAULT_INCLUDE.index("**/*.properties"):]
    assert list(load_profiles(["senior"]).default_include) == antes  # mesma ordem: a chave de memo não muda


def test_senior_tipos_e_extratores() -> None:
    active = load_profiles(["senior"])
    types = active.file_types
    assert (types.language("a.sdl"), types.language("a.PDL")) == ("sdl", "pdl")
    assert types.config_languages == {"sdl", "pdl"}
    assert [(x.section, x.max_depth) for x in active.fact_extractors] == [("sdl", 4), ("properties", 4)]
    sdl = 'domain demo ("Demo")\nservice shop ("Loja") {\n  private query listItems { }\n  action addItem { }\n}\n'
    facts = build_facts({"x/shop.sdl": sdl}, ["x/shop.sdl"], None, active.fact_extractors).to_dict()
    assert facts["sdl"][0]["queries"] == ["listItems"] and facts["sdl"][0]["private"] == ["listItems"]
    lines = active.fact_extractors[0].card_lines(facts["sdl"])
    assert lines == ["  x/shop.sdl: domain=demo service=shop", "     queries: listItems", "     actions: addItem",
                     "     (uso interno/private: listItems)"]
    rules = active.ownership_rules[0]
    assert rules.collect([], {"x/shop.sdl": sdl}, lambda rel: None)["sdl_labels"] == {
        "domains": {"demo": "Demo"}, "services": {"shop": "Loja"}}



def test_linguagem_de_sdl_e_pdl_vem_do_perfil() -> None:
    from code_index.indexing.content import file_language, redact
    from code_index.profiles.registry import load_profiles

    types = load_profiles(["senior"]).file_types
    assert file_language("authentication.sdl", types) == "sdl"
    assert file_language("authentication.PDL", types) == "pdl"
    assert file_language("App.java", types) == "java"
    assert file_language("LICENSE", types) == ""
    # sem o perfil, o núcleo não conhece o formato
    assert file_language("authentication.sdl") == ""
    # linguagem de config do perfil: valor sem aspas é mascarado
    assert redact("password: s3cr3tValue9\n", "sdl", types)[0] == "password: ***\n"
    assert redact("password: s3cr3tValue9\n", "sdl")[0] == "password: s3cr3tValue9\n"

