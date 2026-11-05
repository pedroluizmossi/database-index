"""Limpeza de documentação antes da indexação (puro, sem I/O).

Diagramas e imagens embutidos em base64 (draw.io, SVG, data URIs) viram milhares de linhas sem valor de
busca e encarecem a indexação. Saem do texto que vai para os chunks e para a Voyage. O número de linhas é
preservado (cada trecho removido vira quebras de linha), então as linhas citadas pela busca continuam
batendo com o arquivo gravado.
"""

from __future__ import annotations

import re

# Cerca de código (``` ou ~~~): abertura, informação da linguagem, corpo e fechamento de mesmo tipo.
_FENCE = re.compile(r"^(`{3,}|~{3,})([^\n]*)\n(.*?)^\1[ \t]*$", re.DOTALL | re.MULTILINE)
_DATA_URI = re.compile(r"data:[\w.+/-]+;base64,[A-Za-z0-9+/=]+")
_B64_RUN = re.compile(r"[A-Za-z0-9+/=]{300,}")
_B64_ONLY = re.compile(r"[A-Za-z0-9+/=]+")
_MIN_BLOB = 300
_DIAGRAM_INFO = ("diagram", "drawio")


def _keep_lines(match: re.Match[str]) -> str:
    """Substitui o trecho removido por quebras de linha, mantendo a numeração do arquivo."""
    return "\n" * match.group(0).count("\n")


def strip_embedded_blobs(text: str) -> str:
    """Remove diagramas e imagens embutidas em base64; texto e linhas restantes ficam como estão."""

    def fence(m: re.Match[str]) -> str:
        info = m.group(2).strip().lower()
        compact = re.sub(r"\s+", "", m.group(3))
        is_diagram = info.startswith(_DIAGRAM_INFO)
        is_blob = len(compact) >= _MIN_BLOB and _B64_ONLY.fullmatch(compact) is not None
        return _keep_lines(m) if (is_diagram or is_blob) else m.group(0)

    text = _FENCE.sub(fence, text)
    text = _DATA_URI.sub(_keep_lines, text)
    return _B64_RUN.sub("", text)  # dentro de uma linha: não apaga quebras
