"""Consultas sobre a tabela de chunks (gerenciada pelo CocoIndex): busca híbrida e contagens.

Busca = fusão RRF ponderada de duas fontes:
  - vetor (pgvector HNSW, cosseno);
  - termos, pelo melhor motor disponível no banco:
      bm25       → ParadeDB pg_search sobre `search_symbols` (boost 3) e `search_terms`;
      fts        → full-text do Postgres sobre as mesmas colunas, com pesos A/B;
      fts-legacy → full-text sobre `code` (tabela de antes das colunas de busca; até reindexar).
O motor é detectado no banco (e rechecado periodicamente), então a busca nunca quebra
enquanto o índice é migrado. Valores entram sempre como parâmetros; só nomes vêm das configurações.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg
import numpy as np

from code_index.domain.models import Hit
from code_index.domain.ownership import Scope
from code_index.domain.path_glob import glob_regex
from code_index.domain.search_text import STOPWORDS
from code_index.infra.document_store import DocumentStore
from code_index.infra.relation_store import RelationStore
from code_index.infra.skip_store import SkipStore
from code_index.settings import Settings

log = logging.getLogger(__name__)

_RRF_K = 60
_CAPS_TTL = 30.0          # segundos entre rechecagens do que o banco oferece
_BM25_SYMBOL_BOOST = 3    # nomes de símbolos/caminho valem mais que o corpo do código
BM25_INDEX = "code_chunks_bm25"

# Documento do full-text com pesos (símbolos A, código B). Usado no índice GIN e na consulta:
# precisa ser idêntico nos dois lugares para o índice ser aproveitado.
FTS_DOCUMENT = (
    "(setweight(to_tsvector('simple', coalesce(search_symbols, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(search_terms, '')), 'B'))"
)
_FTS_WEIGHTS = "'{0.1, 0.2, 0.4, 1.0}'"  # D, C, B, A

KeywordEngine = Literal["bm25", "fts", "fts-legacy", "none"]

# Mantido para o caminho legado (e para compatibilidade com quem importava daqui).
_STOPWORDS = STOPWORDS


def keyword_tsquery(query: str) -> str | None:
    """Termos significativos unidos por OR (caminho legado, sobre a coluna `code`)."""
    terms: list[str] = []
    for t in re.findall(r"\w+", query.lower()):
        if len(t) >= 3 and t not in _STOPWORDS and t not in terms:
            terms.append(t)
    return " | ".join(terms) or None




def tsquery_or(terms: Sequence[str]) -> str | None:
    """OR de termos para to_tsquery. Só caracteres de palavra: nada que a sintaxe do tsquery interprete."""
    safe = [t for t in (re.sub(r"\W", "", term) for term in terms) if t]
    return " | ".join(dict.fromkeys(safe)) or None


class _Params:
    """Numeração dinâmica de parâmetros ($1, $2, ...) para montar SQL com filtros opcionais."""

    def __init__(self) -> None:
        self.values: list[object] = []

    def __call__(self, value: object) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


KINDS = ("code", "doc")
DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".adoc", ".txt")


def hit_kind(language: str, path: str) -> str:
    """'doc' para Markdown/RST/AsciiDoc (README, docs/ de um repo, páginas de wiki); senão 'code'."""
    return "doc" if language == "markdown" or path.lower().endswith(DOC_EXTENSIONS) else "code"


def _doc_condition(p: _Params) -> str:
    """Mesmo critério de `hit_kind`, em SQL."""
    exts = p([f"%{e}" for e in DOC_EXTENSIONS])
    return f"(language = 'markdown' OR lower(path) LIKE ANY({exts}))"


def _filters(
    p: _Params,
    repos: Sequence[str] | None,
    language: str | None,
    path_glob: str | None,
    *,
    kind: str | None = None,
    linked_docs_sql: str | None = None,
    exclude_repos: Sequence[str] | None = None,
    exclude_path_glob: Sequence[str] | None = None,
    scope: Scope | None = None,
) -> str:
    """`linked_docs_sql`: subconsulta de vínculos; com ela, o filtro de repos (e o de domínio) também traz
    as páginas de documentação ligadas a esses repos. `scope`: código de um domínio/serviço do DDD."""
    clauses = []
    if scope is not None:
        clauses.append(_scope_sql(p, scope, linked_docs_sql))
    if repos:
        r = p(list(repos))
        if linked_docs_sql:
            clauses.append(
                f"(repo = ANY({r}) OR (repo, path) IN (SELECT l.doc_repo, l.doc_path FROM ({linked_docs_sql}) l"
                f" WHERE l.repo = ANY({r})))"
            )
        else:
            clauses.append(f"repo = ANY({r})")
    if kind == "doc":
        clauses.append(_doc_condition(p))
    elif kind == "code":
        clauses.append(f"NOT {_doc_condition(p)}")
    if language:
        clauses.append(f"language = {p(language.strip().lower())}")
    if path_glob:
        clauses.append(f"path ~ {p(glob_regex(path_glob))}")
    if exclude_repos:
        clauses.append(f"repo <> ALL({p(list(exclude_repos))})")
    for glob in exclude_path_glob or ():
        clauses.append(f"path !~ {p(glob_regex(glob))}")
    return " AND ".join(clauses) or "TRUE"


def _scope_sql(p: _Params, scope: Scope, linked_docs_sql: str | None) -> str:
    """Repos inteiros do domínio, menos pastas de outro dono (monorepo), mais pastas deste dono em outros
    repos. `starts_with` em vez de LIKE: nomes de pasta com '_' ou '%' não viram curinga."""
    if scope.empty:
        return "FALSE"

    def under(folders: Sequence[str]) -> str:
        return " OR ".join(f"starts_with(path, {p(f.rstrip('/') + '/')})" for f in folders)

    parts = []
    simple = sorted(r for r in scope.whole if r not in scope.exclude)
    if simple:
        parts.append(f"repo = ANY({p(simple)})")
    for repo, folders in sorted(scope.exclude.items()):
        if repo in scope.whole:
            parts.append(f"(repo = {p(repo)} AND NOT ({under(folders)}))")
    for repo, folders in sorted(scope.include.items()):
        parts.append(f"(repo = {p(repo)} AND ({under(folders)}))")
    if linked_docs_sql:
        parts.append(f"(repo, path) IN (SELECT l.doc_repo, l.doc_path FROM ({linked_docs_sql}) l"
                     f" WHERE l.repo = ANY({p(sorted(scope.repos))}))")
    return "(" + " OR ".join(parts) + ")"


@dataclass(frozen=True)
class SearchCapabilities:
    table: bool            # a tabela de chunks existe (já houve um sync)
    search_columns: bool   # colunas search_symbols/search_terms existem (índice migrado)
    bm25: bool             # índice BM25 (pg_search) existe

    @property
    def keyword_engine(self) -> KeywordEngine:
        if not self.table:
            return "none"
        if self.bm25 and self.search_columns:
            return "bm25"
        return "fts" if self.search_columns else "fts-legacy"


class ChunkStore:
    def __init__(self, pool: asyncpg.Pool, settings: Settings, *, documents: DocumentStore | None = None,
                 relations: RelationStore | None = None) -> None:
        self._pool = pool
        self._settings = settings
        self._table = settings.chunks_table_qualified
        self._caps: SearchCapabilities | None = None
        self._caps_at = 0.0
        self._documents = documents or DocumentStore(pool, settings)
        self._relations = relations or RelationStore(pool, settings)
        self._skips = SkipStore(pool, settings)

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    # ------------------------------------------------------------------ capacidades do banco

    async def capabilities(self, *, refresh: bool = False) -> SearchCapabilities:
        if not refresh and self._caps is not None and time.monotonic() - self._caps_at < _CAPS_TTL:
            return self._caps
        s = self._settings
        row = await self._pool.fetchrow(
            """SELECT to_regclass($1) IS NOT NULL AS has_table,
                      EXISTS (SELECT 1 FROM information_schema.columns
                              WHERE table_schema = $2 AND table_name = $3 AND column_name = 'search_terms') AS has_cols,
                      EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = $2 AND indexname = $4) AS has_bm25""",
            self._table, s.pg_schema, s.chunks_table, BM25_INDEX,
        )
        self._caps = SearchCapabilities(row["has_table"], row["has_cols"], row["has_bm25"])
        self._caps_at = time.monotonic()
        return self._caps

    async def ensure_bm25_index(self) -> bool:
        """Cria o índice BM25 se o banco tem pg_search e a tabela já tem as colunas de busca.

        Idempotente; chamado na inicialização e após cada sync (o CocoIndex pode recriar a tabela).
        Devolve True se o índice existe ao final.
        """
        has_ext = await self._pool.fetchval("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search')")
        caps = await self.capabilities(refresh=True)
        if not has_ext or not caps.table or not caps.search_columns:
            return caps.bm25
        if not caps.bm25:
            log.info("criando índice BM25 (pg_search) em %s", self._table)
            await self._pool.execute(
                f'CREATE INDEX IF NOT EXISTS "{BM25_INDEX}" ON {self._table} USING bm25 '
                "(id, (search_symbols::pdb.whitespace), (search_terms::pdb.whitespace))"
            )
        return (await self.capabilities(refresh=True)).bm25

    # ------------------------------------------------------------------ busca

    async def hybrid_search(
        self,
        *,
        query_vec: np.ndarray | None,
        terms: Sequence[str],
        limit: int,
        per_source: int,
        vec_weight: float = 1.0,
        kw_weight: float = 1.0,
        repos: Sequence[str] | None = None,
        language: str | None = None,
        path_glob: str | None = None,
        kind: str | None = None,
        include_linked_docs: bool = True,
        exclude_repos: Sequence[str] | None = None,
        exclude_path_glob: Sequence[str] | None = None,
        scope: Scope | None = None,
    ) -> list[Hit]:
        """Fusão RRF ponderada: score = Σ peso_fonte / (60 + posição na fonte).

        `terms` já vêm tokenizados (domain.search_text.query_terms). `per_source` é quantos
        candidatos cada fonte traz; `limit` é quantos sobram após a fusão. Sem fontes → [].
        `kind`: 'code' | 'doc' | None. `include_linked_docs`: com `repos`, traz também as páginas
        de documentação externa ligadas a eles. `scope`: só o código de um domínio/serviço do DDD.

        Com filtro de repos ou de domínio sobre um subconjunto pequeno (até SEARCH_EXACT_MAX_CHUNKS trechos),
        a busca vetorial é exata (sem o HNSW): o índice aproximado descarta quase tudo quando o filtro é
        seletivo e pode devolver poucos resultados.
        """
        if kind is not None and kind not in KINDS:
            raise ValueError(f"kind inválido: {kind} (use code ou doc)")
        caps = await self.capabilities()
        engine = caps.keyword_engine
        use_kw = bool(terms) and kw_weight > 0 and engine != "none"
        use_vec = query_vec is not None and vec_weight > 0 and caps.table
        if not use_vec and not use_kw:
            return []

        p = _Params()
        k = p(per_source)
        linked = self._relations.doc_links_sql() if ((repos or scope) and include_linked_docs) else None
        where = _filters(p, repos, language, path_glob, kind=kind, linked_docs_sql=linked,
                         exclude_repos=exclude_repos, exclude_path_glob=exclude_path_glob, scope=scope)
        exact = use_vec and await self._small_subset(repos, scope)
        # páginas de documentação ignoradas (docs ignore) não aparecem em nenhum resultado
        where = f"({where}) AND (repo, path) NOT IN ({self._documents.ignored_sql()})"
        t = self._table
        parts: list[str] = []
        weights: list[float] = []

        if use_vec:
            v = p(query_vec)
            parts.append(f"""
            SELECT repo, path, id, row_number() OVER (ORDER BY embedding <=> {v}) AS rnk, {float(vec_weight)}::float8 AS w
            FROM (SELECT repo, path, id, embedding FROM {t} WHERE {where}
                  ORDER BY {"(embedding <=> " + v + ") + 0" if exact else "embedding <=> " + v} LIMIT {k}) v""")
            weights.append(vec_weight)

        if use_kw:
            kw_sql = self._keyword_sql(engine, p, terms, where, k)
            if kw_sql:
                parts.append(f"""
            SELECT repo, path, id, row_number() OVER (ORDER BY rank DESC) AS rnk, {float(kw_weight)}::float8 AS w
            FROM ({kw_sql}) kw""")
                weights.append(kw_weight)

        if not parts:
            return []
        sql = f"""
        WITH ranked AS ({" UNION ALL ".join(parts)}),
        fused AS (SELECT repo, path, id, SUM(w / ({_RRF_K} + rnk)) AS score
                  FROM ranked GROUP BY repo, path, id)
        SELECT t.repo, t.path, t.language, t.start_line, t.end_line, t.code, f.score
        FROM fused f JOIN {t} t USING (repo, path, id)
        ORDER BY f.score DESC LIMIT {int(limit)}"""

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    if use_vec and not exact:
                        # pgvector >= 0.8: continua varrendo o HNSW quando filtros descartam candidatos.
                        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                    rows = await conn.fetch(sql, *p.values)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError, asyncpg.UndefinedObjectError):
            # tabela/coluna/índice mudou no meio (sync recriando a tabela): recheca na próxima busca
            self._caps = None
            return []

        # Normalizado pelo máximo possível (1º lugar em todas as fontes usadas).
        max_score = sum(weights) / (_RRF_K + 1)
        hits = [
            Hit(
                repo=r["repo"], path=r["path"], language=r["language"],
                start_line=r["start_line"], end_line=r["end_line"],
                score=round(float(r["score"]) / max_score, 4), code=r["code"],
                kind=hit_kind(r["language"], r["path"]),
            )
            for r in rows
        ]
        await self._annotate_docs(hits)
        return hits

    async def _small_subset(self, repos: Sequence[str] | None, scope: Scope | None) -> bool:
        """O filtro restringe a busca a poucos trechos (vale a busca vetorial exata, `(dist) + 0` impede o
        HNSW e o Postgres usa o índice por repo)."""
        if not repos and scope is None:
            return False
        names = set(repos or ()) if scope is None else (scope.repos & set(repos) if repos else scope.repos)
        if not names:
            return True
        try:
            n = await self._pool.fetchval(f"SELECT count(*) FROM {self._table} WHERE repo = ANY($1::text[])",
                                          sorted(names))
        except asyncpg.UndefinedTableError:
            return False
        return int(n) <= self._settings.search_exact_max_chunks

    @property
    def skips(self) -> SkipStore:
        return self._skips

    async def _annotate_docs(self, hits: list[Hit]) -> None:
        """Documentos externos: URL original, título, repos ligados e, em página de API, o handler."""
        docs = [(h.repo, h.path) for h in hits if h.kind == "doc"]
        if not docs:
            return
        meta = await self._documents.get_many(docs)
        links = await self._relations.links_for_docs(docs)
        impls = await self._relations.impls_for_docs(docs)
        for h in hits:
            key = (h.repo, h.path)
            if key in meta:
                h.url, h.title = meta[key]["url"] or None, meta[key]["title"] or None
            if key in links:
                h.linked_repos = sorted(links[key])
            if key in impls:
                h.implemented_in = impls[key]

    def _keyword_sql(self, engine: KeywordEngine, p: _Params, terms: Sequence[str], where: str, k: str) -> str | None:
        t = self._table
        if engine == "bm25":
            q = p(" ".join(terms))
            return (
                f"SELECT repo, path, id, pdb.score(id) AS rank FROM {t} "
                # ::text explícito: sem ele o asyncpg infere o parâmetro como pdb.boost (sem typmod) e falha
                f"WHERE (search_symbols ||| {q}::text::pdb.boost({_BM25_SYMBOL_BOOST}) OR search_terms ||| {q}::text) "
                f"AND {where} ORDER BY rank DESC LIMIT {k}"
            )
        tsq = tsquery_or(terms)
        if tsq is None:
            return None
        q = p(tsq)
        if engine == "fts":
            return (
                f"SELECT repo, path, id, ts_rank({_FTS_WEIGHTS}, {FTS_DOCUMENT}, to_tsquery('simple', {q})) AS rank "
                f"FROM {t} WHERE {FTS_DOCUMENT} @@ to_tsquery('simple', {q}) AND {where} "
                f"ORDER BY rank DESC LIMIT {k}"
            )
        # fts-legacy: tabela antiga, sem colunas de busca
        return (
            f"SELECT repo, path, id, ts_rank(to_tsvector('simple', code), to_tsquery('simple', {q})) AS rank "
            f"FROM {t} WHERE to_tsvector('simple', code) @@ to_tsquery('simple', {q}) AND {where} "
            f"ORDER BY rank DESC LIMIT {k}"
        )

    # ------------------------------------------------------------------ contagens

    async def counts_by_repo(self) -> dict[str, tuple[int, int]]:
        """Por repo: (arquivos distintos, chunks). Tabela inexistente (nada indexado ainda) → {}."""
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    f"SELECT repo, COUNT(DISTINCT path) AS f, COUNT(*) AS c FROM {self._table} GROUP BY repo"
                )
            except asyncpg.UndefinedTableError:
                return {}
        return {r["repo"]: (r["f"], r["c"]) for r in rows}
