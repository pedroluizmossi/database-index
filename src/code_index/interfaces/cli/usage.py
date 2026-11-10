"""CLI: `code-index usage` (relatório de uso: MCP, API e interface)."""

from __future__ import annotations

import argparse

from ...services import open_service
from ...settings import Settings
from .common import Subparsers, print_json


def _fmt_int(n: float | int | None) -> str:
    return f"{int(n or 0):,}".replace(",", ".")


async def _cmd_usage(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        rep = await svc.usage_report(a.days, email=a.email)
    if a.json:
        print_json(rep, default=str)
        return
    t = rep["totals"]
    who = f" de {a.email}" if a.email else ""
    print(f"Uso{who} nos últimos {rep['days']} dias")
    print(f"  chamadas {_fmt_int(t['calls'])} (MCP {_fmt_int(t['mcp_calls'])}), erros {_fmt_int(t['errors'])}, "
          f"usuários {t['users']}, clientes {t['clients']}")
    print(f"  latência média {t['avg_ms']:.0f} ms, p95 {t['p95_ms']:.0f} ms; sem resultado: {_fmt_int(t['empty_results'])}")
    print(f"  Voyage: {_fmt_int(t['embed_tokens'])} tokens de embedding, {_fmt_int(t['rerank_tokens'])} de rerank, "
          f"US$ {t['cost_usd']:.4f}; contexto devolvido a agents (MCP) ~{_fmt_int(t['context_tokens'])} tokens")
    print()
    print(f"  {'canal':<5} {'ação':<40} {'chamadas':>8} {'erros':>6} {'p95 ms':>7} {'~tokens/resp':>12} {'US$':>8}")
    for r in rep["by_action"][:25]:
        print(f"  {r['channel']:<5} {r['action'][:40]:<40} {r['calls']:>8} {r['errors']:>6} {r['p95_ms']:>7.0f} "
              f"{r['avg_context_tokens'] if r['avg_context_tokens'] is not None else '-':>12} {r['cost_usd']:>8.4f}")
    if rep["by_client"]:
        print()
        print("  clientes: " + ", ".join(f"{c['client']} ({c['calls']})" for c in rep["by_client"][:10]))
    if rep["empty_queries"]:
        print()
        print("  consultas sem resultado:")
        for q in rep["empty_queries"][:10]:
            print(f"    {q['calls']:>4}x  {q['action']:<16} {q['query'][:80]}")


def register(sub: Subparsers, settings: Settings) -> None:
    ug = sub.add_parser("usage", help="relatório de uso: tools, clientes, custo da Voyage, consultas sem resultado")
    ug.add_argument("--days", type=int, default=30)
    ug.add_argument("--email", help="só o uso deste usuário")
    ug.add_argument("--json", action="store_true", help="relatório completo em JSON")
    ug.set_defaults(fn=_cmd_usage)
