"""Trechos pela estrutura de cada formato: empacotamento, seções do markdown, blocos de config e cabeçalho."""

from __future__ import annotations

from code_index.indexing.chunking import (
    chunking_for_tokens, enclosing_member, heading_path, markdown_headings, markdown_spans, package_name, pack_blocks,
    structured_spans,
)
from code_index.indexing.context import Chunking, chunk_header, declarations, full_lines

SMALL = Chunking(size=200, min_size=60, overlap=20, max_chars=400)


def test_alvo_em_tokens_vira_caracteres() -> None:
    c = chunking_for_tokens(800, 200, 1500)
    assert (c.size, c.min_size, c.max_chars) == (2800, 700, 5250) and 0 < c.overlap < c.size


def test_pack_junta_blocos_ate_o_alvo_e_janela_no_bloco_grande() -> None:
    lines = [f"linha {i:03d} " + "x" * 30 for i in range(1, 41)]  # ~41 chars/linha
    spans = pack_blocks(lines, [1, 3, 5, 7, 30], SMALL)
    assert spans[0] == (1, 4)                       # blocos de 2 linhas juntos até ~200 chars
    assert spans[-1][1] == 40                       # cobre tudo
    big = [s for s in spans if s[0] >= 7]           # o bloco 7..29 (~950 chars) virou janelas
    assert len(big) > 2 and all(len(full_lines(lines, a, b)) <= 200 for a, b in big[:-1])


def test_markdown_por_secao_ignorando_bloco_de_codigo_e_com_caminho_dos_titulos() -> None:
    text = (
        "# Autenticação\nIntro curta.\n\n## MFA\n" + "Texto do MFA. " * 20 + "\n```bash\n# não é título\n```\n"
        "### TOTP\n" + "Como configurar o TOTP. " * 12 + "\n## Senhas\n" + "Política de senhas. " * 12 + "\n"
    )
    lines = text.split("\n")
    hs = markdown_headings(lines)
    assert [h.title for h in hs] == ["Autenticação", "MFA", "TOTP", "Senhas"]
    spans = markdown_spans(lines, SMALL)
    starts = {s for s, _ in spans}
    assert {h.line for h in hs if h.title in ("TOTP", "Senhas")} <= starts   # seções grandes começam pedaço novo
    totp = next(h.line for h in hs if h.title == "TOTP")
    assert heading_path(hs, totp, totp + 1) == ["Autenticação", "MFA", "TOTP"]
    senhas = next(h.line for h in hs if h.title == "Senhas")
    assert heading_path(hs, senhas, senhas) == ["Autenticação", "Senhas"]


def test_yaml_por_documento_e_chave_de_topo() -> None:
    doc = ["apiVersion: v1", "kind: Service", "metadata:", "  name: api", "spec:"] + ["  - porta: 80"] * 8
    lines = doc + ["---"] + doc
    spans = structured_spans(lines, "yaml", SMALL)
    assert all(lines[s - 1].strip() == "---" or not lines[s - 1].startswith(" ") for s, _ in spans)
    assert spans[-1][1] == len(lines)


def test_json_por_chave_de_primeiro_nivel() -> None:
    body = ['{', '  "server": {', '    "port": 8080', '  },'] + ['  "a%d": "%s",' % (i, "v" * 60) for i in range(8)] + ['}']
    spans = structured_spans(body, "json", SMALL)
    assert all(body[s - 1].startswith('  "') or s == 1 for s, _ in spans) and len(spans) > 1


def test_properties_pelo_prefixo_da_chave() -> None:
    lines = [f"spring.datasource.p{i}={'x' * 40}" for i in range(3)] + [f"server.p{i}={'y' * 40}" for i in range(3)]
    spans = structured_spans(lines, "properties", Chunking(size=150, min_size=40, overlap=10, max_chars=300))
    assert spans == [(1, 3), (4, 6)]


JAVA = """package br.com.exemplo.auth;

public class MfaService {
    public boolean validateOtp(String codigo,
                               Usuario usuario) {
        int tentativas = repo.tentativas(usuario);
        if (tentativas > max) {
            throw new CodigoInvalido();
        }
        return totp.verifica(codigo);
    }

    public void outro() {
    }
}
"""


def test_cabecalho_com_pacote_e_metodo_envolvente() -> None:
    lines = JAVA.split("\n")
    decls = declarations(JAVA, "java", "MfaService.java")
    assert package_name(JAVA, "java") == "br.com.exemplo.auth"
    member = enclosing_member(lines, decls, 7, "java")          # o if, no meio de validateOtp
    assert member == "public boolean validateOtp(String codigo, Usuario usuario)"
    assert enclosing_member(lines, decls, 13, "java") is None    # começa na declaração de outro método
    header = chunk_header("org__auth", "MfaService.java", "java", decls, 7, 10, package="br.com.exemplo.auth",
                          member=member)
    assert header.splitlines() == [
        "# org__auth/MfaService.java (java)",
        "# pacote: br.com.exemplo.auth",
        "# em: MfaService › public boolean validateOtp(String codigo, Usuario usuario)",
    ]


def test_metodo_envolvente_em_python() -> None:
    src = "class A:\n    def run(self, x):\n        y = x + 1\n        return y\n\n    def b(self):\n        pass\n"
    lines = src.split("\n")
    decls = declarations(src, "python", "a.py")
    assert enclosing_member(lines, decls, 4, "python") == "def run(self, x)"
    assert enclosing_member(lines, decls, 6, "python") is None
    assert package_name(src, "python") is None
