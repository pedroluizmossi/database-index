"""Consulta do find_repos: palavras genéricas saem, o assunto fica."""

from code_index.infra.enrich_store import repo_query


def test_tira_palavras_genericas() -> None:
    assert repo_query("quem cuida do serviço de tenants") == "do de tenants"
    assert repo_query("Serviço de e-mail") == "de e-mail"
    assert repo_query("repositório backend da API de notificação") == "da de notificação"


def test_so_genericas_fica_como_veio() -> None:
    assert repo_query("serviço backend") == "serviço backend"
    assert repo_query("listBlacklistedEmails") == "listBlacklistedEmails"
