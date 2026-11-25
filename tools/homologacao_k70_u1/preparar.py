"""Prepara e valida insumos sintéticos. Este módulo não gera executáveis."""

import argparse
from hashlib import sha256
import os
from pathlib import Path
import stat


PASTA = Path("tools/homologacao_k70_u1")
# Caminho relativo no pacote, origem no checkout e SHA-256 do texto em LF.
FONTES = {
    "lancador.py": (PASTA / "lancador.py",
                    "9f3556ec0f1b83375abc902f096ec8480b657a425049a2433abbea2a5174eda2"),
    "core/__init__.py": (Path("src/core/__init__.py"),
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    "core/atualizacao.py": (Path("src/core/atualizacao.py"),
                           "9be68527f06bead24af56ccf43b440278ec377126bbb6d1dfa9600c0aecbd299"),
    "core/_instalador_atualizacao.py": (Path("src/core/_instalador_atualizacao.py"),
                                       "42708c27c1616ced55a3faa88ea8a5f0519c3b18faf5782fe7f189c2006c533a"),
    "core/_acl_historico_windows.py": (Path("src/core/_acl_historico_windows.py"),
                                      "f734e1be6a818b77860b97c11df8fa7d941cc921f5ee5caf5ff07a2731b38fc8"),
    "core/versao.py": (Path("src/core/versao.py"),
                       "c0e7a137f33f671ed010342987ba70bed5334316cad9c0c85338f48824999ca5"),
}
VARIANTES = {
    "anterior": "63649ef88c9dc3bab6411e8dfb01c8813a305604c4ffa67f782918e262fee163",
    "candidata": "52f40b8aca534ef16f403f4838d8d5e47c12b3f3ad40c603c9f1a4dd4ae71c94",
}
REQUISITOS = "requirements-build-windows.txt"
SHA_REQUISITOS = "0f6ad36e013466c7935d85bc768ddd24c356d9af4de87eaccec786d9e4572bc9"
CONTROLES = {"homologacao.spec", "preparar.py", REQUISITOS}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LIMITE = 64 * 1024


def _exigir_tipo(caminho: Path, *, diretorio: bool) -> None:
    info = caminho.lstat()
    permitido = (stat.S_ISDIR(info.st_mode) if diretorio
                 else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    if not permitido or getattr(info, "st_file_attributes", 0) & _REPARSE:
        raise ValueError("tipo de caminho não permitido na homologação")


def _conferir_diretorio(caminho: Path) -> None:
    if (not caminho.is_absolute() or ".." in caminho.parts
            or str(caminho).startswith(("\\\\", "//"))):
        raise ValueError("use um caminho absoluto local")
    for pasta in (*reversed(caminho.parents), caminho):
        if os.name == "nt" and pasta != Path(caminho.anchor):
            if pasta.is_reserved() or ":" in pasta.name or pasta.name.endswith((" ", ".")):
                raise ValueError("nome de diretório não permitido")
        _exigir_tipo(pasta, diretorio=True)


def _ler(caminho: Path) -> bytes:
    _exigir_tipo(caminho, diretorio=False)
    with caminho.open("rb") as arquivo:
        dados = arquivo.read(_LIMITE + 1)
    if len(dados) > _LIMITE:
        raise ValueError("insumo maior que o permitido")
    return dados


def _conferir_hash(dados: bytes, esperado: str, nome: str) -> bytes:
    if sha256(dados).hexdigest() != esperado:
        raise ValueError(f"conteúdo fora do inventário aprovado: {nome}")
    return dados


def _conferir_nomes(pasta: Path, esperados: set[str]) -> None:
    _exigir_tipo(pasta, diretorio=True)
    if {entrada.name for entrada in pasta.iterdir()} != esperados:
        raise ValueError("entradas ausentes ou extras na árvore de homologação")


def validar_arvore(raiz: Path) -> Path:
    """Exige a árvore exata e devolve a pasta que o spec pode analisar."""
    _conferir_diretorio(raiz)
    _conferir_nomes(raiz, CONTROLES | {"pacote"})
    for nome in CONTROLES:
        _exigir_tipo(raiz / nome, diretorio=False)
    _conferir_hash(_ler(raiz / REQUISITOS), SHA_REQUISITOS, REQUISITOS)
    pacote = raiz / "pacote"
    _conferir_nomes(pacote, {"lancador.py", "variante.json", "core"})
    _conferir_nomes(pacote / "core", {Path(nome).name for nome in FONTES if nome.startswith("core/")})
    for nome, (_, digest) in FONTES.items():
        _conferir_hash(_ler(pacote / nome), digest, nome)
    if sha256(_ler(pacote / "variante.json")).hexdigest() not in VARIANTES.values():
        raise ValueError("variante fora do inventário aprovado")
    return pacote


def preparar(repositorio: Path, destino: Path, variante: str) -> None:
    """Cria um diretório novo fora do checkout, copiando só os insumos aprovados."""
    if variante not in VARIANTES:
        raise ValueError("variante inválida")
    if (not destino.is_absolute() or ".." in destino.parts
            or not destino.name or destino.name.endswith((" ", "."))
            or ":" in destino.name or destino.is_reserved()):
        raise ValueError("destino inválido")
    _conferir_diretorio(repositorio)
    _conferir_diretorio(destino.parent)
    if destino.parent.resolve().is_relative_to(repositorio.resolve()):
        raise ValueError("prepare a árvore fora do checkout")

    fontes = {**FONTES, "variante.json": (PASTA / "fixtures" / f"{variante}.json", VARIANTES[variante])}
    insumos = {}
    for nome, (relativo, digest) in fontes.items():
        origem = repositorio / relativo
        _conferir_diretorio(origem.parent)
        dados = _ler(origem).replace(b"\r\n", b"\n")
        insumos[Path("pacote") / nome] = _conferir_hash(dados, digest, nome)
    for nome in CONTROLES:
        origem = repositorio / PASTA / nome
        _conferir_diretorio(origem.parent)
        dados = _ler(origem).replace(b"\r\n", b"\n")
        insumos[Path(nome)] = _conferir_hash(dados, SHA_REQUISITOS, nome) if nome == REQUISITOS else dados

    destino.mkdir(mode=0o700)
    (destino / "pacote" / "core").mkdir(parents=True)
    for relativo, dados in insumos.items():
        with (destino / relativo).open("xb") as arquivo:
            arquivo.write(dados)
    validar_arvore(destino)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variante", choices=tuple(VARIANTES))
    parser.add_argument("destino", type=Path, help="Diretório novo, com caminho absoluto fora do checkout.")
    args = parser.parse_args()
    try:
        preparar(Path(__file__).absolute().parents[2], args.destino, args.variante)
    except ValueError as erro:
        parser.error(str(erro))
    except OSError:
        parser.error("não foi possível preparar a árvore sintética")
    print(f"Insumos da variante {args.variante} preparados e verificados.")


if __name__ == "__main__":
    main()
