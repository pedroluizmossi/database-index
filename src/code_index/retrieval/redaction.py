"""Máscara de segredos no que sai para clientes (busca, read_file, grep, MCP).

Hoje é o próprio `redact` da indexação. O ponto de saída fica separado porque editar
`indexing/content.py` invalida o memo do CocoIndex e força reprocessar os repos: uma regra
urgente pode entrar aqui primeiro (vale na hora para a saída) e migrar para o `redact` no
próximo reindex planejado.
"""

from __future__ import annotations

from ..indexing.content import redact


def mask_output(text: str, language: str) -> str:
    """Texto com segredos mascarados, pronto para devolver a um cliente."""
    return redact(text, language)[0]
