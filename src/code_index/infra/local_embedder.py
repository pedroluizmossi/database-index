"""Modelo de embedding aberto rodando nesta máquina (voyage-4-nano), usado por `code-index embed-serve`.

Só é importado pelo servidor local: `torch` e `sentence-transformers` vêm do extra opcional `local-embed`
(`uv sync --extra local-embed`), e o resto do projeto não depende deles.

O modelo usa `trust_remote_code` (uma classe Qwen3 bidirecional de ~90 linhas). Por isso a revisão é fixa
(LOCAL_EMBED_REVISION): o código remoto que roda aqui é o que foi revisado, e não o que estiver no
repositório no dia.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

import numpy as np

from code_index.settings import Settings

log = logging.getLogger(__name__)

InputType = Literal["query", "document"]

# Dimensões em que o modelo foi treinado (Matryoshka): cortar em outro tamanho perde qualidade sem aviso.
MATRYOSHKA_DIMENSIONS = (256, 512, 1024, 2048)
DEFAULT_DIMENSION = 1024  # mesmo padrão da API da Voyage


class Embedder(Protocol):
    """O que o servidor precisa de um modelo (o teste usa um falso, sem torch)."""

    model_name: str

    def embed(self, texts: list[str], input_type: InputType | None, dimension: int) -> tuple[np.ndarray, int]:
        """Vetores normalizados (len(texts) x dimension) e o total de tokens lidos."""
        ...


def check_dimension(dimension: int | None) -> int:
    if dimension is None:
        return DEFAULT_DIMENSION
    if dimension not in MATRYOSHKA_DIMENSIONS:
        raise ValueError(f"output_dimension deve ser um de {', '.join(map(str, MATRYOSHKA_DIMENSIONS))}")
    return dimension


def normalize(vectors: np.ndarray) -> np.ndarray:
    """Normaliza cada linha (o corte Matryoshka tira o vetor da esfera unitária)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


class SentenceTransformerEmbedder:
    """voyage-4-nano via sentence-transformers, em CPU.

    Consulta e documento usam prompts diferentes (definidos pelo próprio modelo em
    config_sentence_transformers.json): é o equivalente ao `input_type` da API da Voyage.
    """

    def __init__(self, settings: Settings) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        if settings.local_embed_threads:
            torch.set_num_threads(settings.local_embed_threads)
        self.model_name = settings.local_embed_model
        self._batch = settings.local_embed_batch
        log.info("carregando %s@%s (CPU, %d threads)", self.model_name, settings.local_embed_revision[:8],
                 torch.get_num_threads())
        self._model: Any = SentenceTransformer(
            self.model_name,
            revision=settings.local_embed_revision,
            trust_remote_code=True,
            device="cpu",
            model_kwargs={"dtype": torch.float32},  # bf16 em CPU costuma ser mais lento
        )
        self._model.max_seq_length = settings.local_embed_max_tokens
        prompts = getattr(self._model, "prompts", {}) or {}
        missing = {"query", "document"} - set(prompts)
        if missing:
            raise RuntimeError(f"o modelo não define os prompts {sorted(missing)}; revisão errada?")

    def embed(self, texts: list[str], input_type: InputType | None, dimension: int) -> tuple[np.ndarray, int]:
        vectors = self._model.encode(
            texts,
            prompt_name=input_type,  # None: sem prompt (como na Voyage sem input_type)
            batch_size=self._batch,
            convert_to_numpy=True,
            normalize_embeddings=False,
        )
        vectors = normalize(np.asarray(vectors, dtype=np.float32)[:, :dimension])
        tokens = sum(len(ids) for ids in self._model.tokenizer(texts, add_special_tokens=True)["input_ids"])
        return vectors, tokens
