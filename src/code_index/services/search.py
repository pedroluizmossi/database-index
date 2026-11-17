"""Busca, leitura de arquivos, símbolos, grep e avaliação da busca."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any


from ..domain.models import Hit, SymbolHit
from ..indexing.content import file_language
from ..indexing.orchestrator import configured_repos, repo_for_row
from ..infra.grep import GrepResult, GrepTarget
from ..infra.summary_store import SummaryStore
from ..infra.test_store import TestStore
from ..domain.test_cases import without_test_excludes
from ..retrieval import files as retrieval_files
from ..retrieval.evaluation import (
    CaseResult, ConfigDiff, ConfigReport, EvalCase, baseline_payload, compare, expects_any, first_hit, load_baseline,
    load_cases, save_baseline,
)
from ..retrieval.redaction import mask_output
from ..retrieval.search import RerankOptions

from .ownership import OwnershipMixin

log = logging.getLogger(__name__)

# Limites de entrada (os mesmos da API REST): valem para API, MCP e CLI. Uma consulta enorme vai à Voyage
# no embedding e é cobrada de novo para cada candidato do rerank.
MAX_QUERY_CHARS = 2000
MAX_EXCLUDES = 50
MAX_DOCS = 50
MAX_SYMBOL_CHARS = 200


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
        if not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"consulta vazia ou maior que {MAX_QUERY_CHARS} caracteres")
        if len(exclude_repos or ()) > MAX_EXCLUDES or len(exclude_path_glob or ()) > MAX_EXCLUDES:
            raise ValueError(f"no máximo {MAX_EXCLUDES} itens em exclude_repos e exclude_path_glob")
        if max_docs is not None and not 0 <= max_docs <= MAX_DOCS:
            raise ValueError(f"max_docs deve ser entre 0 e {MAX_DOCS}")
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
        await self.annotate_tests(hits)
        return hits

    async def annotate_tests(self, hits: Sequence[Hit]) -> None:
        """Preenche `tests` (quantos casos de teste cobrem o arquivo) nos resultados de código: o agent sabe
        que existe teste sem que o teste ocupe lugar no resultado (find_tests lista os casos)."""
        counts = await TestStore(self._chunks.pool, self.settings).counts(
            sorted({(h.repo, h.path) for h in hits if h.kind != "doc"}))
        for h in hits:
            h.tests = counts.get((h.repo, h.path))

    async def annotate_summaries(self, hits: Sequence[Hit]) -> None:
        """Preenche `summary`/`summary_keywords` dos resultados cujo arquivo tem resumo (fila file_summaries)."""
        found = await SummaryStore(self._chunks.pool, self.settings).get_many(
            sorted({(h.repo, h.path) for h in hits if h.kind != "doc"}))
        for h in hits:
            info = found.get((h.repo, h.path))
            if info:
                h.summary, h.summary_keywords = info["summary"], info["keywords"]
                h.summary_stale = True if info.get("stale") else None

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
        root = await self._local_path(repo)
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

    def load_eval_cases(self, path: str | Path, split: str | None = None) -> list[EvalCase]:
        """Perguntas de avaliação de um arquivo JSON (formato de evals/queries.json).
        split: 'dev' (para calibrar) ou 'test' (para confirmar); vazio = todas."""
        return load_cases(Path(path), split)

    @staticmethod
    def eval_baseline_path(cases_file: str | Path, name: str) -> Path:
        """Onde fica um baseline: nome simples vira `baselines/<nome>.json` ao lado do arquivo de perguntas;
        caminho (com pasta ou terminado em .json) fica como está."""
        path = Path(name)
        if path.suffix == ".json" or path.parent != Path("."):
            return path
        return Path(cases_file).parent / "baselines" / f"{name}.json"

    @staticmethod
    def load_eval_baseline(path: Path) -> dict[str, Any]:
        """Rodada gravada por `save_eval_baseline`. Arquivo ausente ou inválido → ValueError."""
        try:
            return load_baseline(path)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"não foi possível ler o baseline {path}: {e}") from None

    @staticmethod
    def save_eval_baseline(path: Path, reports: list[ConfigReport], meta: dict[str, Any]) -> dict[str, Any]:
        """Grava a rodada (métricas e a posição de cada pergunta) para comparar depois. Devolve o que gravou."""
        payload = baseline_payload(reports, meta)
        save_baseline(path, payload)
        return payload

    @staticmethod
    def compare_eval(reports: list[ConfigReport], baseline: dict[str, Any]) -> list[ConfigDiff]:
        """Diferença para um baseline, por configuração de mesmo nome e nas perguntas em comum."""
        return compare(reports, baseline)

    async def eval_meta(self, cases: Sequence[EvalCase], *, top_k: int, split: str | None) -> dict[str, Any]:
        """O que identifica uma rodada de avaliação num baseline: quando, com que modelos e sobre o quê."""
        s = self.settings
        return {"date": date.today().isoformat(), "cases": len(cases), "split": split or "all", "top_k": top_k,
                "embed_model": s.embed_model, "embed_dimensions": s.embed_dimensions,
                "rerank_model": s.rerank_model, "rerank_candidates": s.rerank_candidates,
                "keyword_engine": await self.search_engine()}

    async def evaluate(self, cases: Sequence[EvalCase], configs: Sequence[dict[str, Any]],
                       *, top_k: int = 10) -> list[ConfigReport]:
        """Roda as perguntas de avaliação em cada configuração de busca e mede os acertos.

        configs: [{"name", "mode", "rerank", "weights": (vetor, termos) | None,
        "rerank_options": RerankOptions | None, "extra_weights": (descrição do arquivo, cartões) | None,
        "extra_options": {"file_kinds": (...), "stale_factor": float} | None}]. Custo: um embedding
        por pergunta (reaproveitado entre configurações) e um rerank por pergunta em cada
        configuração com rerank. Cada resultado traz também a posição da resposta entre os candidatos
        da fusão (`candidate_rank`), que separa erro de recuperação de erro de ordenação.
        """
        reports = []
        for cfg in configs:
            results = []
            for case in cases:
                seen: list[int | None] = []  # posição da resposta entre os candidatos, antes do rerank

                def on_candidates(candidates: list[Hit], case: EvalCase = case, seen: list[int | None] = seen) -> None:
                    seen.append(first_hit(candidates, case.expect))

                hits = await self._searcher.search(
                    case.query, top_k=top_k, repos=await self._resolve_repos(case.repos),
                    kind=case.kind, language=case.language, path_glob=case.path_glob,
                    mode=cfg.get("mode", "hybrid"), rerank=cfg.get("rerank", True),
                    weights=cfg.get("weights"), rerank_options=cfg.get("rerank_options"),
                    on_candidates=on_candidates, extra_weights=cfg.get("extra_weights"),
                    extra_options=cfg.get("extra_options"),
                )
                results.append(CaseResult(case.query, first_hit(hits, case.expect),
                                          [f"{h.repo}/{h.path}:{h.start_line}" for h in hits[:3]], case.tag,
                                          candidate_rank=seen[0] if seen else None))
            reports.append(ConfigReport(cfg["name"], results))
        return reports

    async def eval_summarized(self, cases: Sequence[EvalCase]) -> set[str]:
        """Perguntas da avaliação cuja resposta está num arquivo com resumo (subconjunto onde a descrição do
        arquivo pode agir)."""
        rows = await self._chunks.pool.fetch(
            f'SELECT repo, path FROM "{self.settings.pg_schema}".file_summaries')
        return expects_any(list(cases), {(r["repo"], r["path"]) for r in rows})

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
        if not name.strip() or len(name) > MAX_SYMBOL_CHARS:
            raise ValueError(f"nome vazio ou maior que {MAX_SYMBOL_CHARS} caracteres")
        hits = await self._symbols.find(
            name, kind=kind, repos=await self._resolve_repos(repos), language=language, limit=limit
        )
        roots = {h.repo: await self._local_path(h.repo) for h in hits if h.signature.startswith("@")}
        return await asyncio.to_thread(self._fix_annotated_lines, hits, roots)

    @staticmethod
    def _fix_annotated_lines(hits: list[SymbolHit], roots: dict[str, Path]) -> list[SymbolHit]:
        """Classes anotadas: o índice guarda a linha da 1ª anotação (`@Service`). Acha a linha da
        declaração no clone, sem reindexar. Se não achar, mantém o que veio do índice."""
        out = []
        for h in hits:
            if h.signature.startswith("@"):
                found = retrieval_files.declaration_line(roots[h.repo], h.path, h.line, h.name)
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
        include_tests: bool = False,
    ) -> GrepResult:
        """Texto exato ou regex nos clones locais, com os filtros de arquivo do índice e segredos mascarados.
        `include_tests`: procura também nos arquivos de teste (fora do índice por padrão)."""
        wanted = await self._resolve_repos(repos)
        rows = await self._repos.all_rows()
        orgs = await self._config.orgs()
        manual = await configured_repos(self._config)
        targets: list[GrepTarget] = []
        for row in rows.values():
            if row.ignored or (wanted is not None and row.name not in wanted):
                continue
            repo = repo_for_row(row, orgs, manual)
            root = repo.local_path(self.settings.repos_dir)
            if root.is_dir():
                excludes = without_test_excludes(repo.all_excludes) if include_tests else repo.all_excludes
                targets.append(GrepTarget(repo.name, root.resolve(), repo.include, tuple(excludes)))
        if not targets:
            return GrepResult(matches=[], truncated=False)

        def masked_lines(path: Path) -> list[str] | None:
            try:
                # bytes → texto sem tradução de quebra de linha (read_text troca \r solto por \n e mudaria a numeração)
                text = path.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                return None
            # `redact` preserva a contagem de linhas; split("\n") numera como o rg (splitlines quebraria em \r, \x0c...)
            return mask_output(text, file_language(path.name)).split("\n")

        async def mask(path: Path) -> list[str] | None:
            """Arquivo inteiro mascarado (uma chave PEM só é reconhecida inteira)."""
            return await asyncio.to_thread(masked_lines, path)

        return await self._grep.search(
            targets, pattern, regex=regex, case_sensitive=case_sensitive, path_glob=path_glob,
            max_results=max_results, mask=mask,
        )

    # ------------------------------------------------------------ enriquecimento (cartão do repo)

