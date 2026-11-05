"""Ponta a ponta: repo avulso -> sync (CocoIndex) -> busca -> remoção. Postgres real, embeddings falsos.

Só roda com `pytest -m integration` e pula se o Postgres do docker-compose não responder.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from code_index.infra.db import migrate, open_pool
from code_index.settings import Settings

pytestmark = pytest.mark.integration

SECRET_KEY = "a8f9d0s9f8d0s9f8"
SECRET_DB_PASSWORD = "S3cr3tPw"
TEST_ONLY_WORD = "marcadorexclusivodoteste"

PAYMENTS = f'''"""Cobrança de clientes."""

API_KEY = "{SECRET_KEY}"
DATABASE = "jdbc:postgresql://admin:{SECRET_DB_PASSWORD}@db.internal/app"


def charge_customer(customer, amount):
    """Cobra o cliente e grava o recibo."""
    return customer.charge(amount)
'''

RETRY = '''"""Reconexão com o banco."""


def retry_connection(attempts=3):
    """Retry: tenta reconectar ao banco com espera exponencial."""
    for _ in range(attempts):
        pass
'''

TEST_PAYMENTS = f"def test_charge():\n    assert {TEST_ONLY_WORD} == 1\n"


def make_project(root: Path) -> Path:
    """Projeto de exemplo: dois arquivos de produção e um teste que não deve ser indexado."""
    files = {
        "src/app/payments.py": PAYMENTS,
        "src/app/retry.py": RETRY,
        "tests/test_payments.py": TEST_PAYMENTS,
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def open_service(settings: Settings) -> Any:
    """Import tardio: o CocoIndex só é importado depois de use_settings()/bootstrap() (fixture int_settings)."""
    from code_index.services import open_service as _open

    return _open(settings)


async def chunk_rows(settings: Settings) -> list[dict]:
    conn = await asyncpg.connect(settings.database_url, timeout=5)
    try:
        rows = await conn.fetch(f"SELECT repo, path, code FROM {settings.chunks_table_qualified}")
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def test_repo_avulso_ponta_a_ponta(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    project = make_project(tmp_path / "meu-projeto")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(project), name="meu-projeto")

        result = await svc.sync(["meu-projeto"], batch=None, do_discover=False, progress=False)
        assert result.errors == {}
        assert set(result.indexed) == {"meu-projeto"}

        status = {r.name: r for r in await svc.list_repos()}["meu-projeto"]
        assert status.status == "indexed"
        assert status.files == 2  # payments.py e retry.py; o teste ficou de fora
        assert status.chunks >= 2

        # Segredos mascarados no banco e fora do que vai para o modelo de embedding.
        rows = await chunk_rows(int_settings)
        codes = "\n".join(r["code"] for r in rows)
        assert SECRET_KEY not in codes
        assert SECRET_DB_PASSWORD not in codes
        assert "***" in codes
        assert fake_llm.embedded, "o embedding falso deveria ter sido chamado"
        assert all(SECRET_KEY not in text and SECRET_DB_PASSWORD not in text for text in fake_llm.embedded)

        # Teste excluído pela política de arquivos.
        assert {r["path"] for r in rows} == {"src/app/payments.py", "src/app/retry.py"}
        assert await svc.search(TEST_ONLY_WORD, mode="keyword", rerank=False) == []

        # Busca por palavra-chave encontra o trecho certo.
        hits = await svc.search("charge customer", mode="keyword", rerank=False)
        assert {h.path for h in hits} == {"src/app/payments.py"}
        assert all(SECRET_KEY not in h.code and SECRET_DB_PASSWORD not in h.code for h in hits)

        # Busca híbrida com rerank (falso): só o retry.py casa com os termos, então vai ao topo.
        hybrid = await svc.search("retry espera exponencial", top_k=3)
        assert hybrid and hybrid[0].path == "src/app/retry.py"
        assert fake_llm.rerank_calls >= 1

        with pytest.raises(ValueError, match="mode inválido"):
            await svc.search("charge", mode="bogus")

        # Leitura de arquivo com números de linha e proteção contra path traversal.
        text = await svc.read_file("meu-projeto", "src/app/payments.py", 1, 2)
        assert text.splitlines()[0] == '    1  """Cobrança de clientes."""'
        with pytest.raises(ValueError):
            await svc.read_file("meu-projeto", "../../../../etc/passwd")

        # Remoção: o índice perde os chunks; o diretório do usuário não é apagado.
        assert await svc.remove_repo("meu-projeto") == "removed"
        await svc.sync(None, batch=None, do_discover=False, progress=False)

        assert await chunk_rows(int_settings) == []
        assert "meu-projeto" not in {r.name for r in await svc.list_repos()}
        assert (project / "src" / "app" / "payments.py").is_file()


@contextmanager
def held_lock(path: Path) -> Iterator[None]:
    """Segura o flock do arquivo de lock, como faria outro processo de sync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


async def test_sync_recusa_quando_outro_sync_segura_o_lock(int_settings: Settings) -> None:
    with held_lock(int_settings.lock_file):
        async with open_service(int_settings) as svc:
            with pytest.raises(RuntimeError, match="já existe um sync em andamento"):
                await svc.sync(None, batch=None, do_discover=False, progress=False)


async def test_migracoes_sao_aplicadas_uma_vez(int_settings: Settings) -> None:
    async with open_pool(int_settings) as pool:
        assert await migrate(pool, int_settings) == []  # já aplicadas por open_pool

    conn = await asyncpg.connect(int_settings.database_url, timeout=5)
    try:
        versions = {r["version"] for r in await conn.fetch(
            f'SELECT version FROM "{int_settings.pg_schema}".schema_migrations')}
        columns = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = 'repos'",
            int_settings.pg_schema)}
    finally:
        await conn.close()

    assert "001_repos" in versions
    assert "language" in columns


async def test_chunks_em_linhas_inteiras_com_cabecalho_e_sem_crlf(
    int_settings: Settings, tmp_path: Path, fake_llm: Any
) -> None:
    """O código guardado são linhas inteiras do arquivo (como o read_file devolve), o texto embedado
    leva o cabeçalho de contexto, e CRLF é normalizado. Arquivos .tf entram no índice."""
    project = tmp_path / "proj-crlf"
    (project / "src").mkdir(parents=True)
    java = "".join(
        f"    public int metodo{i}(int a) {{\r\n        return a + {i};\r\n    }}\r\n\r\n" for i in range(60)
    )
    (project / "src" / "Calculadora.java").write_bytes(
        ("package x;\r\n\r\npublic class Calculadora {\r\n" + java + "}\r\n").encode()
    )
    (project / "main.tf").write_text('resource "keycloak_realm" "demo" {\n  realm = "demo"\n}\n')

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(project), name="proj-crlf")
        result = await svc.sync(["proj-crlf"], batch=None, do_discover=False, progress=False)
        assert result.errors == {}

        conn = await asyncpg.connect(int_settings.database_url, timeout=5)
        try:
            rows = await conn.fetch(
                f"SELECT path, code, start_line, end_line FROM {int_settings.chunks_table_qualified} ORDER BY path, start_line"
            )
        finally:
            await conn.close()

        assert {r["path"] for r in rows} == {"src/Calculadora.java", "main.tf"}
        java_rows = [r for r in rows if r["path"] == "src/Calculadora.java"]
        assert len(java_rows) > 1, "o arquivo deveria virar vários chunks"
        file_lines = (project / "src" / "Calculadora.java").read_text().splitlines()
        for r in java_rows:
            assert "\r" not in r["code"]
            # exatamente as linhas start..end do arquivo: nada começa ou termina no meio da linha
            assert r["code"] == "\n".join(file_lines[r["start_line"] - 1: r["end_line"]])

        java_embedded = [t for t in fake_llm.embedded if "Calculadora.java" in t.splitlines()[0]]
        assert java_embedded and all(t.startswith("# proj-crlf/src/Calculadora.java (java)") for t in java_embedded)
        assert any("em: Calculadora" in t and "declara: metodo" in t for t in java_embedded)
        assert any(t.startswith("# proj-crlf/main.tf (hcl)\n# declara: resource keycloak_realm demo") for t in fake_llm.embedded)
