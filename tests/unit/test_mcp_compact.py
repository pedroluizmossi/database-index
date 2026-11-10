"""list_repos no MCP: saída enxuta, com o resumo do cartão cortado na primeira frase."""

from __future__ import annotations

from code_index.interfaces.http.mcp import _compact_repo


def test_compact_repo() -> None:
    repo = {"name": "o__email", "status": "indexed", "source": "https://x", "pushed_at": "2026", "size_kb": 10,
            "skipped": 0, "error": None, "outdated": False, "files": 3, "tags": ["e-mail"], "card_stale": True,
            "summary": "Envia e-mails dos tenants. Também mantém blocklist."}
    assert _compact_repo(repo) == {"name": "o__email", "status": "indexed", "files": 3, "tags": ["e-mail"],
                                   "summary": "Envia e-mails dos tenants."}
    assert "summary" not in _compact_repo({"name": "x", "summary": None})
    longo = _compact_repo({"summary": "a" * 300})["summary"]
    assert len(longo) == 201 and longo.endswith("…")


def test_page_repos() -> None:
    from code_index.interfaces.http.mcp import page_repos

    rows = [{"name": f"r{i}"} for i in range(5)]
    first = page_repos(rows, limit=2, offset=0)
    assert first["total"] == 5 and [r["name"] for r in first["repos"]] == ["r0", "r1"]
    assert first["next_offset"] == 2 and "offset=2" in first["hint"]
    last = page_repos(rows, limit=2, offset=4)
    assert [r["name"] for r in last["repos"]] == ["r4"] and "next_offset" not in last and "hint" not in last
    assert page_repos(rows, limit=0, offset=-3)["repos"] == [{"name": "r0"}]   # limites saneados
    assert page_repos([], limit=50, offset=0) == {"total": 0, "repos": []}
