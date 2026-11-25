"""Ponto de entrada do executável sintético K70-U1, sem interface operacional."""

import json
import os
from pathlib import Path
import stat
import sys

from core import atualizacao
from core._instalador_atualizacao import MODO_WORKER, _hash, executar_worker


PRODUTO = "SEAGBH-HML-K70-U1"
NOME_EXE = PRODUTO + ".exe"
PREFIXO_LAB = "seagbh-hml-k70-u1-"
VERSOES = {"anterior": "0.0.1-hml", "candidata": "0.0.2-hml"}


def _ler_json(caminho):
    info = atualizacao._sem_link(caminho)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("arquivo de controle inválido")
    with caminho.open("rb") as arquivo:
        bruto = arquivo.read(2049)
    if len(bruto) > 2048:
        raise ValueError("arquivo de controle grande demais")
    return json.loads(bruto)


def _identidade():
    dados = _ler_json(Path(__file__).with_name("variante.json"))
    if not isinstance(dados, dict) or set(dados) != {"produto", "variante", "versao"}:
        raise ValueError("variante inválida")
    variante = dados["variante"]
    if (dados["produto"] != PRODUTO or not isinstance(variante, str)
            or variante not in VERSOES or dados["versao"] != VERSOES[variante]):
        raise ValueError("variante inválida")
    return dados


def _digests(laboratorio):
    if not laboratorio.name.startswith(PREFIXO_LAB):
        raise ValueError("laboratório inválido")
    atualizacao._validar_ancestrais(laboratorio)
    dados = _ler_json(laboratorio / "laboratorio.json")
    if (not isinstance(dados, dict) or set(dados) != {"produto", "anterior", "candidata"}
            or dados["produto"] != PRODUTO):
        raise ValueError("manifesto do laboratório inválido")
    digests = {variante: atualizacao.validar_sha256(dados[variante]) for variante in VERSOES}
    if digests["anterior"] == digests["candidata"]:
        raise ValueError("variantes indistinguíveis")
    return digests


def _observar(laboratorio, identidade, evento):
    pid = os.getpid()
    with (laboratorio / f"{evento}-{pid}.json").open("x", encoding="utf-8") as arquivo:
        json.dump(dict(identidade, evento=evento, pid=pid, ppid=os.getppid(),
                       extracao=Path(sys._MEIPASS).name), arquivo)


def main():
    argumentos = sys.argv[1:]
    if (os.name != "nt" or not getattr(sys, "frozen", False)
            or argumentos not in ([], ["--agendar"], ["--updated"], [MODO_WORKER])):
        print("Modo de homologação indisponível.", file=sys.stderr)
        return 1

    worker = argumentos == [MODO_WORKER]
    executavel = Path(os.path.abspath(sys.executable))
    laboratorio = executavel.parent.parent if worker else executavel.parent
    try:
        identidade = _identidade()
        variante = identidade["variante"]
        digests = _digests(laboratorio)
        if _hash(executavel) != digests[variante]:
            raise ValueError("executável não corresponde ao laboratório")

        if worker:
            atualizacao._validar_area(executavel.parent)
            pedido = {"nome": NOME_EXE, "anterior": digests["anterior"],
                      "esperado": digests["candidata"]}
            if (executavel.name != "worker.exe" or variante != "anterior"
                    or _ler_json(executavel.parent / "pedido.json") != pedido):
                raise ValueError("pedido fora do laboratório")
            _observar(laboratorio, identidade, "auxiliar")
            return executar_worker()

        if executavel.name != NOME_EXE:
            raise ValueError("nome fora do laboratório")
        if argumentos == ["--agendar"]:
            if variante != "anterior":
                raise ValueError("somente a variante anterior agenda")
            _observar(laboratorio, identidade, "agendando")
            download = atualizacao.Download(laboratorio / "candidato.bin", digests["candidata"])
            atualizacao.aplicar_atualizacao(download)
        elif argumentos == ["--updated"]:
            if variante != "candidata":
                raise ValueError("reinício fora da variante candidata")
            _observar(laboratorio, identidade, "reiniciou")
        else:
            _observar(laboratorio, identidade, "iniciou")
        return 0
    except (OSError, ValueError, RecursionError, atualizacao.ErroAtualizacao):
        print("Ensaio recusado. Confira a identidade, os hashes e a área de homologação.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
