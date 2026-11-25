"""Worker local do instalador Windows. Nenhum código é recebido da rede."""
from pathlib import Path
import json
import os
import re
import stat
import subprocess
import sys
import time

from core.atualizacao import (
    ErroAtualizacao, _CHUNK, _MAX_ARQUIVO, _criar_area, _hash_stream,
    _sem_link, _validar_ancestrais, _validar_area, descartar_download, validar_sha256,
)

MODO_WORKER = "--seagbh-instalar-atualizacao"


def _hash(caminho):
    if not stat.S_ISREG(_sem_link(caminho).st_mode):
        raise ErroAtualizacao("integridade")
    with caminho.open("rb") as origem:
        return _hash_stream(origem)


def _snapshot(origem, destino, digest=None):
    if not stat.S_ISREG(_sem_link(origem).st_mode):
        raise ErroAtualizacao("integridade")
    total = 0
    with origem.open("rb") as entrada, destino.open("xb") as saida:
        while bloco := entrada.read(_CHUNK):
            total += len(bloco)
            if total > _MAX_ARQUIVO:
                raise ErroAtualizacao("integridade")
            saida.write(bloco)
        saida.flush()
        os.fsync(saida.fileno())
    observado = _hash(destino)
    if digest is not None and observado != digest:
        raise ErroAtualizacao("integridade")
    return observado


def _substituir(alvo, candidato, backup):
    import ctypes
    from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPVOID
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    substituir = dll.ReplaceFileW
    substituir.argtypes = [LPCWSTR, LPCWSTR, LPCWSTR, DWORD, LPVOID, LPVOID]
    substituir.restype = BOOL
    if not substituir(str(alvo), str(candidato), str(backup), 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def _lancar(arquivo, argumento, *, desacoplar=False):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    # PyInstaller pode alterar temporariamente a busca de DLL no Windows.
    # O filho é outra aplicação congelada, com extração própria e cwd explícito.
    import ctypes
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    consulta = dll.GetDllDirectoryW
    consulta.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    consulta.restype = ctypes.c_uint32
    tamanho = consulta(0, None)
    antigo = ctypes.create_unicode_buffer(tamanho + 1)
    if tamanho and not consulta(len(antigo), antigo):
        raise OSError("dll_directory_unavailable")
    definir = dll.SetDllDirectoryW
    definir.argtypes = [ctypes.c_wchar_p]
    definir.restype = ctypes.c_int
    if not definir(None):
        raise OSError("dll_directory_unavailable")
    try:
        subprocess.Popen([str(arquivo), argumento], cwd=str(arquivo.parent), env=env,
                         close_fds=True, shell=False,
                         creationflags=subprocess.DETACHED_PROCESS if desacoplar else 0)
    finally:
        if antigo.value:
            definir(antigo.value)


def _trava(alvo):
    return alvo.parent / ".seagbh-atualizacao.lock"


def _recuperar_ausente(alvo, backup, anterior):
    """Restaura somente se o destino faltar. Link não sobrescreve um destino."""
    try:
        _sem_link(alvo)
    except FileNotFoundError:
        if _hash(backup) != anterior:
            raise ErroAtualizacao("recuperacao")
        os.link(backup, alvo)


def _instalar(area, alvo, esperado, anterior, *, substituir=_substituir, lancar=_lancar,
              aguardar=time.sleep, tentativas=60):
    """Troca somente snapshot revalidado. Em erro, jamais executa candidato."""
    trava = _trava(alvo)
    try:
        trava.mkdir()
    except FileExistsError:
        raise ErroAtualizacao("em_andamento") from None
    candidato = area / "candidato.exe"
    backup = area / "anterior.exe"
    try:
        for tentativa in range(tentativas):
            if _hash(alvo) != anterior:
                raise ErroAtualizacao("alvo_alterado")
            if _hash(candidato) != esperado:
                raise ErroAtualizacao("integridade")
            try:
                substituir(alvo, candidato, backup)
            except OSError as exc:
                if getattr(exc, "winerror", None) in (32, 33) and tentativa + 1 < tentativas:
                    aguardar(1)
                    continue
                try:
                    _recuperar_ausente(alvo, backup, anterior)
                except (OSError, ErroAtualizacao):
                    raise ErroAtualizacao("recuperacao") from None
                raise ErroAtualizacao("substituicao") from None
            break
        if _hash(backup) != anterior or _hash(alvo) != esperado:
            raise ErroAtualizacao("recuperacao")
        try:
            lancar(alvo, "--updated")
        except OSError:
            # Manter os dois arquivos: nunca desfazer uma troca sobrescrevendo
            # um destino que possa ter sido modificado por outro instalador.
            raise ErroAtualizacao("reinicio") from None
    finally:
        trava.rmdir()


def agendar(download):
    alvo = Path(os.path.abspath(sys.executable))
    area = None
    try:
        area = _criar_area(alvo.parent)
        anterior = _snapshot(alvo, area / "worker.exe")
        _snapshot(download.caminho, area / "candidato.exe", download.digest)
        with (area / "pedido.json").open("x", encoding="utf-8") as pedido:
            json.dump({"nome": alvo.name, "anterior": anterior, "esperado": download.digest}, pedido)
        _lancar(area / "worker.exe", MODO_WORKER, desacoplar=True)
    except (OSError, ErroAtualizacao):
        if area is not None:
            try:
                for nome in ("worker.exe", "candidato.exe", "pedido.json"):
                    (area / nome).unlink(missing_ok=True)
                area.rmdir()
            except OSError:
                # Preserva o erro original e não remove nada fora da área.
                pass
        raise
    descartar_download(download)
    return area


def _mensagem_falha(area, codigo):
    """Estado sanitizado para o operador no ambiente de aplicação real."""
    try:
        with (area / "resultado.json").open("x", encoding="utf-8") as f:
            json.dump({"estado": codigo}, f)
    except OSError:
        pass
    import ctypes
    from ctypes.wintypes import HWND, LPCWSTR, UINT
    exibir = ctypes.WinDLL("user32").MessageBoxW
    exibir.argtypes = [HWND, LPCWSTR, LPCWSTR, UINT]
    exibir.restype = ctypes.c_int
    exibir(
        None, f"A atualização não foi concluída ({codigo}).\n"
        "Não apague os arquivos de recuperação. Consulte o suporte.\n"
        f"Pasta de diagnóstico: {area}", "Atualização SEAGBH", 0x10,
    )


def executar_worker():
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return 1
    worker = Path(os.path.abspath(sys.executable))
    area = worker.parent
    try:
        _validar_area(area)
        if worker.name != "worker.exe" or not stat.S_ISREG(_sem_link(area / "pedido.json").st_mode):
            return 1
        with (area / "pedido.json").open("rb") as pedido:
            dados = json.loads(pedido.read(2049))
        if (not isinstance(dados, dict) or set(dados) != {"nome", "anterior", "esperado"}
                or not isinstance(dados["nome"], str)
                or not re.fullmatch(r'[^/\\:*?"<>|\x00-\x1f]{1,200}\.exe', dados["nome"], flags=re.I)):
            return 1
        anterior = validar_sha256(dados["anterior"])
        esperado = validar_sha256(dados["esperado"])
        if _hash(worker) != anterior:
            return 1
        _validar_ancestrais(area.parent)
    except (OSError, ValueError, ErroAtualizacao):
        return 1
    try:
        _instalar(area, area.parent / dados["nome"], esperado, anterior)
    except (OSError, ErroAtualizacao) as exc:
        _mensagem_falha(area, exc.codigo if isinstance(exc, ErroAtualizacao) else "armazenamento")
        return 1
    return 0
