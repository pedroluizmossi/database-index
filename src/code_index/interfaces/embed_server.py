"""Servidor de embeddings local com a mesma forma da API da Voyage (`POST /v1/embeddings`).

Assim o resto do projeto não muda: o LiteLLM continua usando o provedor `voyage`, só com outro endereço
(EMBED_API_BASE) e outra chave (EMBED_API_KEY). Serve só embedding; o rerank continua na API.

Escuta em 127.0.0.1 por padrão e exige `Authorization: Bearer <EMBED_API_KEY>`.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from code_index.infra.local_embedder import Embedder, check_dimension
from code_index.settings import Settings

MAX_INPUTS = 1000  # mesmo limite de itens por chamada da API da Voyage


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    input_type: Literal["query", "document"] | None = None
    output_dimension: int | None = None
    truncation: bool = True
    encoding_format: Literal["float"] | None = None


class EmbeddingItem(BaseModel):
    object: Literal["embedding"] = "embedding"
    embedding: list[float]
    index: int


class Usage(BaseModel):
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingItem]
    model: str
    usage: Usage = Field(default_factory=lambda: Usage(total_tokens=0))


def create_embed_app(settings: Settings, embedder: Embedder) -> FastAPI:
    app = FastAPI(title="code-index embeddings locais", docs_url=None, redoc_url=None, openapi_url=None)
    expected = f"Bearer {settings.embed_api_key.get_secret_value()}".encode()
    # Em CPU, uma chamada de cada vez: paralelizar só disputa os mesmos núcleos.
    lock = asyncio.Lock()

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "model": embedder.model_name}

    async def embeddings(body: EmbeddingRequest, authorization: str | None = Header(None)) -> EmbeddingResponse:
        if not authorization or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(401, "chave inválida (Authorization: Bearer <EMBED_API_KEY>)")
        texts = [body.input] if isinstance(body.input, str) else body.input
        if not texts:
            raise HTTPException(400, "input vazio")
        if len(texts) > MAX_INPUTS:
            raise HTTPException(400, f"no máximo {MAX_INPUTS} textos por chamada")
        if any(not isinstance(t, str) for t in texts):
            raise HTTPException(400, "input deve ser texto")
        try:
            dimension = check_dimension(body.output_dimension)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        async with lock:
            vectors, tokens = await asyncio.to_thread(embedder.embed, texts, body.input_type, dimension)
        return EmbeddingResponse(
            data=[EmbeddingItem(embedding=v.tolist(), index=i) for i, v in enumerate(vectors)],
            model=embedder.model_name,
            usage=Usage(total_tokens=tokens),
        )

    # O LiteLLM monta `<EMBED_API_BASE>/embeddings`: aceita com e sem o prefixo /v1.
    app.post("/v1/embeddings")(embeddings)
    app.post("/embeddings")(embeddings)
    return app
