"""Cartão do repo: parsers (SDL, catalog-info), fatos, README, candidatos, validação e texto da tarefa."""

from __future__ import annotations

from code_index.domain.repo_card import (
    build_facts,
    clean_readme,
    material_hash,
    parse_catalog_info,
    parse_pdl,
    parse_payload,
    parse_sdl,
    related_candidates,
    render_card_task,
    top_modules,
    validate_card,
)

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


CATALOG = """apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: email-backend
  description: ''
  tags: [email, platform]
  annotations:
    argocd/app-name: email-prod-platform
    outra/anotacao: ignorada
spec:
  lifecycle: production
  owner: arquitetura
  type: service
"""


def test_parse_catalog_info() -> None:
    assert parse_catalog_info(CATALOG) == {
        "kind": "Component", "name": "email-backend", "tags": ["email", "platform"],
        "type": "service", "owner": "arquitetura", "lifecycle": "production",
        "annotations": {"argocd/app-name": "email-prod-platform"},
    }
    assert parse_catalog_info("::: não é yaml") == {}
    assert parse_catalog_info("- uma lista") == {}


def test_build_facts() -> None:
    files = {
        "catalog-info.yaml": CATALOG,
        "email.sdl": SDL,
        "pom.xml": "<project><artifactId>email-parent</artifactId></project>",
        "java/impl/pom.xml": "<project><artifactId>email-impl</artifactId></project>",
    }
    paths = ["README.md", "java/impl/src/main/A.java", "java/impl/src/main/resources/db/migration/V1.sql",
             "java/impl/src/test/ATest.java", ".github/workflows/ci.yml", "Dockerfile"]
    facts = build_facts(files, paths, "java").to_dict()
    assert facts["language"] == "java"
    assert facts["catalog"]["owner"] == "arquitetura"
    assert facts["artifacts"] == ["email-parent", "email-impl"] or facts["artifacts"] == ["email-impl", "email-parent"]
    assert [s["service"] for s in facts["sdl"]] == ["email"]
    assert set(facts["has"]) >= {"db-migrations", "docker", "ci-github"}


def test_top_modules_ignora_testes_e_ocultas() -> None:
    mods = top_modules(["src/a.py", "src/b/c.py", "tests/t.py", ".github/x.yml", "raiz.txt"])
    assert mods == ["src", "src/b"]


def test_clean_readme_tira_badges_e_corta() -> None:
    text = ("[![Quality](http://x/badge)](http://x)\n![img](a.png)\n<!-- oculto -->\n# Email\n\n\n\nServiço.\n")
    assert clean_readme(text) == "# Email\n\nServiço."
    longo = "linha\n" * 2000
    assert clean_readme(longo, max_chars=100).endswith("[... README cortado]")


def test_related_candidates_por_nome_e_sdl() -> None:
    repos = ["o__snr-email-backend", "o__snr-email-frontend", "o__snr-email-blocklist", "o__snr-auth-backend",
             "o__mailer"]
    facts = {"o__snr-email-backend": {"sdl": [{"service": "email"}]}, "o__mailer": {"sdl": [{"service": "email"}]}}
    out = related_candidates("o__snr-email-backend", repos, facts)
    assert set(out) == {"o__snr-email-frontend", "o__snr-email-blocklist", "o__mailer"}
    assert "o__snr-auth-backend" not in out


def test_material_hash_estavel() -> None:
    assert material_hash({"a": 1, "b": [1, 2]}) == material_hash({"b": [1, 2], "a": 1})
    assert material_hash({"a": 1}) != material_hash({"a": 2})


def _valid() -> dict:
    return {
        "summary": "Serviço da plataforma que envia e-mails dos tenants e mantém blocklist.",
        "kind": "service",
        "capabilities": ["envio de e-mail", "blocklist"],
        "domain_tags": ["E-mail", "notificação"],
        "glossary": ["blacklist = blocklist"],
        "related_repos": [{"repo": "snr-email-frontend", "basis": "nome", "why": "UI"}],
        "entrypoints": ["./java/A.java"],
        "evidencias": [],
    }


def test_validate_card_ok_normaliza() -> None:
    card, errors = validate_card(_valid(), candidates=["o__snr-email-frontend"], path_exists=lambda p: p == "java/A.java")
    assert errors == []
    assert card is not None
    assert card["related_repos"] == [{"repo": "o__snr-email-frontend", "basis": "nome", "why": "UI"}]
    assert card["entrypoints"] == ["java/A.java"]
    assert card["domain_tags"] == ["e-mail", "notificação"]


def test_validate_card_entrypoint_com_ponto_inicial_e_preservado() -> None:
    card, errors = validate_card(_valid() | {"entrypoints": [".github/workflows/ci.yml"]},
                                 candidates=["o__snr-email-frontend"],
                                 path_exists=lambda p: p == ".github/workflows/ci.yml")
    assert errors == []
    assert card is not None and card["entrypoints"] == [".github/workflows/ci.yml"]


def test_validate_card_erros_legiveis() -> None:
    raw = _valid() | {"kind": "banco", "summary": "curto", "extra": 1,
                      "related_repos": [{"repo": "inventado", "basis": "nome", "why": "?"},
                                        {"repo": "snr-email-frontend", "basis": "acho", "why": "?"}],
                      "entrypoints": ["nao/existe.java"]}
    card, errors = validate_card(raw, candidates=["o__snr-email-frontend"], path_exists=lambda p: False)
    assert card is None
    text = "\n".join(errors)
    for expected in ("related_repos[1].basis", "campos desconhecidos: extra", "summary", "kind", '"inventado" não está nos CANDIDATOS',
                     "entrypoints: não existem no repo: nao/existe.java"):
        assert expected in text
    assert validate_card([], candidates=[], path_exists=lambda p: True) == (None, ["a entrega deve ser um objeto JSON"])


def test_validate_card_relacionado_fora_dos_candidatos_so_com_codigo() -> None:
    rel = {"repo": "go-legacy-gateway-client", "why": "o mock implementa o Client dele"}
    known = ["o__go-legacy-gateway-client"]
    card, errors = validate_card(_valid() | {"related_repos": [rel | {"basis": "codigo"}], "entrypoints": []},
                                 candidates=[], known_repos=known, path_exists=lambda p: True)
    assert errors == [] and card is not None and card["related_repos"][0]["repo"] == "o__go-legacy-gateway-client"
    _, errors = validate_card(_valid() | {"related_repos": [rel | {"basis": "nome"}], "entrypoints": []},
                              candidates=[], known_repos=known, path_exists=lambda p: True)
    assert "fora da lista" in errors[0]


def test_parse_payload_aceita_cerca() -> None:
    assert parse_payload('```json\n{"a": 1}\n```') == {"a": 1}
    try:
        parse_payload("{a: 1}")
    except ValueError as e:
        assert "JSON inválido" in str(e)
    else:
        raise AssertionError("deveria falhar")


def test_render_card_task_tem_instrucoes_antes_dos_dados() -> None:
    facts = build_facts({"email.sdl": SDL}, ["email.sdl"], "java").to_dict()
    text = render_card_task(task_id="t1", repo="o__snr-email-backend", commit="abcdef123", remaining=3,
                            material={"facts": facts, "readme": "# Email", "symbols": ["SendEmailHandler"],
                                      "suggested_files": ["java/A.java"]},
                            candidates=["o__snr-email-frontend"])
    assert text.index("INSTRUÇÕES") < text.index(" DADOS ") < text.index("README")
    assert "enrich submit t1" in text and "commit abcdef1" in text
    assert "domain=platform service=email" in text and "SendEmailHandler" in text
    assert "related_repos lista" in text and "grep -E 'Foo|Bar'" in text
    assert '"properties"' not in text  # schema compacto, não o JSON Schema inteiro


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
    facts = build_facts({"email.pdl": PDL}, ["email.pdl"], None).to_dict()
    assert facts["properties"][0]["group"] == "platform.email"
