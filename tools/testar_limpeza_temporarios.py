"""Teste isolado e seguro de limpar_arquivos_temporarios() — sem tocar no temp real."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import atualizacao  # noqa: E402


def criar_arquivo(caminho: str, idade_segundos: float):
    with open(caminho, "wb") as f:
        f.write(b"teste")
    velho = time.time() - idade_segundos
    os.utime(caminho, (velho, velho))


def main():
    # 1) Diretório isolado (NÃO é o temp real do sistema)
    falso_temp = tempfile.mkdtemp(prefix="seagbh_test_")
    gettempdir_original = tempfile.gettempdir
    tempfile.gettempdir = lambda: falso_temp  # monkeypatch só neste processo

    try:
        # 2) Cenário: 2 velhos (devem ser removidos) + 2 novos (devem ficar)
        exe_velho = os.path.join(falso_temp, "SEAGBH_v2.0.1.exe")
        bat_velho = os.path.join(falso_temp, "seagbh_update_12345.bat")
        exe_novo = os.path.join(falso_temp, "SEAGBH_v2.0.2.exe")
        bat_novo = os.path.join(falso_temp, "seagbh_update_67890.bat")

        criar_arquivo(exe_velho, idade_segundos=7200)   # 2 h
        criar_arquivo(bat_velho, idade_segundos=7200)   # 2 h
        criar_arquivo(exe_novo, idade_segundos=60)      # 1 min
        criar_arquivo(bat_novo, idade_segundos=60)      # 1 min

        # 3) Executa a função (foi aqui que o NameError ocorreria antes da correção)
        atualizacao.limpar_arquivos_temporarios()

        # 4) Validações
        assert not os.path.exists(exe_velho), "EXE velho deveria ter sido removido"
        assert not os.path.exists(bat_velho), "BAT velho deveria ter sido removido"
        assert os.path.exists(exe_novo), "EXE novo não deveria ser removido"
        assert os.path.exists(bat_novo), "BAT novo não deveria ser removido"
        print("TESTE_OK: limpar_arquivos_temporarios() sem NameError e com filtragem correta")
    finally:
        # 5) Restaura e limpa
        tempfile.gettempdir = gettempdir_original
        import shutil
        shutil.rmtree(falso_temp, ignore_errors=True)


if __name__ == "__main__":
    main()