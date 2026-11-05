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
