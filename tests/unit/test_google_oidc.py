"""Validação do id_token do Google com uma chave RSA gerada no teste (sem rede).

Autorização: e-mail listado (GOOGLE_ALLOWED_EMAILS / admins) OU domínio Workspace (claim `hd`).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from code_index.infra.google_oidc import GoogleOIDC, LoginError

CLIENT_ID = "cid.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=KEY.public_key())


def make_token(key: Any = KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "123", "iat": now, "exp": now + 600,
        "email": "eu@gmail.com", "email_verified": True, "nonce": "n1", "name": "Eu",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def make_oidc(make_settings):
    def _make(**overrides: Any) -> GoogleOIDC:
        s = make_settings(google_client_id=CLIENT_ID, google_client_secret="x",
                          public_url="https://code-index.example.com", **overrides)
        return GoogleOIDC(s, jwks=FakeJWKS())  # type: ignore[arg-type]

    return _make


@pytest.fixture
def pessoal(make_oidc) -> GoogleOIDC:
    """Conta Google pessoal autorizada por e-mail (sem domínio Workspace)."""
    return make_oidc(google_allowed_emails=["eu@gmail.com"])


async def test_conta_pessoal_listada_entra(pessoal: GoogleOIDC) -> None:
    ident = await pessoal.verify_id_token(make_token(), nonce="n1")
    assert (ident.email, ident.domain) == ("eu@gmail.com", None)


async def test_admin_entra_mesmo_sem_estar_na_lista(make_oidc) -> None:
    oidc = make_oidc(admin_emails=["Eu@Gmail.com"])
    assert (await oidc.verify_id_token(make_token(), nonce="n1")).email == "eu@gmail.com"


@pytest.mark.parametrize(
    ("token_kwargs", "msg"),
    [
        ({"email": "outro@gmail.com"}, "não tem acesso"),     # conta não listada
        ({"email_verified": False}, "não verificado"),
        ({"nonce": "outro"}, "nonce"),
        ({"aud": "outro-client"}, "inválido"),
        ({"iss": "https://evil.example"}, "emissor"),
        ({"exp": int(time.time()) - 3600}, "inválido"),
        ({"key": OTHER_KEY}, "inválido"),                      # assinatura com outra chave
    ],
)
async def test_token_recusado(pessoal: GoogleOIDC, token_kwargs: dict[str, Any], msg: str) -> None:
    key = token_kwargs.pop("key", KEY)
    with pytest.raises(LoginError, match=msg):
        await pessoal.verify_id_token(make_token(key, **token_kwargs), nonce="n1")


async def test_dominio_workspace(make_oidc) -> None:
    oidc = make_oidc(google_allowed_domain="minhaempresa.com")
    ok = make_token(email="ana@minhaempresa.com", hd="minhaempresa.com")
    assert (await oidc.verify_id_token(ok, nonce="n1")).domain == "minhaempresa.com"
    # sufixo do e-mail sem o claim hd não basta (conta pessoal com alias/encaminhamento)
    with pytest.raises(LoginError, match="não tem acesso"):
        await oidc.verify_id_token(make_token(email="ana@minhaempresa.com"), nonce="n1")
    with pytest.raises(LoginError, match="não tem acesso"):
        await oidc.verify_id_token(make_token(), nonce="n1")  # gmail fora da lista


def test_url_de_login_tem_pkce_state_nonce(pessoal: GoogleOIDC, make_oidc) -> None:
    start = pessoal.start()
    q = parse_qs(urlsplit(start.url).query)
    assert q["redirect_uri"] == ["https://code-index.example.com/auth/callback"]
    assert q["state"] == [start.state] and q["nonce"] == [start.nonce]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0] != start.code_verifier
    assert "hd" not in q  # sem domínio configurado, nenhuma dica de domínio
    q2 = parse_qs(urlsplit(make_oidc(google_allowed_domain="minhaempresa.com").start().url).query)
    assert q2["hd"] == ["minhaempresa.com"]


def test_sem_configuracao_do_google(make_settings) -> None:
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        GoogleOIDC(make_settings())


def test_sem_usuarios_autorizados_ninguem_entra(make_settings) -> None:
    s = make_settings(google_client_id=CLIENT_ID, google_client_secret="x")
    with pytest.raises(RuntimeError, match="nenhum usuário autorizado"):
        GoogleOIDC(s)
