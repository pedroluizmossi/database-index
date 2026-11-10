"""Busca, leitura de arquivos, símbolos, grep e avaliação da busca."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any


from ..domain.models import Hit, SymbolHit
from ..indexing.content import file_language
from ..indexing.orchestrator import configured_repos, repo_for_row
from ..infra.grep import GrepResult, GrepTarget
from ..infra.summary_store import SummaryStore
from ..retrieval import files as retrieval_files
from ..retrieval.evaluation import CaseResult, ConfigReport, EvalCase, first_hit, load_cases
from ..retrieval.redaction import mask_output
from ..retrieval.search import RerankOptions

from .ownership import OwnershipMixin

log = logging.getLogger(__name__)


class SearchMixin(OwnershipMixin):
    async def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        repos: list[str] | None = None,
        language: str | None = None,
        path_glob: str | None = None,
        mode: str = "hybrid",
        rerank: bool = True,
        candidates: int | None = None,
        kind: str | None = None,
        include_linked_docs: bool = True,
        exclude_repos: list[str] | None = None,
        exclude_path_glob: list[str] | None = None,
        max_docs: int | None = None,
        domain: str | None = None,
        service: str | None = None,
    ) -> list[Hit]:
        """Busca híbrida (vetor + full-text) com rerank opcional, em código e documentação.

        domain / service: só o código do domínio (e serviço) do DDD, inclusive pastas de monorepos
        (services/ownership.py). Nome fora do catálogo → ValueError com sugestões.

        max_docs: limite de páginas de documentação quando a busca mistura código e docs
        (padrão: um quarto do top_k, no mínimo 2).

        exclude_repos / exclude_path_glob: o que não pode aparecer no resultado (nomes de repo e globs de caminho).
        """
        if service and not domain:
            raise ValueError("informe o domínio junto com o serviço (ex.: domain=platform, service=audit)")
        scope = await self.owner_scope(domain, service) if domain else None
        hits = await self._searcher.search(
            query,
            top_k=top_k,
            repos=await self._resolve_repos(repos),
            scope=scope,
            language=language,
            path_glob=path_glob,
            mode=mode,
            rerank=rerank,
            candidates=candidates,
            kind=kind,
            include_linked_docs=include_linked_docs,
            exclude_repos=await self._resolve_repos(exclude_repos),
            exclude_path_glob=exclude_path_glob,
            max_docs=max_docs,
        )
        for h in hits:  # regras de máscara que só valem na saída (ver retrieval/redaction)
            h.code = mask_output(h.code, h.language)
        await self.annotate_owners(hits)
        await self.annotate_summaries(hits)
        return hits

    async def annotate_summaries(self, hits: Sequence[Hit]) -> None:
        """Preenche `summary`/`summary_keywords` dos resultados cujo arquivo tem resumo (fila file_summaries)."""
        found = await SummaryStore(self._chunks.pool, self.settings).get_many(
            sorted({(h.repo, h.path) for h in hits if h.kind != "doc"}))
        for h in hits:
            info = found.get((h.repo, h.path))
            if info:
                h.summary, h.summary_keywords = info["summary"], info["keywords"]

    async def read_file(
        self,
        repo: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Lê um arquivo do clone local (ou do diretório de um repo avulso), com números de linha.

        Aceita o nome como nas outras consultas: 'org__repo', 'org/repo' ou só 'repo'."""
        repo = (await self._resolve_repos([repo]) or [repo])[0]
        root = await asyncio.to_thread(self._local_path, repo)
        return await asyncio.to_thread(retrieval_files.read_file, root, repo, path, start_line, end_line)

    @staticmethod
    def rerank_variants() -> list[dict[str, Any]]:
        """Configurações que comparam as variações do rerank com a busca híbrida pura."""
        def cfg(name: str, **opts: bool) -> dict[str, Any]:
            return {"name": name, "mode": "hybrid", "rerank": True, "rerank_options": RerankOptions(**opts)}

        return [
            {"name": "hybrid (sem rerank)", "mode": "hybrid", "rerank": False},
            cfg("rerank"),
            cfg("+instrução", instruction=True),
            cfg("+cabeçalho", header=True),
            cfg("+só código", code_only=True),
            cfg("+instr+cab", instruction=True, header=True),
            cfg("+todas", instruction=True, header=True, code_only=True),
            cfg("+instr, pula ident.", instruction=True, skip_identifiers=True),
        ]

    def load_eval_cases(self, path: str | Path) -> list[EvalCase]:
        """Perguntas de avaliação de um arquivo JSON (formato de evals/queries.json)."""
        return load_cases(Path(path))

    async def evaluate(self, cases: Sequence[EvalCase], configs: Sequence[dict[str, Any]],
                       *, top_k: int = 10) -> list[ConfigReport]:
        """Roda as perguntas de avaliação em cada configuração de busca e mede os acertos.

        configs: [{"name", "mode", "rerank", "weights": (vetor, termos) | None,
        "rerank_options": RerankOptions | None}]. Custo: um embedding
        por pergunta (reaproveitado entre configurações) e um rerank por pergunta em cada
        configuração com rerank.
        """
        reports = []
        for cfg in configs:
            results = []
            for case in cases:
                hits = await self._searcher.search(
                    case.query, top_k=top_k, repos=await self._resolve_repos(case.repos),
                    kind=case.kind, language=case.language, path_glob=case.path_glob,
                    mode=cfg.get("mode", "hybrid"), rerank=cfg.get("rerank", True),
                    weights=cfg.get("weights"), rerank_options=cfg.get("rerank_options"),
                )
                results.append(CaseResult(case.query, first_hit(hits, case.expect),
                                          [f"{h.repo}/{h.path}:{h.start_line}" for h in hits[:3]], case.tag))
            reports.append(ConfigReport(cfg["name"], results))
        return reports

    async def search_engine(self) -> str:
        """Motor da busca por termos em uso: bm25 | fts | fts-legacy | none (ver infra/chunk_store)."""
        return (await self._chunks.capabilities(refresh=True)).keyword_engine

    async def find_symbol(
        self,
        name: str,
        *,
        kind: str | None = None,
        repos: Sequence[str] | None = None,
        language: str | None = None,
        limit: int = 20,
    ) -> list[SymbolHit]:
        """Onde uma classe/função/método/recurso é declarado (tabela preenchida na indexação)."""
        hits = await self._symbols.find(
            name, kind=kind, repos=await self._resolve_repos(repos), language=language, limit=limit
        )
        return await asyncio.to_thread(self._fix_annotated_lines, hits)

    def _fix_annotated_lines(self, hits: list[SymbolHit]) -> list[SymbolHit]:
        """Classes anotadas: o índice guarda a linha da 1ª anotação (`@Service`). Acha a linha da
        declaração no clone, sem reindexar. Se não achar, mantém o que veio do índice."""
        out = []
        for h in hits:
            if h.signature.startswith("@"):
                found = retrieval_files.declaration_line(self._local_path(h.repo), h.path, h.line, h.name)
                if found:
                    h = replace(h, line=found[0], signature=found[1])
            out.append(h)
        return out

    async def grep(
        self,
        pattern: str,
        *,
        repos: Sequence[str] | None = None,
        path_glob: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> GrepResult:
        """Texto exato ou regex nos clones locais, com os filtros de arquivo do índice e segredos mascarados."""
        wanted = await self._resolve_repos(repos)
        rows = await self._repos.all_rows()
        orgs = self._config.load_orgs()
        manual = configured_repos(self._config)
        targets: list[GrepTarget] = []
        for row in rows.values():
            if row.ignored or (wanted is not None and row.name not in wanted):
                continue
            repo = repo_for_row(row, orgs, manual)
            root = repo.local_path(self.settings.repos_dir)
            if root.is_dir():
                targets.append(GrepTarget(repo.name, root.resolve(), repo.include, tuple(repo.all_excludes)))
        if not targets:
            return GrepResult(matches=[], truncated=False)

        def mask(path: str, line: str) -> str:
            return mask_output(line, file_language(path.rsplit("/", 1)[-1]))

        return await self._grep.search(
            targets, pattern, regex=regex, case_sensitive=case_sensitive, path_glob=path_glob,
            max_results=max_results, mask=mask,
        )

    # ------------------------------------------------------------ enriquecimento (cartão do repo)

