"""Mensagens claras dos erros de sync e vencimento do token da wiki."""

from __future__ import annotations

import base64
import json

from code_index.domain.sync_errors import explain_error
from code_index.sources.wikijs import token_expired_at


def test_erros_conhecidos_viram_frase_clara() -> None:
    assert "contexto do modelo" in explain_error(
        "litellm.ContextWindowExceededError: request (9294 tokens) exceeds the available context size (8192 tokens)")
    assert "servidor de modelos" in explain_error("litellm.APIConnectionError: Connection refused")
    assert "GitHub" in explain_error("fatal: Authentication failed for 'https://github.com/x/y.git'")
    assert explain_error("") .startswith("erro sem mensagem")
    assert explain_error("o Wiki.js recusou o token\nlinha 2") == "o Wiki.js recusou o token"   # já em português


def _jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{body}.assinatura"


def test_vencimento_do_token_da_wiki() -> None:
    assert token_expired_at(_jwt(1000), now=2000) == "01/01/1970 00:16 UTC"
    assert token_expired_at("Bearer " + _jwt(3000), now=2000) is None   # ainda vale
    assert token_expired_at("api-key-sem-pontos", now=2000) is None      # API key: não é JWT
