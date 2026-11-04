"""Consultas: busca híbrida (vetor + full-text, fusão RRF), leitura de arquivos e status."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

import asyncpg
import litellm
from pgvector.asyncpg import register_vector

from .config import DATABASE_URL, PG_SCHEMA, RERANK_CANDIDATES, RERANK_MODEL, TABLE_NAME, local_path_for
from .embedding import embed_query

_T = f'"{PG_SCHEMA}"."{TABLE_NAME}"'
_RRF_K = 60
_RERANK_DOC_CHARS = 6000
log = logging.getLogger(__name__)

_STOPWORDS = frozenset("""
a o e é de da do das dos em no na nos nas um uma uns umas para por com sem que se como onde qual quais
ao aos à às ou mais menos ser está são foi feito feita sobre entre seu sua seus suas isso este esta
the an and or of to in on for with from by is are be was how where what which when does do this that
""".split())


def _keyword_tsquery(query: str) -> str | None:
    """Termos significativos unidos por OR: frases em linguagem natural ainda casam
    parcialmente, e o ts_rank prioriza trechos que contêm mais termos."""
    terms = []
    for t in re.findall(r"\w+", query.lower()):
        if len(t) >= 3 and t not in _STOPWORDS and t not in terms:
            terms.append(t)
    return " | ".join(terms) or None


@dataclass
class Hit:
    repo: str
    path: str
    language: str
    start_line: int
    end_line: int
    score: float
    code: str

    def to_dict(self) -> dict:
        return asdict(self)


async def _connect() -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    await register_vector(conn)
    return conn


class _Params:
    def __init__(self) -> None:
        self.values: list = []

    def __call__(self, value: object) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


def _filters(p: _Params, repos: list[str] | None, language: str | None, path_glob: str | None) -> str:
    clauses = []
    if repos:
        clauses.append(f"repo = ANY({p(repos)})")
    if language:
        clauses.append(f"language = {p(language)}")
    if path_glob:
        like = path_glob.replace("%", r"\%").replace("_", r"\_").replace("**", "*").replace("*", "%").replace("?", "_")
        clauses.append(f"path LIKE {p(like)}")
    return " AND ".join(clauses) or "TRUE"


async def search(
    query: str,
    *,
    top_k: int = 10,
    repos: list[str] | None = None,
    language: str | None = None,
    path_glob: str | None = None,
    mode: str = "hybrid",
    rerank: bool = True,
    candidates: int | None = None,
) -> list[Hit]:
    """Busca híbrida (vetor + full-text, RRF) seguida de rerank.

    mode: 'hybrid' (padrão), 'semantic' ou 'keyword'.
    rerank: reordena `candidates` resultados com RERANK_MODEL e devolve os `top_k` melhores.
    """
    if mode not in ("hybrid", "semantic", "keyword"):
        raise ValueError(f"mode inválido: {mode}")
    use_vec = mode in ("hybrid", "semantic")
    use_kw = mode in ("hybrid", "keyword")
    rerank = rerank and bool(RERANK_MODEL)
    fetch_n = max(top_k, candidates or RERANK_CANDIDATES) if rerank else top_k
    p = _Params()
    k = p(max(fetch_n * 2, 40))
    where = _filters(p, repos, language, path_glob)

    parts = []
    if use_vec:
        v = p(await embed_query(query))
        parts.append(f"""
            SELECT repo, path, id, row_number() OVER (ORDER BY embedding <=> {v}) AS rnk
            FROM (SELECT repo, path, id, embedding FROM {_T} WHERE {where}
                  ORDER BY embedding <=> {v} LIMIT {k}) v""")
    tsq = _keyword_tsquery(query) if use_kw else None
    if tsq:
        q = p(tsq)
        parts.append(f"""
            SELECT repo, path, id, row_number() OVER (ORDER BY rank DESC) AS rnk
            FROM (SELECT repo, path, id,
                         ts_rank(to_tsvector('simple', code), to_tsquery('simple', {q})) AS rank
                  FROM {_T}
                  WHERE to_tsvector('simple', code) @@ to_tsquery('simple', {q}) AND {where}
                  ORDER BY rank DESC LIMIT {k}) k""")
    if not parts:
        return []
    sql = f"""
        WITH ranked AS ({" UNION ALL ".join(parts)}),
        fused AS (SELECT repo, path, id, SUM(1.0 / ({_RRF_K} + rnk)) AS score
                  FROM ranked GROUP BY repo, path, id)
        SELECT t.repo, t.path, t.language, t.start_line, t.end_line, t.code, f.score
        FROM fused f JOIN {_T} t USING (repo, path, id)
        ORDER BY f.score DESC LIMIT {int(fetch_n)}"""

    conn = await _connect()
    try:
        async with conn.transaction():
            if use_vec:
                # pgvector >= 0.8: continua varrendo o HNSW quando filtros descartam candidatos.
                await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            rows = await conn.fetch(sql, *p.values)
    finally:
        await conn.close()

    max_score = (2.0 if mode == "hybrid" else 1.0) / (_RRF_K + 1)
    hits = [
        Hit(
            repo=r["repo"], path=r["path"], language=r["language"],
            start_line=r["start_line"], end_line=r["end_line"],
            score=round(float(r["score"]) / max_score, 4), code=r["code"],
        )
        for r in rows
    ]
    if rerank and len(hits) > 1:
        try:
            return await _rerank(query, hits, top_k)
        except Exception as e:  # noqa: BLE001 - rerank é melhoria; sem ele, fica a ordem híbrida
            log.warning("rerank falhou (%s); usando ordem da busca híbrida", e)
    return hits[:top_k]


async def _rerank(query: str, hits: list[Hit], top_k: int) -> list[Hit]:
    docs = [f"{h.repo}/{h.path}\n{h.code[:_RERANK_DOC_CHARS]}" for h in hits]
    resp = await litellm.arerank(model=RERANK_MODEL, query=query, documents=docs, top_n=top_k)
    out = []
    for r in resp.results:
        h = hits[r["index"]]
        h.score = round(float(r["relevance_score"]), 4)
        out.append(h)
    return out


def read_file(repo: str, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Lê um arquivo do clone local (com proteção contra path traversal)."""
    try:
        root = local_path_for(repo).resolve()
    except ValueError:
        raise ValueError(f"repositório desconhecido: {repo}") from None
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"arquivo não encontrado: {repo}/{path}")
    lines = target.read_text(errors="replace").splitlines()
    s = max(1, start_line or 1)
    e = min(len(lines), end_line or len(lines))
    return "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))


async def repo_status(status: str | None = None, org: str | None = None, name: str | None = None) -> list[dict]:
    """Estado de cada repo (tabela de estado + contagem de chunks no índice)."""
    from . import state

    conn = await state.connect()
    try:
        rows = await state.all_rows(conn)
        counts: dict[str, tuple[int, int]] = {}
        try:
            for r in await conn.fetch(f"SELECT repo, COUNT(DISTINCT path) f, COUNT(*) c FROM {_T} GROUP BY repo"):
                counts[r["repo"]] = (r["f"], r["c"])
        except asyncpg.UndefinedTableError:
            pass
    finally:
        await conn.close()
    out = []
    for r in sorted(rows.values(), key=lambda r: r.name):
        if (status and r.status != status) or (org and r.org != org) or (name and name not in r.name):
            continue
        files, chunks = counts.get(r.name, (0, 0))
        out.append({
            "name": r.name, "org": r.org, "status": r.status, "language": r.language, "source": r.url or r.path,
            "commit": r.indexed_commit[:7] if r.indexed_commit else None,
            "indexed_at": r.indexed_at.astimezone().isoformat(timespec="seconds") if r.indexed_at else None,
            "pushed_at": r.pushed_at.astimezone().isoformat(timespec="seconds") if r.pushed_at else None,
            "outdated": r.changed_upstream, "files": files, "chunks": chunks, "error": r.error,
        })
    return out
