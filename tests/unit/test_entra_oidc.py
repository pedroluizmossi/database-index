"""Validação do id_token da Microsoft (Entra ID) com uma chave RSA gerada no teste (sem rede).

Autorização: um único tenant (GUID), sem contas convidadas; e-mail listado OU domínio configurado.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from code_index.infra.entra_oidc import EntraOIDC
from code_index.infra.login import configured_providers, open_provider
from code_index.infra.oidc import LoginError

TENANT = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT = "99999999-2222-3333-4444-555555555555"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ISS = f"https://login.microsoftonline.com/{TENANT}/v2.0"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=KEY.public_key())


def make_token(**overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISS, "aud": CLIENT_ID, "sub": "abc", "iat": now, "exp": now + 600, "tid": TENANT, "oid": "o1",
        "preferred_username": "Ana@Empresa.com.br", "name": "Ana", "nonce": "n1", "ver": "2.0",
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, KEY, algorithm="RS256")


@pytest.fixture
def make_oidc(make_settings):
    def _make(**overrides: Any) -> EntraOIDC:
        values: dict[str, Any] = {"azure_tenant_id": TENANT, "azure_client_id": CLIENT_ID,
                                  "azure_client_secret": "x", "public_url": "https://code-index.example.com"}
        values.update(overrides)
        return EntraOIDC(make_settings(**values), jwks=FakeJWKS())  # type: ignore[arg-type]

    return _make


async def test_membro_do_dominio_entra(make_oidc) -> None:
    ident = await make_oidc(azure_allowed_domain="empresa.com.br").verify_id_token(make_token(), nonce="n1")
    assert (ident.email, ident.provider) == ("ana@empresa.com.br", "microsoft")


async def test_claim_email_tem_preferencia(make_oidc) -> None:
    oidc = make_oidc(azure_allowed_emails=["ana.silva@empresa.com.br"])
    ident = await oidc.verify_id_token(make_token(email="ana.silva@empresa.com.br"), nonce="n1")
    assert ident.email == "ana.silva@empresa.com.br"


async def test_admin_entra_mesmo_sem_estar_na_lista(make_oidc) -> None:
    oidc = make_oidc(admin_emails=["ana@empresa.com.br"])
    assert (await oidc.verify_id_token(make_token(), nonce="n1")).email == "ana@empresa.com.br"


@pytest.mark.parametrize(
    ("claims", "msg"),
    [
        ({"tid": OTHER_TENANT}, "outro tenant"),
        ({"iss": f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"}, "emissor"),
        ({"idp": "https://sts.windows.net/" + OTHER_TENANT + "/"}, "convidadas"),   # guest de outro tenant
        ({"idp": "live.com"}, "convidadas"),                                         # guest com conta pessoal
        ({"preferred_username": "ana@outra.com"}, "não tem acesso"),
        ({"preferred_username": None}, "não informou o e-mail"),
        ({"nonce": "outro"}, "nonce"),
        ({"aud": "outro-app"}, "inválido"),
    ],
)
async def test_token_recusado(make_oidc, claims: dict[str, Any], msg: str) -> None:
    oidc = make_oidc(azure_allowed_domain="empresa.com.br")
    with pytest.raises(LoginError, match=msg):
        await oidc.verify_id_token(make_token(**claims), nonce="n1")


async def test_dominio_nao_aceita_sufixo_parecido(make_oidc) -> None:
    oidc = make_oidc(azure_allowed_domain="empresa.com.br")
    with pytest.raises(LoginError, match="não tem acesso"):
        await oidc.verify_id_token(make_token(preferred_username="x@falsa-empresa.com.br.evil"), nonce="n1")
    with pytest.raises(LoginError, match="não tem acesso"):
        await oidc.verify_id_token(make_token(preferred_username="x@sub.naoempresa.com.br"), nonce="n1")


def test_url_de_login_usa_o_tenant(make_oidc) -> None:
    start = make_oidc(azure_allowed_domain="empresa.com.br").start()
    url = urlsplit(start.url)
    q = parse_qs(url.query)
    assert url.netloc == "login.microsoftonline.com" and url.path == f"/{TENANT}/oauth2/v2.0/authorize"
    assert q["redirect_uri"] == ["https://code-index.example.com/auth/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["nonce"] == [start.nonce]
    assert q["domain_hint"] == ["empresa.com.br"]


@pytest.mark.parametrize("tenant", ["common", "organizations", "consumers", "empresa.com.br"])
def test_tenant_precisa_ser_guid(make_oidc, tenant: str) -> None:
    with pytest.raises(RuntimeError, match="GUID"):
        make_oidc(azure_tenant_id=tenant, azure_allowed_domain="empresa.com.br")


def test_sem_usuarios_autorizados_ninguem_entra(make_oidc) -> None:
    with pytest.raises(RuntimeError, match="nenhum usuário autorizado"):
        make_oidc()


def test_google_e_microsoft_juntos(make_settings) -> None:
    s = make_settings(google_client_id="g", google_client_secret="x", google_allowed_emails=["eu@gmail.com"],
                      azure_tenant_id=TENANT, azure_client_id=CLIENT_ID, azure_client_secret="y",
                      azure_allowed_domain="empresa.com.br")
    assert [p.name for p in configured_providers(s)] == ["google", "microsoft"]
    assert open_provider(s, "microsoft").name == "microsoft"
    with pytest.raises(RuntimeError, match="não está configurado"):
        open_provider(make_settings(google_client_id="g", google_client_secret="x"), "microsoft")


async def test_motivo_da_recusa_aparece(make_settings) -> None:
    """Sem o motivo (AADSTS...), não dá para saber se é segredo, plataforma ou redirect errado."""
    import httpx

    body = {"error": "invalid_client",
            "error_description": "AADSTS7000215: Invalid client secret provided.\r\nTrace ID: x\r\nCorrelation ID: y"}
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401, json=body)))
    s = make_settings(azure_tenant_id=TENANT, azure_client_id=CLIENT_ID, azure_client_secret="x",
                      azure_allowed_domain="empresa.com.br")
    oidc = EntraOIDC(s, http=http, jwks=FakeJWKS())  # type: ignore[arg-type]
    with pytest.raises(LoginError) as e:
        await oidc.finish(code="c", code_verifier="v", nonce="n1")
    assert str(e.value) == ("a Microsoft recusou o código de autorização: "
                            "invalid_client: AADSTS7000215: Invalid client secret provided.")
