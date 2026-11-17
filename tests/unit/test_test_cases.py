"""domain.test_cases: arquivo de teste, sujeito, extração dos casos por linguagem e ligação ao código."""

from __future__ import annotations

from code_index.domain.file_patterns import DEFAULT_EXCLUDE
from code_index.domain.test_cases import (
    Declaration, extract_test_cases, find_target, is_test_path, link_file, subject_of, without_test_excludes,
)


def names(text: str, path: str) -> list[tuple[int, str]]:
    return [(c.line, c.display) for c in extract_test_cases(text, path)]


def test_reconhece_arquivo_de_teste_e_tira_so_os_padroes_de_teste() -> None:
    assert is_test_path("src/test/java/x/AuthServiceTest.java")
    assert is_test_path("app/__tests__/login.tsx") and is_test_path("pkg/auth_test.go")
    assert is_test_path("Api.Tests/LoginTests.cs") and is_test_path("tests/test_auth.py")
    assert not is_test_path("src/main/java/x/AuthService.java") and not is_test_path("src/contest.py")
    rest = without_test_excludes(DEFAULT_EXCLUDE)
    assert "**/node_modules" in rest and "**/.*" in rest
    assert "**/test" not in rest and "**/*Test.java" not in rest


def test_sujeito_pelo_nome_do_arquivo() -> None:
    assert subject_of("src/test/AuthServiceTest.java") == "AuthService"
    assert subject_of("x/AuthServiceTests.cs") == "AuthService"
    assert subject_of("x/AuthServiceIT.java") == "AuthService"
    assert subject_of("src/auth.service.spec.ts") == "auth.service"
    assert subject_of("tests/test_auth.py") == "auth" and subject_of("pkg/auth_test.go") == "auth"
    assert subject_of("spec/user_spec.rb") == "user"
    assert subject_of("src/test/TestHelpers.java") is None and subject_of("tests/conftest.py") is None


def test_junit_com_display_name_classe_aninhada_e_parametrizado() -> None:
    java = """class AuthServiceTest {
    @Test
    void deveBloquearContaAposCincoFalhas() {
        service.lockAccount(user);
    }

    @Nested
    class Renovacao {
        @ParameterizedTest
        @ValueSource(strings = {"a"})
        @DisplayName("renova o token expirado")
        void renova(String s) { service.refresh(s); }
    }

    public void testLegado() {}
}
"""
    cases = extract_test_cases(java, "src/test/AuthServiceTest.java")
    assert [(c.line, c.display) for c in cases] == [
        (3, "AuthServiceTest > deveBloquearContaAposCincoFalhas"),
        (12, "AuthServiceTest > Renovacao > renova o token expirado"),
        (15, "AuthServiceTest > testLegado"),
    ]
    assert "lockAccount" in cases[0].idents and "refresh" not in cases[0].idents
    assert "refresh" in cases[1].idents


def test_kotlin_csharp_e_spock() -> None:
    kt = 'class LoginTest {\n    @Test\n    fun `recusa senha vazia`() {\n    }\n}\n'
    assert names(kt, "a/LoginTest.kt") == [(3, "LoginTest > recusa senha vazia")]
    cs = "public class LoginTests {\n    [Fact]\n    public async Task RecusaSenhaVazia() {\n    }\n}\n"
    assert names(cs, "a/LoginTests.cs") == [(3, "LoginTests > RecusaSenhaVazia")]
    groovy = 'class LoginSpec extends Specification {\n    def "recusa senha vazia"() {\n    }\n}\n'
    assert names(groovy, "a/LoginSpec.groovy") == [(2, "LoginSpec > recusa senha vazia")]


def test_js_describe_aninhado_each_e_skip() -> None:
    ts = """import { lock } from "./auth";
describe("auth", () => {
  describe('bloqueio', () => {
    it("trava após 5 falhas", () => { lock(user) });
    it.skip(`ignora admin`, () => {});
  });
  test.each([[1], [2]])("aceita %s", (n) => {});
});
it("fora de describe", () => {});
"""
    assert names(ts, "src/auth.spec.ts") == [
        (4, "auth > bloqueio > trava após 5 falhas"),
        (5, "auth > bloqueio > ignora admin"),
        (7, "auth > aceita %s"),
        (9, "fora de describe"),
    ]


def test_python_go_ruby_e_php() -> None:
    py = "def test_solto():\n    pass\n\nclass TestLogin:\n    def test_recusa(self):\n        pass\n    def helper(self):\n        pass\n"
    assert names(py, "tests/test_login.py") == [(1, "test_solto"), (5, "TestLogin > test_recusa")]
    go = ('func TestLogin(t *testing.T) {\n\tt.Run("senha vazia", func(t *testing.T) {})\n}\n'
          "func helper() {\n\tt.Run(\"nao conta\", nil)\n}\n")
    assert names(go, "pkg/login_test.go") == [(1, "TestLogin"), (2, "TestLogin > senha vazia")]
    rb = "RSpec.describe User do\n  context 'sem senha' do\n    it 'é inválido' do\n    end\n  end\nend\n"
    assert names(rb, "spec/user_spec.rb") == [(3, "User > sem senha > é inválido")]
    php = "class LoginTest extends TestCase {\n    public function testRecusa() {}\n    /** @test */\n    public function aceita() {}\n}\n"
    assert names(php, "tests/LoginTest.php") == [(2, "testRecusa"), (4, "aceita")]
    assert extract_test_cases("qualquer coisa", "a/LoginTest.swift") == []


DECLS = [
    Declaration("AuthService", "svc/src/main/java/x/AuthService.java", "container"),
    Declaration("lockAccount", "svc/src/main/java/x/AuthService.java", "member", "AuthService"),
    Declaration("refresh", "svc/src/main/java/x/AuthService.java", "member", "AuthService"),
    Declaration("AuthService", "outro/src/main/java/y/AuthService.java", "container"),
    Declaration("TokenStore", "svc/src/main/java/x/TokenStore.java", "container"),
]


def test_liga_pelo_nome_escolhendo_o_mais_proximo_e_os_metodos_chamados() -> None:
    java = "class AuthServiceTest {\n  @Test\n  void trava() { service.lockAccount(u); }\n  @Test\n  void nada() {}\n}\n"
    path = "svc/src/test/java/x/AuthServiceTest.java"
    linked = link_file(path, extract_test_cases(java, path), DECLS, [])
    assert (linked.subject, linked.target_path, linked.how) == ("AuthService", "svc/src/main/java/x/AuthService.java", "nome")
    assert [(c.name, called) for c, called in linked.cases] == [("trava", ["lockAccount"]), ("nada", [])]


def test_liga_modulo_js_pelo_arquivo_e_sem_nome_pelas_citacoes() -> None:
    assert find_target("src/auth.service.spec.ts", [], [], ["src/auth.service.ts", "src/user.ts"]) == (
        "auth.service", "src/auth.service.ts", "nome")
    java = ("class FluxoIT {\n  @Test\n  void fluxo() { TokenStore t; AuthService a; }\n"
            "  @Test\n  void outro() { TokenStore t; }\n}\n")
    path = "svc/src/test/java/x/integracao/Fluxo.java"
    assert find_target(path, extract_test_cases(java, path), DECLS, []) == (None, "svc/src/main/java/x/TokenStore.java", "citações")
    empate = "class X {\n  @Test\n  void f() { TokenStore t; AuthService a; }\n}\n"
    assert find_target(path, extract_test_cases(empate, path), DECLS, [])[1] is None   # empate: não liga


def test_liga_por_prefixo_so_com_citacao_ou_pacote_unico() -> None:
    decls = [
        Declaration("RefreshTokenHandler", "svc/src/main/java/x/handler/RefreshTokenHandler.java", "container"),
        Declaration("RefreshTokenValidator", "svc/src/main/java/x/handler/RefreshTokenValidator.java", "container"),
        Declaration("RefreshTokenHandler", "svc/src/main/java/x/outro/RefreshTokenHandler.java", "container"),
        Declaration("UserMapper", "svc/src/main/java/x/user/UserMapper.java", "container"),
    ]
    path = "svc/src/test/java/x/handler/RefreshTokenTest.java"
    java = "class RefreshTokenTest {\n  @Test\n  void renova() { new RefreshTokenHandler().handle(); }\n}\n"
    assert find_target(path, extract_test_cases(java, path), decls, []) == (
        "RefreshToken", "svc/src/main/java/x/handler/RefreshTokenHandler.java", "prefixo")
    # sem citação e com duas classes no mesmo pacote: não liga
    sem_citacao = "class RefreshTokenTest {\n  @Test\n  void renova() { chama(); }\n}\n"
    assert find_target(path, extract_test_cases(sem_citacao, path), decls, [])[1] is None
    # sem citação, mas única no pacote: liga
    path2 = "svc/src/test/java/x/user/UserTest.java"
    assert find_target(path2, extract_test_cases("class UserTest {\n  @Test\n  void a() {}\n}\n", path2), decls, [])[1] is None
    path3 = "svc/src/test/java/x/user/UserMapTest.java"
    assert find_target(path3, extract_test_cases("class UserMapTest {\n  @Test\n  void a() {}\n}\n", path3), decls, [])[1] == (
        "svc/src/main/java/x/user/UserMapper.java")
