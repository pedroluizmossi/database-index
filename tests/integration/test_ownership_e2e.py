"""Dono no DDD de ponta a ponta: inferência no sync, catálogo, atribuição manual e busca por domínio.

Repos locais (sem rede) cobrindo cada origem: SDL do próprio repo, repo irmão, rotas chamadas, uso de lib e
monorepo com um serviço por pasta. Embeddings falsos (conftest)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


def open_service(settings: Settings) -> Any:
    from code_index.services import open_service as _open

    return _open(settings)


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return root


def make_repos(base: Path) -> dict[str, Path]:
    return {
        "audit-backend": write(base / "audit-backend", {
            "audit.sdl": 'domain platform ("Plataforma")\nservice audit ("Auditoria") {\n  query listEvents {}\n}\n',
            "pom.xml": "<project><artifactId>audit-backend</artifactId><dependencies>"
                       "<dependency><artifactId>trilha-lib</artifactId></dependency></dependencies></project>",
            "src/AuditService.java": "class AuditService {\n  void registrarEventoDeAuditoria() {}\n}\n",
        }),
        "audit-frontend": write(base / "audit-frontend", {
            "src/app.ts": "export function telaDeAuditoria() { return 'eventos'; }\n",
        }),
        "portal-web": write(base / "portal-web", {
            "src/api.ts": "get(`platform/audit/queries/listEvents`);\npost('platform/audit/actions/x');\n"
                          "function portalDeEventos() {}\n",
        }),
        "trilha": write(base / "trilha", {
            "pom.xml": "<project><artifactId>trilha-lib</artifactId></project>",
            "src/Trilha.java": "class Trilha {\n  void gravarTrilhaDeEventos() {}\n}\n",
        }),
        "erp": write(base / "erp", {
            "folha/folha.sdl": 'domain hcm ("RH")\nservice payroll ("Folha") {\n  query calcular {}\n}\n',
            "folha/Calculo.java": "class Calculo {\n  void calcularFolhaDePagamento() {}\n}\n",
            "ferias/ferias.sdl": 'domain hcm ("RH")\nservice vacation ("Férias") {\n  query programar {}\n}\n',
            "ferias/Programacao.java": "class Programacao {\n  void programarFeriasDoColaborador() {}\n}\n",
            "comum/Util.java": "class Util {\n  void utilitarioComumDoErp() {}\n}\n",
        }),
        "legado": write(base / "legado", {
            "contas/Lancamento.java": "class Lancamento {\n  void lancamentoContabilLegado() {}\n}\n",
            "folha/Rubrica.java": "class Rubrica {\n  void rubricaDaFolhaLegada() {}\n}\n",
        }),
    }


async def setup(svc: Any, base: Path) -> None:
    for name, path in make_repos(base).items():
        await svc.add_repo(str(path), name=name)
    await svc.sync(None, batch=None, do_discover=False)


async def test_inferencia_catalogo_e_busca_por_dominio(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    async with open_service(int_settings) as svc:
        await setup(svc, tmp_path)

        owners = {(r["repo"], r["path"]): (r["owners"], r["origin"]) for r in await svc.ownership_list()}
        assert owners[("audit-backend", None)] == (["platform/audit"], "declared")
        assert owners[("audit-frontend", None)] == (["platform/audit"], "sibling")
        assert owners[("portal-web", None)] == (["platform/audit"], "api_calls")
        assert owners[("trilha", None)] == (["platform/audit"], "usage")
        assert owners[("erp", None)] == (["hcm/payroll", "hcm/vacation"], "declared")
        assert owners[("erp", "folha")] == (["hcm/payroll"], "declared")
        assert owners[("erp", "ferias")] == (["hcm/vacation"], "declared")
        assert owners[("legado", None)] == ([], None)

        catalog = await svc.ownership_catalog()
        domains = {d["name"]: d for d in catalog["domains"]}
        assert domains["platform"]["label"] == "Plataforma" and domains["hcm"]["label"] == "RH"
        assert {s["name"]: s["label"] for s in domains["hcm"]["services"]} == {"payroll": "Folha", "vacation": "Férias"}
        assert "transversal" in domains
        assert catalog["coverage"]["repos"] == 6 and catalog["coverage"]["owned"] == 5

        repos = {r.name: r for r in await svc.list_repos()}
        assert repos["portal-web"].owners == ["platform/audit"] and repos["portal-web"].owner_origin == "api_calls"

        # Busca por serviço num monorepo: a pasta do outro serviço fica de fora; a pasta sem dono próprio
        # (comum) segue o repo, que pertence aos dois serviços.
        hits = await svc.search("calcular folha programar ferias utilitario", top_k=10, mode="keyword",
                                domain="hcm", service="payroll", kind="code")
        paths = {(h.repo, h.path) for h in hits}
        assert {("erp", "folha/Calculo.java"), ("erp", "comum/Util.java")} <= paths
        assert not {p for p in paths if p[1].startswith("ferias/")}
        assert {(h.path.split("/")[0], tuple(h.owners)) for h in hits} == {
            ("folha", ("hcm/payroll",)), ("comum", ("hcm/payroll", "hcm/vacation"))}

        # Domínio inteiro: o repo todo (com a pasta comum); outro domínio não aparece.
        hits = await svc.search("eventos auditoria utilitario comum", top_k=20, mode="keyword", domain="hcm",
                                kind="code")
        assert {h.repo for h in hits} == {"erp"}
        hits = await svc.search("eventos auditoria trilha", top_k=20, domain="platform", kind="code")
        assert {h.repo for h in hits} <= {"audit-backend", "audit-frontend", "portal-web", "trilha"} and hits

        with pytest.raises(ValueError, match="quis dizer platform"):
            await svc.search("x", domain="platfrom")
        with pytest.raises(ValueError, match="domínio junto"):
            await svc.search("x", service="audit")


async def test_atribuicao_manual(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    async with open_service(int_settings) as svc:
        await setup(svc, tmp_path)

        with pytest.raises(ValueError, match="desconhecido"):
            await svc.set_owner(None, ["legado"], "financeiro")
        res = await svc.set_owner(None, ["legado"], "financeiro", create=True, reason="ERP antigo")
        assert res == {"updated": 1, "owner": "financeiro", "entities": ["repo:legado"]}
        await svc.set_owner(None, ["legado"], "hcm/payroll", path="folha")
        with pytest.raises(ValueError, match="pasta não encontrada"):
            await svc.set_owner(None, ["legado"], "hcm/payroll", path="nao-existe")
        with pytest.raises(ValueError, match="um repositório por vez"):
            await svc.set_owner(None, ["legado", "erp"], "hcm", path="folha")

        # Manual vence a inferência (o irmão dizia platform/audit) e o resto continua inferido.
        await svc.set_owner(None, ["audit-frontend"], "transversal")
        owners = {(r["repo"], r["path"]): (r["owners"], r["origin"]) for r in await svc.ownership_list()}
        assert owners[("legado", None)] == (["financeiro"], "manual")
        assert owners[("legado", "folha")] == (["hcm/payroll"], "manual")
        assert owners[("audit-frontend", None)] == (["transversal"], "manual")
        alternatives = next(r for r in await svc.ownership_list(repo="audit-frontend") if r["path"] is None)
        assert alternatives["alternatives"] == [{"owner": "platform/audit", "origin": "sibling", "confidence": "alta"}]

        # A pasta do legado entra na busca do serviço; o resto do legado, não.
        hits = await svc.search("rubrica folha lancamento contabil calcular", top_k=10, mode="keyword",
                                domain="hcm", service="payroll", kind="code")
        assert {(h.repo, h.path) for h in hits} >= {("legado", "folha/Rubrica.java")}
        assert ("legado", "contas/Lancamento.java") not in {(h.repo, h.path) for h in hits}

        catalog = await svc.ownership_catalog()
        fin = next(d for d in catalog["domains"] if d["name"] == "financeiro")
        assert fin["repos"] == 1 and catalog["coverage"]["by_origin"]["manual"] == 2

        assert (await svc.clear_owner(None, ["audit-frontend"]))["cleared"] == 1
        back = next(r for r in await svc.ownership_list(repo="audit-frontend") if r["path"] is None)
        assert (back["owners"], back["origin"]) == (["platform/audit"], "sibling")
        assert [r["repo"] for r in await svc.ownership_list(unowned=True)] == []
