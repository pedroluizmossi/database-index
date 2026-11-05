"""indexing/context: declarações por linguagem, cabeçalho do chunk e linhas inteiras."""

from __future__ import annotations

from code_index.indexing.context import Decl, chunk_bounds, chunk_header, declarations, full_lines

JAVA = """package br.x;

@Service
public class HmacServiceImpl implements HmacService {

    private static final int MINIMUM = 32;

    @Override
    public String sign(String payload, String secret) {
        return compute(payload, secret);
    }

    private String compute(String payload, String secret) {
        if (payload == null) {
            return "";
        }
        return payload;
    }
}
"""

TS = """import { Component } from '@angular/core';

@Component({ selector: 'app-hmac' })
export class HmacConfigComponent implements OnDestroy {
  static readonly STEP = 0;

  constructor(private service: WebhookService) {}

  save(form: FormGroup): void {
    if (form.valid) {
      this.service.update(form.value);
    }
  }
}

export const toKey = (value: string) => value.trim();
export function helper(a: number) { return a; }
"""

PY = """class Pedido:
    def total(self):
        return 1

async def processa(pedido):
    return pedido.total()
"""

HCL = '''resource "keycloak_realm" "senior" {
  realm = "senior"
}

module "clients" {
  source = "./clients"
}
'''

SDL = """service email {
    query healthcheck {
    }
    action sendEmail {
    }
    record emailLog {
    }
}
"""

YAML = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: kong
spec:
  replicas: 1
---
kind: Service
metadata:
  name: kong-proxy
"""

MD = """# Funções

## Deploy

Como publicar.
"""


def names(decls: list[Decl], kind: str | None = None) -> list[str]:
    return [d.name for d in decls if kind is None or d.kind == kind]


def test_java_classe_e_metodos() -> None:
    d = declarations(JAVA, "java", "HmacServiceImpl.java")
    assert names(d, "container") == ["HmacServiceImpl"]
    assert names(d, "member") == ["sign", "compute"]
    assert "if" not in names(d)


def test_typescript_classe_metodos_e_funcoes() -> None:
    d = declarations(TS, "typescript", "hmac-config.component.ts")
    assert names(d, "container") == ["HmacConfigComponent"]
    assert {"save", "toKey", "helper"} <= set(names(d, "member"))
    assert "if" not in names(d) and "constructor" not in names(d)


def test_python() -> None:
    d = declarations(PY, "python", "pedido.py")
    assert names(d) == ["Pedido", "total", "processa"]


def test_terraform_hcl() -> None:
    d = declarations(HCL, "hcl", "main.tf")
    assert names(d) == ["resource keycloak_realm senior", "module clients"]


def test_sdl_pela_extensao() -> None:
    d = declarations(SDL, "", "email.sdl")
    assert names(d) == ["service email", "query healthcheck", "action sendEmail", "record emailLog"]
    assert d[0].kind == "container"


def test_yaml_kubernetes_kind_e_nome() -> None:
    assert names(declarations(YAML, "yaml", "kong.yaml")) == ["Deployment kong", "Service kong-proxy"]


def test_markdown_titulos() -> None:
    assert names(declarations(MD, "markdown", "README.md")) == ["Funções", "Deploy"]


def test_linguagem_desconhecida_nao_quebra() -> None:
    assert declarations("qualquer coisa\n", "", "arquivo.xyz") == []


def test_cabecalho_com_classe_envolvente_e_declaracoes() -> None:
    d = declarations(JAVA, "java", "HmacServiceImpl.java")
    # trecho que começa dentro da classe e contém só o método compute
    header = chunk_header("org__eventos", "src/HmacServiceImpl.java", "java", d, 13, 19)
    assert header == "# org__eventos/src/HmacServiceImpl.java (java)\n# em: HmacServiceImpl · declara: compute"


def test_cabecalho_secao_markdown() -> None:
    d = declarations(MD, "markdown", "README.md")
    assert chunk_header("r", "README.md", "markdown", d, 5, 5) == "# r/README.md (markdown)\n# seção: Deploy"


def test_cabecalho_sem_contexto_so_caminho() -> None:
    assert chunk_header("r", "a.xyz", "", [], 1, 3) == "# r/a.xyz"


def test_linhas_inteiras_e_fim_na_quebra_de_linha() -> None:
    lines = ["um", "dois", "tres", "quatro"]
    assert full_lines(lines, 2, 3) == "dois\ntres"
    # chunk que termina logo após a quebra da linha 2 (coluna 1 da linha 3) não inclui a linha 3
    assert chunk_bounds(1, 3, 1) == (1, 2)
    assert chunk_bounds(1, 3, 5) == (1, 3)
    assert chunk_bounds(2, 2, 1) == (2, 2)  # chunk de uma linha só não encolhe


def test_linha_da_classe_anotada_e_a_do_nome() -> None:
    d = declarations(JAVA, "java", "HmacServiceImpl.java")
    (cls,) = [x for x in d if x.kind == "container"]
    assert JAVA.split("\n")[cls.line - 1].startswith("public class HmacServiceImpl")


def test_metodo_com_modificadores_em_varias_linhas() -> None:
    src = "class A {\n    public\n    static void run(int x) {\n    }\n}\n"
    (run,) = [x for x in declarations(src, "java", "A.java") if x.name == "run"]
    assert run.line == 3


def test_spans_sem_repeticao_de_linha_longa() -> None:
    from code_index.indexing.context import chunk_spans

    lines = ["x" * 5000]
    # o splitter corta a linha em pedaços; todos viram a mesma linha inteira
    assert chunk_spans([(1, 1, 1500), (1, 1, 3000), (1, 1, 5001)], lines) == [(1, 1)]


def test_spans_funde_trecho_pequeno_ao_anterior_e_ao_proximo() -> None:
    from code_index.indexing.context import chunk_spans

    body = [f"    linha_de_codigo_{i} = calcula(valor, {i})" for i in range(40)]
    lines = body + ["  }", "}"]
    spans = chunk_spans([(1, 20, 50), (21, 40, 50), (41, 42, 2)], lines)
    assert spans == [(1, 20), (21, 42)]
    # pequeno no início vai para o próximo
    lines2 = ["import x;", ""] + body
    assert chunk_spans([(1, 2, 1), (3, 42, 50)], lines2) == [(1, 42)]


def test_spans_arquivo_pequeno_inteiro_continua() -> None:
    from code_index.indexing.context import chunk_spans

    assert chunk_spans([(1, 3, 2)], ["a: 1", "b: 2", "c: 3"]) == [(1, 3)]
