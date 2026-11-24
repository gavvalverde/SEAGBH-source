"""Diagnóstico de metadados para manutenção SQLite; nunca autoriza restauração.

Não abre o banco, não adquire concessão e não altera arquivos. Exige caminho
explícito; não lê configuração, licença, Firebase ou Database operacional.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

# Permite invocação pela raiz: python -B tools/inspecionar_manutencao_sqlite.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.barreira_sqlite import ErroBarreira, caminho_coordenacao


def _estado(caminho: Path) -> str:
    """Consulta lstat somente; links quebrados continuam observáveis."""
    try:
        info = caminho.lstat()
    except FileNotFoundError:
        return "ausente"
    except OSError:
        return "nao_verificado"
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return "link_ou_reparse"
    if stat.S_ISREG(info.st_mode):
        return "regular" if info.st_nlink == 1 else "multiplos_links"
    if stat.S_ISDIR(info.st_mode):
        return "diretorio"
    return "outro_tipo"


def inspecionar(destino: str | Path) -> dict[str, object]:
    """Retorna apenas estados genéricos, nunca caminhos ou conteúdo SQLite."""
    resposta: dict[str, object] = {
        "versao": 1,
        "modo": "somente_metadados",
        "restauracao_autorizada": False,
        "exclusividade": "nao_comprovada",
        "compatibilidade": "nao_comprovada",
    }
    try:
        coordenacao = caminho_coordenacao(backend="sqlite", banco=destino)
        banco = Path(destino)
        auxiliares = {
            "wal": _estado(Path(str(banco) + "-wal")),
            "shm": _estado(Path(str(banco) + "-shm")),
            "journal": _estado(Path(str(banco) + "-journal")),
            "marcador_legado": _estado(Path(str(banco) + ".lock")),
            "arquivo_coordenacao": _estado(coordenacao),
        }
        status_banco = _estado(banco)
        avisos = [nome for nome in ("wal", "shm", "journal", "marcador_legado")
                  if auxiliares[nome] != "ausente"]
        if (status_banco != "regular"
                or auxiliares["arquivo_coordenacao"] not in ("ausente", "regular")
                or any(estado not in ("ausente", "regular") for estado in auxiliares.values())):
            resultado = "recusado"
        else:
            resultado = "inspecionado_com_avisos" if avisos else "inspecionado"
        resposta.update(resultado=resultado, destino=status_banco, auxiliares=auxiliares,
                        alertas=avisos, coordenacao_adquirida=False)
    except (ErroBarreira, OSError, TypeError, ValueError) as erro:
        codigo = erro.codigo if isinstance(erro, ErroBarreira) else "metadados_indisponiveis"
        resposta.update(resultado="recusado", motivo=codigo)
    return resposta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", required=True, help="Caminho absoluto explícito do SQLite a inspecionar")
    args = parser.parse_args(argv)
    resultado = inspecionar(args.destino)
    print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
    return 2 if resultado["resultado"] == "recusado" else 0


if __name__ == "__main__":
    raise SystemExit(main())
