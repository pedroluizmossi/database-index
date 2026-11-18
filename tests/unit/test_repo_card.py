"""Cartão do repo: catalog-info, fatos, README, candidatos, validação e texto da tarefa (núcleo, sem perfil; o SDL do
perfil senior fica em tests/profiles/senior/)."""

from __future__ import annotations

from code_index.domain.repo_card import (
    build_facts,
    clean_readme,
    material_hash,
    parse_catalog_info,
    parse_payload,
    related_candidates,
    render_card_task,
    top_modules,
    validate_card,
)


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
        "pom.xml": "<project><artifactId>email-parent</artifactId></project>",
        "java/impl/pom.xml": "<project><artifactId>email-impl</artifactId></project>",
    }
    paths = ["README.md", "java/impl/src/main/A.java", "java/impl/src/main/resources/db/migration/V1.sql",
             "java/impl/src/test/ATest.java", ".github/workflows/ci.yml", "Dockerfile"]
    facts = build_facts(files, paths, "java").to_dict()
    assert facts["language"] == "java"
    assert facts["catalog"]["owner"] == "arquitetura"
    assert facts["artifacts"] == ["email-parent", "email-impl"] or facts["artifacts"] == ["email-impl", "email-parent"]
    assert facts["sdl"] == []  # sem perfil, nenhum extrator de contrato
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




def test_render_card_task_sem_perfil_fala_em_contrato_generico() -> None:
    facts = build_facts({}, ["README.md"], "java").to_dict()
    text = render_card_task(task_id="t1", repo="o__pedidos-backend", commit="abcdef123", remaining=0,
                            material={"facts": facts, "readme": "# Pedidos", "symbols": ["PedidoService"],
                                      "suggested_files": []}, candidates=[])
    assert text.index("INSTRUÇÕES") < text.index(" DADOS ") < text.index("README")
    assert "enrich submit t1" in text and "commit abcdef1" in text and "PedidoService" in text
    assert "sem contrato de API, classes de nome genérico" in text and "SDL" not in text
