"""Vetores extras da busca (puro): perguntas dos resumos de arquivos e cartões dos repos.

Os trechos de código têm o vetor do próprio código; estes textos descrevem o código com palavras de gente e
ajudam perguntas que não usam o vocabulário do código ("detectar o navegador pelo user agent"):
  - question:  cada pergunta do resumo de um arquivo (`file_summaries.questions`) vira um vetor. A consulta é
               comparada com outra pergunta, a comparação mais fácil para o modelo. Leva ao arquivo.
  - summary:   o texto do resumo (com o caminho e as palavras-chave) vira um vetor por arquivo. Na busca, resumo
               e perguntas são UMA fonte ("descrição do arquivo"): vale a mais parecida, sem voto duplo.
  - repo_card: o cartão do repo (resumo, capacidades, tags, glossário) vira um vetor por repo. Diz quais repos
               tratam do assunto e reforça os trechos deles.

Embedar custa (Voyage), então nada é embedado na hora em que o resumo ou o cartão muda: o texto esperado
é comparado com o gravado (hash e modelo) e o que diverge fica **pendente** até alguém pedir a geração
(`code-index vectors embed`, ou o botão em Indexação). Vetor sem texto de origem fica **obsoleto** e sai
na próxima geração (sem custo).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

VectorKind = Literal["question", "summary", "repo_card"]
KINDS: tuple[VectorKind, ...] = ("question", "summary", "repo_card")
FILE_KINDS: tuple[VectorKind, ...] = ("question", "summary")   # levam ao arquivo (fonte "descrição do arquivo")
KIND_LABELS: dict[str, str] = {"question": "perguntas dos resumos", "summary": "resumos dos arquivos",
                               "repo_card": "cartões dos repos"}
MAX_TEXT_CHARS = 2000      # o cartão inteiro cabe (resumo ≤ 1200 + listas curtas); o resto é corte de segurança
CHARS_PER_TOKEN = 4        # estimativa de tokens antes de gerar (a cobrança real vem da Voyage depois)


@dataclass(frozen=True, order=True)
class VectorKey:
    kind: str
    repo: str
    path: str = ""          # arquivo da pergunta; vazio no cartão
    ord: int = 0            # posição da pergunta no resumo


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class VectorText:
    key: VectorKey
    text: str

    @property
    def hash(self) -> str:
        return text_hash(self.text)


def question_texts(repo: str, path: str, questions: Iterable[Any]) -> list[VectorText]:
    """Uma entrada por pergunta não vazia, na ordem do resumo (`ord` = posição entre as válidas)."""
    clean = [q.strip()[:MAX_TEXT_CHARS] for q in questions if isinstance(q, str) and q.strip()]
    return [VectorText(VectorKey("question", repo, path, i), q) for i, q in enumerate(clean)]


def summary_text(path: str, summary: str, keywords: Iterable[Any] = ()) -> str:
    """Texto embedado do resumo: o caminho (dá o assunto: billing/, auth/), o resumo e as palavras-chave.
    O código fica de fora: ele já tem o vetor dos trechos."""
    lines = [f"Arquivo {path}", summary.strip()]
    terms = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    if terms:
        lines.append("Termos: " + ", ".join(terms))
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def summary_texts(repo: str, path: str, summary: Any, keywords: Iterable[Any] = ()) -> list[VectorText]:
    """O vetor do resumo do arquivo (nenhum se o resumo está vazio)."""
    if not isinstance(summary, str) or not summary.strip():
        return []
    return [VectorText(VectorKey("summary", repo, path), summary_text(path, summary, keywords))]


def estimate_tokens(chars: int) -> int:
    return -(-max(chars, 0) // CHARS_PER_TOKEN)


def _items(value: Any) -> list[str]:
    return [v.strip() for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def card_text(repo: str, card: Mapping[str, Any]) -> str:
    """Texto embedado do cartão: o que o repo faz, com as palavras que alguém usaria para procurá-lo.
    Entradas, repos relacionados e evidências ficam de fora (caminhos e nomes, não assunto)."""
    name = repo.split("__", 1)[-1]
    kind = card.get("kind")
    lines = [f"Repositório {name}" + (f" ({kind})" if isinstance(kind, str) and kind else "")]
    summary = card.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.append(summary.strip())
    for label, key, sep in (("O que faz", "capabilities", "; "), ("Assuntos", "domain_tags", ", "),
                            ("Termos", "glossary", "; ")):
        items = _items(card.get(key))
        if items:
            lines.append(f"{label}: {sep.join(items)}")
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def card_texts(repo: str, card: Mapping[str, Any] | None) -> list[VectorText]:
    """O vetor do cartão (nenhum se o repo não tem cartão escrito)."""
    if not card or not isinstance(card.get("summary"), str):
        return []
    return [VectorText(VectorKey("repo_card", repo), card_text(repo, card))]


@dataclass(frozen=True)
class Stored:
    hash: str
    model: str


@dataclass
class KindCounts:
    sources: int = 0      # textos que deveriam ter vetor
    embedded: int = 0     # com vetor em dia (mesmo texto e mesmo modelo)
    pending: int = 0      # sem vetor, ou com vetor de outro texto/modelo
    obsolete: int = 0     # vetor gravado sem texto de origem (resumo ou cartão saiu)

    def to_dict(self) -> dict[str, int]:
        return {"sources": self.sources, "embedded": self.embedded, "pending": self.pending,
                "obsolete": self.obsolete}


@dataclass
class VectorPlan:
    embed: list[VectorText] = field(default_factory=list)
    delete: list[VectorKey] = field(default_factory=list)
    counts: dict[str, KindCounts] = field(default_factory=lambda: {k: KindCounts() for k in KINDS})

    @property
    def pending(self) -> int:
        return len(self.embed)

    def counts_dict(self) -> dict[str, dict[str, int]]:
        return {k: c.to_dict() for k, c in self.counts.items()}


def plan(expected: Iterable[VectorText], stored: Mapping[VectorKey, Stored], model: str) -> VectorPlan:
    """O que gerar (texto novo, mudado ou de outro modelo) e o que apagar (sem origem)."""
    out = VectorPlan()
    seen: set[VectorKey] = set()
    for item in expected:
        if item.key in seen:
            continue
        seen.add(item.key)
        counts = out.counts.setdefault(item.key.kind, KindCounts())
        counts.sources += 1
        current = stored.get(item.key)
        if current is not None and current.hash == item.hash and current.model == model:
            counts.embedded += 1
        else:
            counts.pending += 1
            out.embed.append(item)
    for key in sorted(stored):
        if key not in seen:
            out.counts.setdefault(key.kind, KindCounts()).obsolete += 1
            out.delete.append(key)
    return out
