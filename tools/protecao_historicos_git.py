"""Barreira de metadados Git para historicos administrativos potencialmente sensiveis.

Examina a ancestralidade e as arvores dos commits introduzidos no Git.
As duas excecoes legadas sao blobs ja publicados e IMUTAVEIS. Remover a
excecao requer um plano de preservacao local e migracao aprovado.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


# Identificadores de objetos Git, NAO hashes de senhas ou chaves.
# Excecoes temporarias para evitar excluir copias operacionais durante rollout.
LEGADOS_IMUTAVEIS = {
    "tools/historico_chaves.json": "94c30e85d9cd99b0b5c65643acf3bb3c14d72ba2",
    "tools/historico_excluidos.json": "7b9d91e1ef977047447528bb052a5a9099d23072",
}

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_COMPRIMENTOS_OID = {b"sha1": 40, b"sha256": 64}


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
    if not isinstance(raw, bytes) or (raw and not raw.endswith(b"\0")):
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


def _git(*argumentos: str, cwd: Path | None) -> bytes:
    return subprocess.run(
        ["git", "--no-replace-objects", *argumentos],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_GRAFT_FILE": os.devnull},
    ).stdout


def _commits_introduzidos(base: str, head: str, *, evento: str, cwd: Path | None) -> list[str]:
    if evento not in ("pull_request", "push"):
        raise ErroInventario("evento_invalido")
    ambiente = _git("rev-parse", "--is-shallow-repository", "--show-object-format", cwd=cwd).splitlines()
    if len(ambiente) != 2 or ambiente[0] != b"false":
        raise ErroInventario("ancestralidade_indisponivel")
    comprimento = _COMPRIMENTOS_OID.get(ambiente[1])
    for oid in (base, head):
        if (not isinstance(oid, str) or len(oid) != comprimento
                or not _OID.fullmatch(oid) or not oid.strip("0")
                or _git("cat-file", "-t", oid, cwd=cwd).strip() != b"commit"):
            raise ErroInventario("referencia_invalida")

    if evento == "push":
        _git("merge-base", "--is-ancestor", base, head, cwd=cwd)
    else:
        _git("merge-base", base, head, cwd=cwd)
    # Percorre todos os pais e subtrai o historico inteiro da base do evento.
    # Sem pathspec, o Git nao simplifica a ancestralidade de merges.
    commits = _git("rev-list", "--missing=error", f"{base}..{head}", cwd=cwd).decode("ascii").splitlines()
    if any(len(oid) != comprimento or not _OID.fullmatch(oid) for oid in commits):
        raise ErroInventario("metadados_invalidos")
    return commits


def verificar_intervalo(
    base: str, head: str, *, evento: str, cwd: Path | None = None,
) -> int:
    violacoes = 0
    commits_com_violacoes = 0
    try:
        commits = _commits_introduzidos(base, head, evento=evento, cwd=cwd)
        for oid in commits:
            arvore = _git("ls-tree", "-r", "-z", "--full-tree", oid, cwd=cwd)
            proibidos = entradas_proibidas(entradas_git(arvore))
            if proibidos:
                violacoes += len(proibidos)
                commits_com_violacoes += 1
    except (OSError, UnicodeError, subprocess.CalledProcessError, ErroInventario):
        print("Protecao do historico: falha ao verificar referencias ou metadados Git.", file=sys.stderr)
        return 2
    if violacoes:
        print(
            f"Protecao do historico: {violacoes} entrada(s) proibida(s) em "
            f"{commits_com_violacoes} de {len(commits)} commit(s) inspecionado(s). "
            "Nomes e conteudos omitidos.",
            file=sys.stderr,
        )
        return 1
    print(f"Protecao do historico: OK em {len(commits)} commit(s) (somente metadados Git).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evento", required=True, choices=("pull_request", "push"))
    parser.add_argument("--base", required=True, help="OID da base do evento")
    parser.add_argument("--head", required=True, help="OID do commit introduzido pelo evento")
    args = parser.parse_args()
    return verificar_intervalo(args.base, args.head, evento=args.evento)


if __name__ == "__main__":
    sys.exit(main())
