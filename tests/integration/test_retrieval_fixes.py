"""Correções de consulta/leitura que não exigem reindexar: glob, linguagem, nome curto, linha de símbolo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_index.settings import Settings

pytestmark = pytest.mark.integration


async def test_correcoes_de_consulta(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    root = tmp_path / "proj"
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "main.py").write_text("def iniciar_aplicacao():\n    return 1\n", encoding="utf-8")
    (root / "src" / "app" / "modulo.py").write_text("def iniciar_modulo():\n    return 2\n", encoding="utf-8")
    (root / "Validador.java").write_text(
        "package x;\n\n@Slf4j\n@Service\npublic class CaptchaValidator {\n    public void validar() {}\n}\n",
        encoding="utf-8",
    )
    (root / ".npmrc").write_text("//h/:_authToken=12345678-aaaa-bbbb-cccc-1234567890ab\n", encoding="utf-8")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(root), name="org__proj")
        await svc.sync(None, batch=None, do_discover=False, progress=False)

        # ** pode ser vazio: src/main.py entra em src/**/*.py; src/*.py não desce para src/app
        fundo = await svc.search("iniciar", mode="keyword", rerank=False, top_k=10, path_glob="src/**/*.py")
        assert {h.path for h in fundo} == {"src/main.py", "src/app/modulo.py"}
        raso = await svc.search("iniciar", mode="keyword", rerank=False, top_k=10, path_glob="src/*.py")
        assert {h.path for h in raso} == {"src/main.py"}

        assert await svc.search("iniciar", mode="keyword", rerank=False, language="Python")

        # nome curto (sem a org), como nas outras consultas
        assert "iniciar_aplicacao" in await svc.read_file("proj", "src/main.py")
        with pytest.raises(ValueError, match="arquivo não encontrado"):
            await svc.read_file("proj", ".npmrc")
        with pytest.raises(ValueError, match="repositório desconhecido"):
            await svc.read_file("nao-existe", "src/main.py")

        # classe anotada: linha da declaração, não da anotação
        (sym,) = [s for s in await svc.find_symbol("CaptchaValidator", kind="container")]
        assert (sym.line, sym.signature) == (5, "public class CaptchaValidator {")


async def test_indexacao_sdl_minificado_e_acentos(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    root = tmp_path / "svc"
    root.mkdir()
    (root / "auth.sdl").write_text(
        'service auth {\n  "Realiza a validação do login"\n  public action login {\n  }\n}\n', encoding="utf-8")
    (root / "meta.json").write_text('{"a":' + '"x",' * 9000 + '"b":1}', encoding="utf-8")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(root), name="svc")
        await svc.sync(None, batch=None, do_discover=False, progress=False)

        (hit,) = await svc.search("validacao", mode="keyword", rerank=False, language="sdl")
        assert hit.path == "auth.sdl" and hit.language == "sdl"
        assert await svc.search("validação", mode="keyword", rerank=False)

        skipped = await svc.skipped_files("svc")
        assert any(s["path"] == "meta.json" and s["reason"] == "minificado" for s in skipped)


async def test_find_symbol_membros_e_fallback(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.services import open_service

    root = tmp_path / "sym"
    root.mkdir()
    (root / "Pedido.java").write_text(
        "public class PedidoService {\n    public void criar() {}\n    public void cancelar() {}\n}\n", encoding="utf-8")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(root), name="sym")
        await svc.sync(None, batch=None, do_discover=False, progress=False)

        membros = await svc.find_symbol("PedidoService.*")
        assert [m.name for m in membros] == ["criar", "cancelar"]
        # classe errada no nome qualificado: ainda acha o método
        (m,) = await svc.find_symbol("ClasseErrada.cancelar")
        assert (m.name, m.container) == ("cancelar", "PedidoService")
        with pytest.raises(ValueError, match="Classe.\\*"):
            await svc.find_symbol("*")


async def test_variacoes_do_rerank(int_settings: Settings, tmp_path: Path, fake_llm: Any,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    from code_index.retrieval.search import RERANK_INSTRUCTION, RerankOptions
    from code_index.services import open_service

    root = tmp_path / "rr"
    root.mkdir()
    (root / "Token.java").write_text(
        "public class TokenService {\n    public String renovarToken(String refresh) {\n        return refresh;\n    }\n}\n",
        encoding="utf-8")
    (root / "README.md").write_text("# Token\n\nComo renovar o token usando o refresh token.\n", encoding="utf-8")
    (root / "Refresh.java").write_text(
        "public class RefreshHandler {\n    public String token(String refresh) {\n        return refresh;\n    }\n}\n",
        encoding="utf-8")

    sent: list[dict] = []
    original = fake_llm.arerank

    async def spy(*args: Any, **kwargs: Any) -> Any:
        sent.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(litellm, "arerank", spy)
    async with open_service(int_settings) as svc:
        await svc.add_repo(str(root), name="rr")
        await svc.sync(None, batch=None, do_discover=False, progress=False)

        async def run(**opts: bool) -> list[Any]:
            sent.clear()
            return await svc._searcher.search("renovar token", top_k=5, rerank=True,
                                              rerank_options=RerankOptions(**opts))

        hits = await run(instruction=True, header=True)
        assert {"Token.java", "README.md"} <= {h.path for h in hits}
        assert sent[0]["query"].startswith(RERANK_INSTRUCTION)
        java = next(d for d in sent[0]["documents"] if "renovarToken" in d)
        assert java.startswith("# rr/Token.java (java)\n# em: TokenService") or "declara: TokenService" in java

        hits = await run(code_only=True)
        assert len(sent[0]["documents"]) == 2 and not any("README" in d for d in sent[0]["documents"])
        assert "README.md" in {h.path for h in hits}  # a doc continua no resultado, na posição híbrida


async def test_identificador_nao_vai_ao_reranker(int_settings: Settings, tmp_path: Path, fake_llm: Any) -> None:
    from code_index.retrieval.search import RerankOptions
    from code_index.services import open_service

    root = tmp_path / "idt"
    root.mkdir()
    (root / "A.java").write_text("public class PedidoService {\n    void criar() {}\n}\n", encoding="utf-8")
    (root / "B.java").write_text("public class PedidoRepository {\n    void salvar() {}\n}\n", encoding="utf-8")

    async with open_service(int_settings) as svc:
        await svc.add_repo(str(root), name="idt")
        await svc.sync(None, batch=None, do_discover=False, progress=False)
        pula = RerankOptions(instruction=True, skip_identifiers=True)

        fake_llm.rerank_calls = 0
        hits = await svc._searcher.search("PedidoService", rerank=True, rerank_options=pula)
        assert hits[0].path == "A.java" and fake_llm.rerank_calls == 0

        await svc._searcher.search("onde o pedido é criado", rerank=True, rerank_options=pula)
        assert fake_llm.rerank_calls == 1  # frase continua indo ao reranker

        await svc._searcher.search("PedidoService", rerank=True, rerank_options=RerankOptions())
        assert fake_llm.rerank_calls == 2  # sem a opção, identificador também vai
