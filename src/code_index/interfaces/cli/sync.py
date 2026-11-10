"""CLI: `code-index sync|index`, `watch` e `worker` (indexação e fila de sync)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ...domain.models import SyncResult
from ...services import open_service
from ...settings import Settings
from .common import Subparsers


def _print_result(res: SyncResult) -> None:
    for name, commit in res.indexed.items():
        print(f"ok    {name} @ {(commit or '-')[:7]}")
    for name, err in res.errors.items():
        print(f"ERRO  {name}: {(err.splitlines() or [''])[0][:200]}", file=sys.stderr)
    if res.remaining:
        print(f"{res.remaining} repos ainda na fila (rode de novo ou use `code-index watch`).")


async def _cmd_sync(a: argparse.Namespace, settings: Settings) -> None:
    if a.page:
        if len(a.repos) != 1 or not a.repos[0].startswith("docs__"):
            raise SystemExit("--page exige exatamente uma fonte de documentação, ex.: code-index index docs__wiki --page Plataforma/Email")
        if a.dry_run:
            raise SystemExit("--page não combina com --dry-run (use `code-index docs preview` para ver a seleção)")
        async with open_service(settings) as svc:
            res = await svc.index_doc_pages(a.repos[0], a.page)
        _print_result(res)
        if res.errors:
            sys.exit(1)
        return
    async with open_service(settings) as svc:
        res = await svc.sync(
            a.repos or None,
            batch=None if a.all else a.batch,
            do_discover=not a.no_discover,
            pull=not a.no_pull,
            full=a.full,
            dry_run=a.dry_run,
        )
    if a.dry_run:
        print("seria processado:" if res.selected else "nada a processar.")
        for name in res.selected:
            print(f"  {name}")
        if res.remaining:
            print(f"(+{res.remaining} na fila depois deste lote)")
        return
    _print_result(res)
    # Nomes pedidos: qualquer erro falha. Lote automático: falha quando nada foi indexado (a regra do job da API).
    if (res.errors and a.repos) or res.all_failed:
        sys.exit(1)


async def _cmd_watch(a: argparse.Namespace, settings: Settings) -> None:
    async with open_service(settings) as svc:
        while True:
            try:
                res = await svc.sync(batch=a.batch, progress=False)
                logging.info("lote: %d indexados, %d erros, %d na fila",
                             len(res.indexed), len(res.errors), res.remaining)
            except Exception:
                logging.exception("falha no ciclo de sync")
            await asyncio.sleep(a.interval)


async def _cmd_worker(a: argparse.Namespace, settings: Settings) -> None:
    import signal

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):  # encerra limpo: o job em andamento volta à fila
        loop.add_signal_handler(sig, stop.set)
    async with open_service(settings) as svc:
        n = await svc.run_sync_worker(stop, once=a.once)
    print(f"worker encerrado ({n} job(s) reservado(s)).")


def register(sub: Subparsers, settings: Settings) -> None:
    s = sub.add_parser("sync", aliases=["index"],
                       help="indexa os repos informados, ou um lote dos pendentes/alterados")
    s.add_argument("repos", nargs="*",
                   help="nomes (org__repo, org/repo ou só repo), URL git ou diretório; vazio = lote automático")
    s.add_argument("-b", "--batch", type=int, default=settings.default_batch,
                   help=f"máximo de repos por execução no lote automático (padrão {settings.default_batch})")
    s.add_argument("--all", action="store_true", help="sem limite de lote")
    s.add_argument("--no-discover", action="store_true", help="não consulta a API do GitHub")
    s.add_argument("--no-pull", action="store_true", help="não atualiza os clones")
    s.add_argument("--full", action="store_true", help="reprocessa tudo, ignorando caches (caro!)")
    s.add_argument("--dry-run", action="store_true", help="só mostra o que seria processado (sem custo)")
    s.add_argument("--page", action="append", metavar="CAMINHO",
                   help="fonte de documentação: indexa só esta página (sem idioma, ex.: Plataforma/Email); "
                        "pode repetir. A página precisa estar na seleção da fonte.")
    s.set_defaults(fn=_cmd_sync)

    w = sub.add_parser("watch", help="sync periódico em lotes")
    w.add_argument("-b", "--batch", type=int, default=settings.default_batch)
    w.add_argument("--interval", type=int, default=300, help="segundos entre lotes (padrão 300)")
    w.set_defaults(fn=_cmd_watch)

    wk = sub.add_parser("worker", help="executa os jobs da fila de sync (pedidos pela API/interface)")
    wk.add_argument("--once", action="store_true", help="tenta um job só e sai (sem esperar a fila)")
    wk.set_defaults(fn=_cmd_worker)
