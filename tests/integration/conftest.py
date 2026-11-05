"""Fixtures de integração: Postgres do docker-compose em schema isolado e embeddings/rerank falsos.

Isolamento: tudo fica em `tmp_path` e no schema `code_index_test`, que é apagado no teardown.
Nunca tocamos no schema `code_index`, em `.repos/`, `.cocoindex/` ou no `repos.toml` do projeto.
Nenhuma chamada à Voyage: `litellm.aembedding` e `litellm.arerank` são substituídos por funções locais.
"""

from __future__ import annotations

import asyncio
import os
import hashlib
import math
import re
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import asyncpg
import litellm
import pytest

from code_index import settings as settings_module
from code_index.settings import Settings, bootstrap, use_settings

# Outro Postgres (ex.: a imagem com pg_search para testar o BM25): CODE_INDEX_TEST_PG_URL=...
PG_TEST_URL = os.getenv("CODE_INDEX_TEST_PG_URL", "postgres://cocoindex:cocoindex@localhost:5432/cocoindex")
TEST_SCHEMA = "code_index_test"
EMBED_DIM = 1024
_TOKEN = re.compile(r"\w+")


# ---------------------------------------------------------------- Postgres

def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def pg_url() -> str:
    """URL do Postgres de teste; pula a suíte inteira se o servidor não responder."""

    async def ping() -> None:
        conn = await asyncpg.connect(PG_TEST_URL, timeout=3)
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()

    try:
        _run(ping())
    except Exception as exc:  # noqa: BLE001 - qualquer falha de conexão vira skip
        pytest.skip(f"Postgres indisponível em {PG_TEST_URL} ({type(exc).__name__}: {exc})")
    return PG_TEST_URL


def _drop_test_schema(url: str) -> None:
    async def drop() -> None:
        conn = await asyncpg.connect(url, timeout=5)
        try:
            await conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        finally:
            await conn.close()

    _run(drop())


@pytest.fixture
def int_settings(tmp_path, monkeypatch, pg_url, clean_env) -> Iterator[Settings]:
    """Settings isolado + use_settings + bootstrap, antes de qualquer execução do CocoIndex."""
    base = tmp_path / "estado-do-indice"
    base.mkdir()
    settings = Settings(
        _env_file=None,
        base_dir=base,
        database_url=pg_url,
        pg_schema=TEST_SCHEMA,
        github_token="token-falso-de-teste",  # evita rodar `gh auth token`
        repos_file=base / "repos.toml",
        repos_dir=base / "repos",
        cocoindex_db=base / "cocoindex-db",
        lock_file=base / "code-index.lock",
    )
    monkeypatch.setattr(settings_module, "_current", None)
    monkeypatch.setenv("COCOINDEX_DB", str(settings.cocoindex_db))  # bootstrap sobrescreve; monkeypatch restaura
    use_settings(settings)
    bootstrap(settings)
    try:
        yield settings
    finally:
        _drop_test_schema(pg_url)


# ---------------------------------------------------------------- LiteLLM falso

def fake_vector(text: str) -> list[float]:
    """Embedding determinístico: bag-of-words com hash estável (sha256) em 1024 dimensões."""
    vec = [0.0] * EMBED_DIM
    for token in _TOKEN.findall(text.lower()):
        bucket = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % EMBED_DIM
        vec[bucket] += 1.0
    if not any(vec):
        vec[0] = 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


class FakeLiteLLM:
    """Substitui litellm.aembedding/arerank. Registra o que seria enviado à Voyage."""

    def __init__(self) -> None:
        self.embedded: list[str] = []
        self.rerank_calls = 0

    async def aembedding(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        texts = [str(t) for t in kwargs["input"]]
        self.embedded.extend(texts)
        return SimpleNamespace(data=[{"index": i, "embedding": fake_vector(t)} for i, t in enumerate(texts)])

    async def arerank(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.rerank_calls += 1
        query_tokens = set(_TOKEN.findall(str(kwargs["query"]).lower()))
        docs = list(kwargs["documents"])
        scored = []
        for i, doc in enumerate(docs):
            overlap = len(query_tokens & set(_TOKEN.findall(doc.lower())))
            scored.append((i, round(overlap / max(len(query_tokens), 1), 4)))
        scored.sort(key=lambda item: (-item[1], item[0]))
        top_n = kwargs.get("top_n") or len(docs)
        return SimpleNamespace(results=[{"index": i, "relevance_score": s} for i, s in scored[:top_n]])


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLiteLLM:
    fake = FakeLiteLLM()
    monkeypatch.setattr(litellm, "aembedding", fake.aembedding)
    monkeypatch.setattr(litellm, "arerank", fake.arerank)
    return fake


