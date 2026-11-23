"""Barreira de metadados Git para historicos administrativos potencialmente sensiveis.

Nao abre arquivos de historico nem examina blobs; usa somente a arvore Git.
As duas excecoes legadas sao blobs ja publicados e IMUTAVEIS. Remover a
excecao requer um plano de preservacao local e migracao aprovado.
"""

from __future__ import annotations

import re
import subprocess
import sys


# Identificadores de objetos Git, NAO hashes de senhas ou chaves.
# Excecoes temporarias para evitar excluir copias operacionais durante rollout.
LEGADOS_IMUTAVEIS = {
    "tools/historico_chaves.json": "94c30e85d9cd99b0b5c65643acf3bb3c14d72ba2",
    "tools/historico_excluidos.json": "7b9d91e1ef977047447528bb052a5a9099d23072",
}

_OID = re.compile(r"^[0-9a-f]{40,64}$")


class ErroInventario(RuntimeError):
    """Inspecao de metadados indisponivel ou inconsistente."""


def arquivo_de_historico(caminho: str) -> bool:
    """Reconhece nomes operacionais em qualquer pasta sem consultar conteudo."""
    if not isinstance(caminho, str) or not caminho:
        return False
    nome = caminho.rsplit("/", 1)[-1].casefold()
    return (
        (nome.startswith("historico") and any(
            marca in nome for marca in (".json", ".sqlite3", ".db", ".backup")
        ))
        or nome.startswith("originais.sqlite3")
    )


def entradas_git(raw: bytes) -> list[tuple[str, str, str]]:
    """Decodifica `git ls-tree -rz` (modo, tipo, OID, caminho)."""
    if not isinstance(raw, bytes):
        raise ErroInventario("metadados_invalidos")
    resultado = []
    for entrada in raw.split(b"\0"):
        if not entrada:
            continue
        cabecalho, sep, caminho = entrada.partition(b"\t")
        partes = cabecalho.split(b" ")
        if sep != b"\t" or not caminho or len(partes) != 3:
            raise ErroInventario("metadados_invalidos")
        try:
            modo, tipo, oid = (parte.decode("ascii") for parte in partes)
            nome = caminho.decode("utf-8", errors="surrogateescape")
        except UnicodeError:
            raise ErroInventario("metadados_invalidos") from None
        if not modo.isdigit() or not _OID.fullmatch(oid):
            raise ErroInventario("metadados_invalidos")
        resultado.append((nome, tipo, oid))
    return resultado


def entradas_proibidas(entradas: list[tuple[str, str, str]]) -> list[str]:
    """Rejeita novos historicos e qualquer mudanca nos dois blobs legados."""
    proibidos = []
    for nome, tipo, oid in entradas:
        if arquivo_de_historico(nome) and not (
            nome in LEGADOS_IMUTAVEIS
            and tipo == "blob"
            and oid == LEGADOS_IMUTAVEIS[nome]
        ):
            proibidos.append(nome)
    return proibidos


def verificar_head() -> int:
    try:
        processo = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=True,
        )
        proibidos = entradas_proibidas(entradas_git(processo.stdout))
    except (OSError, subprocess.CalledProcessError, ErroInventario):
        print("Protecao do historico: falha na leitura de metadados Git.", file=sys.stderr)
        return 2
    if proibidos:
        print(
            f"Protecao do historico: {len(proibidos)} objeto(s) rastreado(s) "
            "fora das excecoes legadas imutaveis. Nomes e conteudos omitidos.",
            file=sys.stderr,
        )
        return 1
    print("Protecao do historico: OK (somente metadados Git).")
    return 0


if __name__ == "__main__":
    sys.exit(verificar_head())
