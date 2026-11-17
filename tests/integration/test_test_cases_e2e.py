"""Testes fora do índice, mas acessíveis: leitura no sync, find_tests, contagem na busca e grep com testes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


def _project(base: Path) -> Path:
    root = base / "auth"
    main = root / "src" / "main" / "java" / "x"
    test = root / "src" / "test" / "java" / "x"
    main.mkdir(parents=True)
    test.mkdir(parents=True)
    (main / "AuthService.java").write_text(
        "package x;\n\npublic class AuthService {\n    public void lockAccount(String user) {\n    }\n\n"
        "    public String refresh(String token) {\n        return token;\n    }\n}\n", encoding="utf-8")
    (test / "AuthServiceTest.java").write_text(
        "package x;\n\nclass AuthServiceTest {\n    @Test\n    void deveBloquearContaAposCincoFalhas() {\n"
        "        new AuthService().lockAccount(\"maria\");\n        String senha = \"password=hunter2\";\n    }\n\n"
        "    @Test\n    @DisplayName(\"renova o token expirado\")\n    void renova() {\n"
        "        new AuthService().refresh(\"t\");\n    }\n}\n", encoding="utf-8")
    web = root / "web"
    web.mkdir()
    (web / "format.ts").write_text("export function formatName(n: string) {\n  return n.trim();\n}\n", encoding="utf-8")
    (web / "format.spec.ts").write_text(
        'import { formatName } from "./format";\ndescribe("formatName", () => {\n'
        '  it("remove espaços das pontas", () => { formatName(" a "); });\n});\n', encoding="utf-8")
    return root


async def test_testes_lidos_no_sync_e_acessiveis_sem_entrar_na_busca(int_settings: Settings, tmp_path: Path,
                                                                     fake_llm: Any) -> None:
    from code_index.services import open_service

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(_project(tmp_path)), name="auth")
        assert (await svc.sync(["auth"], batch=None, do_discover=False, progress=False)).errors == {}
        # os testes não entram no índice de trechos nem nos embeddings
        assert not any("Test" in t or "deveBloquear" in t or ".spec." in t for t in fake_llm.embedded)
        st = await svc.tests_status()
        assert (st["repos"], st["files"], st["cases"], st["linked"]) == (1, 2, 3, 3)

        # B: find_tests por método, classe, arquivo e nome
        res = await svc.find_tests("AuthService.lockAccount")
        assert res["matched_by"] == "membro" and res["total"] == 1
        (t,) = res["tests"]
        assert t["name"] == "AuthServiceTest > deveBloquearContaAposCincoFalhas" and t["line"] == 5
        assert t["path"] == "src/test/java/x/AuthServiceTest.java"
        assert t["tests_file"] == "src/main/java/x/AuthService.java" and t["calls"] == ["lockAccount"]
        res = await svc.find_tests("AuthService")
        assert res["matched_by"] == "classe" and [x["name"] for x in res["tests"]] == [
            "AuthServiceTest > deveBloquearContaAposCincoFalhas", "AuthServiceTest > renova o token expirado"]
        assert (await svc.find_tests("web/format.ts"))["tests"][0]["name"] == "formatName > remove espaços das pontas"
        assert (await svc.find_tests("formatName"))["total"] == 1
        assert (await svc.find_tests("token expirado"))["matched_by"] == "nome do teste"
        assert (await svc.find_tests("Inexistente"))["tests"] == []
        with pytest.raises(ValueError, match="desconhecido"):              # como na busca: repo errado é erro
            await svc.find_tests("AuthService", repos=["outro-repo"])

        # D: a busca não devolve testes, mas diz quantos cobrem o arquivo
        hits = await svc.search("lockAccount refresh AuthService", mode="keyword", rerank=False, top_k=20)
        assert hits and not any("/test/" in h.path or ".spec." in h.path for h in hits)
        by_path = {h.path: h.tests for h in hits}
        assert by_path["src/main/java/x/AuthService.java"] == 2
        fmt = await svc.search("formatName trim", mode="keyword", rerank=False)
        assert {h.path: h.tests for h in fmt}.get("web/format.ts") == 1

        # A: grep só vê testes quando pedido, e com a máscara de segredos
        assert not (await svc.grep("deveBloquearConta")).matches
        found = (await svc.grep("deveBloquearConta", include_tests=True)).matches
        assert [(m.path, m.line) for m in found] == [("src/test/java/x/AuthServiceTest.java", 5)]
        secret = (await svc.grep("password=", include_tests=True)).matches
        assert secret and "hunter2" not in secret[0].text

        # sem mudança no repo, o próximo sync não relê; --force relê
        assert (await svc.scan_tests())["repos"] == 0
        assert (await svc.scan_tests(force=True))["repos"] == 1
