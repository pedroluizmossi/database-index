"""Vetores extras da busca: estado pendente, geração sob pedido e efeito na fusão (LLM falso)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from code_index.retrieval.evaluation import Expected, first_hit
from code_index.settings import Settings

pytestmark = pytest.mark.integration

QUESTION = "Como descobrir qual navegador e qual versão o cliente usa"


def _project(base: Path) -> Path:
    root = base / "portal"
    (root / "src").mkdir(parents=True)
    (root / "src" / "agent.py").write_text(
        "def parse_ua(header):\n    return header.split('/')[-1]\n", encoding="utf-8")
    for i in range(6):   # código que só casa com a consulta pelas palavras soltas
        (root / "src" / f"outro_{i}.py").write_text(
            f"def outro_{i}(cliente, versao):\n    # qual cliente usa qual versao\n    return cliente\n",
            encoding="utf-8")
    return root


async def test_perguntas_pendentes_geradas_e_usadas_na_busca(int_settings: Settings, tmp_path: Path,
                                                             fake_llm: Any) -> None:
    from code_index.services import open_service

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(_project(tmp_path)), name="portal")
        assert (await svc.sync(["portal"], batch=None, do_discover=False, progress=False)).errors == {}
        st = await svc.vector_status()
        assert st["pending"] == 0 and st["kinds"]["question"]["sources"] == 0 and st["last_embedded_at"] is None

        task = await svc.enrich_next(kind="file_summaries", agent="teste", repo="portal")
        assert task is not None
        files = [{"path": p, "resumo": "Lê o cabeçalho do cliente e devolve só o trecho final da identificação.",
                  "termos": ["cabeçalho", "identificação", "texto"], "perguntas": [QUESTION if p == "src/agent.py" else "Para que serve?",
                                                        "Quando é usado?", "O que devolve?"]}
                 for p in task["files"]]
        await svc.enrich_submit(task["id"], json.dumps({"files": files}))

        # o resumo entrou, mas nada foi embedado: tudo pendente, e a busca segue sem esses vetores
        st = await svc.vector_status()
        q = st["kinds"]["question"]
        assert (q["sources"], q["embedded"], q["pending"], q["obsolete"]) == (21, 0, 21, 0)
        assert st["kinds"]["summary"]["pending"] == 7 and st["pending"] == 28   # 7 arquivos: resumo + 3 perguntas
        assert st["pending_estimate"]["chars"] == st["pending_chars"] > 0
        sent = len(fake_llm.embedded)
        assert (await svc.embed_vectors(dry_run=True))["to_embed"] == 28 and len(fake_llm.embedded) == sent
        expect = (Expected("portal", "src/agent.py"),)
        before = first_hit(await svc.search(QUESTION, mode="semantic", rerank=False, top_k=10), expect)

        res = await svc.embed_vectors()
        assert (res["embedded"], res["deleted"]) == (28, 0) and QUESTION in fake_llm.embedded
        st = await svc.vector_status()
        assert st["pending"] == 0 and st["kinds"]["question"]["embedded"] == 21 and st["last_embedded_at"]
        assert (await svc.embed_vectors())["embedded"] == 0          # nada mudou: nada a gerar

        hits = await svc.search(QUESTION, mode="semantic", rerank=False, top_k=10)
        assert first_hit(hits, expect) == 1 and (before is None or before > 1)
        off = await svc._searcher.search(QUESTION, mode="semantic", rerank=False, top_k=10, extra_weights=(0, 0))
        assert first_hit(off, expect) == before                     # desligado = busca de antes

        # vetores de outro modelo (EMBED_MODEL mudou): deixam de valer, voltam a ser pendentes e a busca os ignora
        vt = f'"{int_settings.pg_schema}".search_vectors'
        await svc._chunks.pool.execute(f"UPDATE {vt} SET model = 'voyage/modelo-antigo'")
        st = await svc.vector_status()
        assert st["pending"] == 28 and st["other_model"] == 28
        assert first_hit(await svc.search(QUESTION, mode="semantic", rerank=False, top_k=10), expect) == before
        assert (await svc.embed_vectors())["embedded"] == 28

        # resumo removido: os vetores dele ficam obsoletos e saem na próxima geração, sem custo
        await svc._chunks.pool.execute(
            f'DELETE FROM "{int_settings.pg_schema}".file_summaries WHERE path = $1', "src/agent.py")
        st = await svc.vector_status()
        assert st["kinds"]["question"]["obsolete"] == 3 and st["kinds"]["summary"]["obsolete"] == 1
        assert st["pending"] == 0
        sent = len(fake_llm.embedded)
        assert (await svc.embed_vectors())["deleted"] == 4 and len(fake_llm.embedded) == sent


async def test_cartao_reforca_os_trechos_do_repo(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    summary = "Faturamento de pedidos: emite notas fiscais e cobra o cliente."
    async with open_service(int_settings) as svc:
        for name, comment in (("alfa", "processar pedido do cliente"), ("beta", "beta")):
            root = tmp_path / name
            root.mkdir()
            (root / "main.py").write_text(f"def processar(pedido):\n    # {comment}\n    return pedido\n",
                                          encoding="utf-8")
            await svc.add_repo(str(root), name=name)
        assert (await svc.sync(["alfa", "beta"], batch=None, do_discover=False, progress=False)).errors == {}
        card = {"kind": "service", "summary": summary, "capabilities": ["Emitir nota fiscal"],
                "domain_tags": ["faturamento"]}
        await svc._chunks.pool.execute(
            f"""INSERT INTO "{int_settings.pg_schema}".repo_cards (repo, input_hash, material, card, card_input_hash)
                VALUES ('beta', 'h', '{{}}'::jsonb, $1::jsonb, 'h')
                ON CONFLICT (repo) DO UPDATE SET card = EXCLUDED.card""", json.dumps(card))
        assert (await svc.vector_status())["kinds"]["repo_card"]["pending"] == 1
        assert (await svc.embed_vectors())["embedded"] == 1

        query = f"{summary} processar pedido"
        flat = await svc._searcher.search(query, mode="semantic", rerank=False, top_k=2, extra_weights=(0, 0))
        assert [h.repo for h in flat] == ["alfa", "beta"]                  # sem o cartão, o código de alfa casa mais
        hits = await svc._searcher.search(query, mode="semantic", rerank=False, top_k=2, extra_weights=(0, 0.3))
        assert [h.repo for h in hits] == ["beta", "alfa"]                  # o cartão leva o repo certo ao topo
        far = await svc._searcher.search("processar pedido", mode="semantic", rerank=False, top_k=2,
                                         extra_weights=(0, 0.3))
        assert [h.repo for h in far] == ["alfa", "beta"]                   # cartão sem relação com a consulta não pesa


UA_QUERY = "detectar navegador versão user agent tela de login"


def _portal(base: Path) -> Path:
    root = base / "web"
    (root / "src").mkdir(parents=True)
    (root / "src" / "browser.py").write_text("def ua(h):\n    return h.split('/')\n", encoding="utf-8")
    for i in range(5):
        (root / "src" / f"util_{i}.py").write_text(   # casam com a consulta pelo código: sem extras, vêm antes
            f"def util_{i}(x):\n    # tela de login, versão {i}\n    return x\n", encoding="utf-8")
    return root


async def _summarize(svc: Any, repo: str) -> None:
    task = await svc.enrich_next(kind="file_summaries", agent="teste", repo=repo)
    assert task is not None
    files = []
    for p in task["files"]:
        target = p == "src/browser.py"
        files.append({"path": p,
                      "resumo": ("Detecta o navegador e a versão pelo user agent na tela de login." if target
                                 else "Função auxiliar que devolve o valor recebido sem mudança."),
                      "termos": ["navegador", "user agent", "login"] if target else ["auxiliar", "valor", "entrada"],
                      "perguntas": ["Para que serve?", "Quando é usado?", "O que devolve?"]})
    await svc.enrich_submit(task["id"], json.dumps({"files": files}))


async def test_resumo_embedado_ciclo_de_vida(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    root = _portal(tmp_path)
    expect = (Expected("web", "src/browser.py"),)
    schema = int_settings.pg_schema
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(root), name="web")
        assert (await svc.sync(["web"], batch=None, do_discover=False, progress=False)).errors == {}
        await _summarize(svc, "web")

        # só os resumos (--kind summary): as perguntas continuam pendentes
        res = await svc.embed_vectors(kinds=["summary"])
        assert res["embedded"] == 6 and all(t.startswith("Arquivo src/") for t in fake_llm.embedded[-6:])
        st = await svc.vector_status()
        assert st["kinds"]["summary"]["pending"] == 0 and st["kinds"]["question"]["pending"] == 18
        with pytest.raises(ValueError, match="desconhecido"):
            await svc.embed_vectors(kinds=["outro"])
        # o índice HNSW parcial do modelo nasce na primeira geração
        idx = await svc._chunks.pool.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname LIKE 'search_vectors_hnsw_%'", schema)
        assert idx == 1

        def search(**opts: Any) -> Any:
            return svc._searcher.search(UA_QUERY, mode="semantic", rerank=False, top_k=10, **opts)

        off = first_hit(await search(extra_weights=(0, 0)), expect)
        hits = await search(extra_options={"file_kinds": ("summary",)})
        assert first_hit(hits, expect) == 1 and (off is None or off > 1)
        assert "descrição" in hits[0].matched_by and "vetor" in hits[0].matched_by
        filtered = await search(repos=["web"], extra_options={"file_kinds": ("summary",)})   # busca exata
        assert first_hit(filtered, expect) == 1
        assert first_hit(await search(extra_options={"file_kinds": ("question",)}), expect) == off  # perguntas genéricas

        # o arquivo muda: o resumo fica desatualizado no fim do sync e vota menos (0 = não vota)
        (root / "src" / "browser.py").write_text("def ua(h):\n    return h.split('/')[-1]\n", encoding="utf-8")
        assert (await svc.sync(["web"], batch=None, do_discover=False, progress=False)).errors == {}
        row = await svc._chunks.pool.fetchrow(
            f'SELECT stale, stale_since FROM "{schema}".file_summaries WHERE path = $1', "src/browser.py")
        assert row["stale"] is True and row["stale_since"] is not None
        assert (await svc.vector_status())["stale_summaries"] == 1
        top = (await svc.search(UA_QUERY, mode="semantic", rerank=False, top_k=10))
        assert any(h.path == "src/browser.py" and h.summary_stale for h in top)
        assert all(h.summary_stale is None for h in top if h.path != "src/browser.py")
        muted = await search(extra_options={"file_kinds": ("summary",), "stale_factor": 0.0})
        assert first_hit(muted, expect) == first_hit(await search(extra_weights=(0, 0)), expect)   # o código mudou
        assert all("descrição" not in h.matched_by for h in muted if h.path == "src/browser.py")
        half = first_hit(await search(extra_options={"file_kinds": ("summary",)}), expect)   # 0,5: vota pela metade
        full = first_hit(await search(extra_options={"file_kinds": ("summary",), "stale_factor": 1.0}), expect)
        assert half is not None and full == 1 and full <= half < (first_hit(muted, expect) or 99)

        # novo resumo: volta a ficar em dia
        await _summarize(svc, "web")
        assert not await svc._chunks.pool.fetchval(
            f'SELECT stale FROM "{schema}".file_summaries WHERE path = $1', "src/browser.py")

        # o arquivo sai do repo: no fim do sync o resumo e os vetores dele somem, sem chamar a Voyage
        await svc.embed_vectors()
        (root / "src" / "browser.py").unlink()
        sent = len(fake_llm.embedded)
        assert (await svc.sync(["web"], batch=None, do_discover=False, progress=False)).errors == {}
        assert not any(t.startswith("Arquivo ") or t in ("Para que serve?",) for t in fake_llm.embedded[sent:])
        left = await svc._chunks.pool.fetchval(
            f'SELECT count(*) FROM "{schema}".search_vectors WHERE path = $1', "src/browser.py")
        assert left == 0 and (await svc.vector_status())["obsolete"] == 0

    # geração automática no fim do sync, com teto de caracteres (VECTORS_AUTO_EMBED_MAX_CHARS)
    auto = int_settings.model_copy(update={"vectors_auto_embed_max_chars": 150})
    async with open_service(auto) as svc:
        await svc._chunks.pool.execute(f'UPDATE "{schema}".search_vectors SET model = \'antigo\'')
        total = (await svc.vector_status())["pending"]
        res = await svc.maintain_vectors()
        assert 0 < res["embedded"] < total
        assert (await svc.vector_status())["pending"] == total - res["embedded"]


async def test_fila_de_resumos_pula_gerados_e_curtos(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    root = tmp_path / "app"
    (root / "src-gen").mkdir(parents=True)
    (root / "src").mkdir()
    long_body = "".join(f"    x = x + {i}\n" for i in range(20))
    (root / "src" / "longo.py").write_text(f"def longo(x):\n{long_body}    return x\n", encoding="utf-8")
    (root / "src" / "curto.py").write_text("def curto():\n    return 1\n", encoding="utf-8")
    (root / "src-gen" / "modelo.py").write_text(f"def gerado(x):\n{long_body}    return x\n", encoding="utf-8")
    settings = int_settings.model_copy(update={"summary_min_lines": 15})
    async with open_service(settings) as svc:
        await svc.add_repo(str(root), name="app")
        assert (await svc.sync(["app"], batch=None, do_discover=False, progress=False)).errors == {}
        task = await svc.enrich_next(kind="file_summaries", agent="teste", repo="app")
        assert task is not None and task["files"] == ["src/longo.py"]
        st = (await svc.vector_status())["summary_coverage"]
        assert (st["files"], st["missing"], st["skipped"]) == (3, 1, 2)
