"""Busca por termos (BM25 ou full-text), tabela de símbolos e grep, ponta a ponta com LLM falso.

Roda nos dois bancos: com a imagem pgvector comum (motor "fts") e com a imagem com pg_search
(motor "bm25"), escolhendo via CODE_INDEX_TEST_PG_URL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration

HMAC = """package br.x;

public class HmacServiceImpl {

    public String sign(String payload, String secret) {
        return compute(payload, secret);
    }

    private String compute(String payload, String secret) {
        throw new IllegalStateException("Saldo insuficiente");
    }
}
"""

RETRY = """public class WebhookRetryService {

    public void retryFailedDeliveries() {
        // reenvia entregas que falharam
    }
}
"""

TF = '''resource "keycloak_realm" "demo" {
  realm = "demo"
}
'''


def open_service(settings: Settings) -> Any:
    from code_index.services import open_service as _open

    return _open(settings)


async def _has_pg_search(settings: Settings) -> bool:
    conn = await asyncpg.connect(settings.database_url)
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search'"))
    finally:
        await conn.close()


def make_project(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "HmacServiceImpl.java").write_text(HMAC)
    (root / "src" / "WebhookRetryService.java").write_text(RETRY)
    (root / "main.tf").write_text(TF)
    return root


async def test_busca_por_termos_simbolos_e_grep(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    project = make_project(tmp_path / "proj")
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(project), name="proj")
        res = await svc.sync(["proj"], batch=None, do_discover=False, progress=False)
        assert res.errors == {}

        # motor: BM25 se o banco tem pg_search; senão full-text com pesos
        expected = "bm25" if await _has_pg_search(int_settings) else "fts"
        assert await svc.search_engine() == expected

        # identificador exato e pedaço de identificador (camelCase quebrado na indexação)
        hits = await svc.search("HmacServiceImpl", mode="keyword", rerank=False)
        assert hits and hits[0].path == "src/HmacServiceImpl.java"
        hits = await svc.search("hmac", mode="keyword", rerank=False)
        assert hits and hits[0].path == "src/HmacServiceImpl.java"
        hits = await svc.search("retry failed deliveries", mode="keyword", rerank=False)
        assert hits and hits[0].path == "src/WebhookRetryService.java"

        # símbolos: classe, método qualificado e recurso Terraform
        sym = await svc.find_symbol("HmacServiceImpl")
        assert (sym[0].path, sym[0].kind, sym[0].line) == ("src/HmacServiceImpl.java", "container", 3)
        sym = await svc.find_symbol("HmacServiceImpl.compute")
        assert [(s.name, s.container, s.line) for s in sym] == [("compute", "HmacServiceImpl", 9)]
        assert "compute(String payload" in sym[0].signature
        sym = await svc.find_symbol("keycloak_realm", kind="section")
        assert sym and sym[0].path == "main.tf"
        assert await svc.find_symbol("NaoExiste") == []
        with pytest.raises(ValueError, match="kind inválido"):
            await svc.find_symbol("x", kind="classe")

        # grep pelo serviço: só repos conhecidos, com o nome curto
        g = await svc.grep("Saldo insuficiente", repos=["proj"])
        assert [(m.repo, m.path, m.line) for m in g.matches] == [("proj", "src/HmacServiceImpl.java", 10)]
        with pytest.raises(ValueError, match="desconhecido"):
            await svc.grep("x", repos=["nao-existe"])

        # remover o repo apaga também os símbolos no próximo sync
        await svc.remove_repo("proj")
        await svc.sync(None, batch=None, do_discover=False, progress=False)
        assert await svc.find_symbol("HmacServiceImpl") == []


async def test_tabela_antiga_continua_buscavel(int_settings: Settings) -> None:
    """Antes de reindexar, a tabela não tem as colunas de busca: a busca usa o caminho legado."""
    from code_index.infra.chunk_store import ChunkStore
    from code_index.infra.db import open_pool

    async with open_pool(int_settings) as pool:
        await pool.execute(
            f"""CREATE TABLE {int_settings.chunks_table_qualified} (
                repo text, path text, id bigint, language text, code text,
                start_line bigint, end_line bigint, embedding vector(3), PRIMARY KEY (repo, path, id))"""
        )
        await pool.execute(
            f"INSERT INTO {int_settings.chunks_table_qualified} VALUES "
            "('r', 'a.py', 1, 'python', 'def charge_customer(): pass', 1, 1, '[1,0,0]')"
        )
        store = ChunkStore(pool, int_settings)
        assert (await store.capabilities(refresh=True)).keyword_engine == "fts-legacy"
        hits = await store.hybrid_search(query_vec=None, terms=["charge"], limit=5, per_source=10)
        assert [h.path for h in hits] == ["a.py"]
        # sem colunas de busca, o índice BM25 não é criado (nem quebra)
        assert await store.ensure_bm25_index() is False
