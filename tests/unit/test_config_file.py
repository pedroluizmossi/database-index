"""ConfigFile (repos.toml): roundtrip, formato enxuto, validações ao ler. Só usa tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_index.domain.models import Org, Repo
from code_index.infra.config_file import ConfigFile


def test_arquivo_inexistente_e_configuracao_vazia(tmp_path: Path) -> None:
    cfg = ConfigFile(tmp_path / "repos.toml")

    assert cfg.load_repos() == []
    assert cfg.load_orgs() == []


def test_roundtrip_de_repos_e_orgs(tmp_path: Path) -> None:
    cfg = ConfigFile(tmp_path / "repos.toml")
    repos = [
        Repo(name="avulso", url="https://github.com/acme/avulso.git", branch="dev",
             extra_exclude=("**/out", "**/gen")),
        Repo(name="local", path=str(tmp_path / "src")),
    ]
    orgs = [Org(name="acme", match=("api-*",), skip=("*-fork",), languages=("python", "go"),
                include_archived=True, max_size_mb=50)]

    cfg.save(repos=repos, orgs=orgs)

    assert cfg.load_repos() == repos
    assert cfg.load_orgs() == orgs


def test_so_grava_campos_diferentes_do_padrao(tmp_path: Path) -> None:
    path = tmp_path / "repos.toml"
    ConfigFile(path).save(repos=[Repo(name="a", url="https://github.com/o/a.git")])

    text = path.read_text()

    keys = {line.split("=", 1)[0].strip() for line in text.splitlines() if "=" in line and not line.startswith("#")}
    assert "[[repo]]" in text
    assert 'name = "a"' in text
    assert 'url = "https://github.com/o/a.git"' in text
    assert not keys & {"include", "exclude", "extra_exclude", "branch"}


def test_listas_viram_tuplas_ao_ler(tmp_path: Path) -> None:
    cfg = ConfigFile(tmp_path / "repos.toml")
    cfg.save(orgs=[Org(name="acme", match=("api-*",), languages=("python",))])

    org = cfg.load_orgs()[0]

    assert isinstance(org.match, tuple)
    assert org.languages == ("python",)


def test_save_sem_argumento_preserva_o_que_ja_existe(tmp_path: Path) -> None:
    cfg = ConfigFile(tmp_path / "repos.toml")
    orgs = [Org(name="acme", languages=("go",))]
    cfg.save(repos=[Repo(name="a", path="/tmp/a")], orgs=orgs)

    cfg.save(repos=[Repo(name="b", path="/tmp/b")])
    assert cfg.load_orgs() == orgs
    assert [r.name for r in cfg.load_repos()] == ["b"]

    cfg.save(orgs=[])
    assert cfg.load_orgs() == []
    assert [r.name for r in cfg.load_repos()] == ["b"]


def test_save_lista_vazia_remove_os_repos(tmp_path: Path) -> None:
    cfg = ConfigFile(tmp_path / "repos.toml")
    cfg.save(repos=[Repo(name="a", path="/tmp/a")])

    cfg.save(repos=[])

    assert cfg.load_repos() == []


def test_valores_com_aspas_e_barras_sobrevivem_ao_roundtrip(tmp_path: Path) -> None:
    cfg = ConfigFile(tmp_path / "repos.toml")
    repo = Repo(name="a", path='/tmp/caminho "com aspas" \\ barra', extra_exclude=('**/"quoted"',))

    cfg.save(repos=[repo])

    assert cfg.load_repos() == [repo]


def test_max_size_mb_zero_e_preservado(tmp_path: Path) -> None:
    # max_size_mb=0 significa "sem limite" e precisa ser gravado (é diferente de None).
    cfg = ConfigFile(tmp_path / "repos.toml")
    cfg.save(orgs=[Org(name="acme", max_size_mb=0, include_forks=True)])

    org = cfg.load_orgs()[0]

    assert org.max_size_mb == 0
    assert org.include_forks is True


def test_arquivo_editado_a_mao_com_comentarios_e_lido(tmp_path: Path) -> None:
    path = tmp_path / "repos.toml"
    path.write_text(
        "# comentário do usuário\n\n[[org]]\nname = \"acme\"\nmatch = [\"api-*\"]\n\n"
        "[[repo]]\nname = \"avulso\"\npath = \"/tmp/avulso\"\n"
    )
    cfg = ConfigFile(path)

    assert [o.name for o in cfg.load_orgs()] == ["acme"]
    assert cfg.load_orgs()[0].match == ("api-*",)
    assert [r.name for r in cfg.load_repos()] == ["avulso"]


def test_nomes_de_repo_duplicados_levantam_erro(tmp_path: Path) -> None:
    path = tmp_path / "repos.toml"
    path.write_text('[[repo]]\nname = "dup"\npath = "/tmp/a"\n\n[[repo]]\nname = "dup"\npath = "/tmp/b"\n')

    with pytest.raises(ValueError, match="duplicados"):
        ConfigFile(path).load_repos()


@pytest.mark.parametrize(
    "body",
    [
        '[[repo]]\nname = "x"\nurl = "https://github.com/o/x.git"\npath = "/tmp/x"\n',  # url e path
        '[[repo]]\nname = "x"\n',  # nem url nem path
        '[[repo]]\nname = "com espaco"\npath = "/tmp/x"\n',  # nome inválido
    ],
)
def test_repo_invalido_no_arquivo_levanta_erro(tmp_path: Path, body: str) -> None:
    path = tmp_path / "repos.toml"
    path.write_text(body)

    with pytest.raises(ValueError):
        ConfigFile(path).load_repos()
