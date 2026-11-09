"""Rotas de login com Google e Microsoft ativos ao mesmo tempo (sem banco: o lifespan não roda)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from code_index.interfaces.http.app import create_app

TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def make_client(make_settings):
    def _make(**overrides) -> TestClient:
        values = {"auth_secret": "s" * 48, "public_url": "http://127.0.0.1:8765",
                  "google_client_id": "g.apps", "google_client_secret": "x", "google_allowed_emails": ["eu@x.com"],
                  "azure_tenant_id": TENANT, "azure_client_id": "az", "azure_client_secret": "y",
                  "azure_allowed_domain": "empresa.com.br"}
        values.update(overrides)
        return TestClient(create_app(make_settings(**values)), base_url="http://127.0.0.1:8765",
                          follow_redirects=False)

    return _make


def test_lista_os_dois_provedores(make_client) -> None:
    r = make_client().get("/auth/providers")
    assert r.json() == [{"name": "google", "label": "Google"}, {"name": "microsoft", "label": "Microsoft"}]


def test_login_sem_provedor_mostra_a_escolha(make_client) -> None:
    r = make_client().get("/auth/login", params={"next": "/auth/authorize?req=abc.def"})
    assert r.status_code == 200
    assert "Entrar com Google" in r.text and "Entrar com Microsoft" in r.text
    assert "provider=microsoft&amp;next=%2Fauth%2Fauthorize%3Freq%3Dabc.def" in r.text


@pytest.mark.parametrize(("provider", "host"), [("google", "accounts.google.com"),
                                                 ("microsoft", "login.microsoftonline.com")])
def test_login_redireciona_para_o_provedor(make_client, provider: str, host: str) -> None:
    r = make_client().get("/auth/login", params={"provider": provider})
    assert r.status_code == 302 and urlsplit(r.headers["location"]).netloc == host
    assert "cix_login" in r.cookies or "cix_login" in r.headers.get("set-cookie", "")


def test_um_so_provedor_vai_direto(make_client) -> None:
    r = make_client(google_client_id=None, google_client_secret=None).get("/auth/login")
    assert r.status_code == 302 and urlsplit(r.headers["location"]).netloc == "login.microsoftonline.com"


def test_provedor_nao_configurado(make_client) -> None:
    r = make_client(azure_client_id=None).get("/auth/login", params={"provider": "microsoft"})
    assert r.status_code == 503 and "não está configurado" in r.text


def test_nenhum_provedor(make_client) -> None:
    r = make_client(google_client_id=None, azure_client_id=None).get("/auth/login")
    assert r.status_code == 503 and "Nenhum login configurado" in r.text


@pytest.mark.parametrize("nxt", ["https://evil.example/", "//evil.example/x", "/ui/", "/auth/authorize?req=a//b"])
def test_next_so_para_o_consentimento(make_client, nxt: str) -> None:
    r = make_client().get("/auth/login", params={"next": nxt})
    assert "next=" not in r.text  # destino estranho é descartado (open redirect)


def test_login_microsoft_guarda_provedor_e_destino_no_cookie(make_client) -> None:
    c = make_client()
    r = c.get("/auth/login", params={"provider": "microsoft", "next": "/auth/authorize?req=abc"})
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/auth/callback"] and q["domain_hint"] == ["empresa.com.br"]
    from itsdangerous import URLSafeTimedSerializer

    data = URLSafeTimedSerializer("s" * 48, salt="code-index-login").loads(r.cookies["cix_login"])
    assert (data["p"], data["x"]) == ("microsoft", "/auth/authorize?req=abc")
