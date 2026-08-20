"""
Sistema de auto-atualização — SEAGBH

Verifica versão remota no Firebase (/updates/latest),
baixa o novo .exe do GitHub Releases e aplica via script .bat.
"""

import json
import os
import sys
import subprocess
import tempfile
from urllib.request import urlopen, Request
from urllib.error import URLError

from core.versao import __version__
from core.licenca import FIREBASE_URL


# ══════════════════════════════════════════════════════════════════════════════
#  Consulta ao Firebase
# ══════════════════════════════════════════════════════════════════════════════

def verificar_atualizacao() -> dict | None:
    """
    Consulta /updates/latest no Firebase.

    Retorna dict com {version, url, changelog, data, obrigatoria}
    se houver versão mais recente, ou None se estiver atualizado.
    """
    try:
        url = f"{FIREBASE_URL}/updates/latest.json"
        req = Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=120) as resp:  # ← Timeout consistente com download
            dados = json.loads(resp.read().decode())
    except (URLError, OSError, json.JSONDecodeError):
        return None

    if not isinstance(dados, dict):
        return None

    versao_remota = dados.get("version", "")
    if not versao_remota:
        return None

    # Comparação de versão: extrai apenas números, ignora sufixos (beta, rc, alpha, etc)
    def _parse_version(v: str) -> tuple:
        """Extrai apenas números inteiros da versão, ignorando sufixos."""
        try:
            # Remove sufixos como -beta, -rc, -alpha
            base = v.split("-")[0]  # "2.0.1-beta" → "2.0.1"
            return tuple(int(x) for x in base.split("."))
        except (ValueError, AttributeError):
            return (0,)  # Fallback para versão inválida

    try:
        local = _parse_version(__version__)
        remota = _parse_version(versao_remota)
    except Exception:
        return None

    if remota > local:
        return dados
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Download com callback de progresso
# ══════════════════════════════════════════════════════════════════════════════

def baixar_atualizacao(url: str, destino: str,
                       progresso_cb=None, sha256_esperado: str | None = None) -> bool:
    """
    Baixa o arquivo de *url* para *destino* com validação de integridade.

    progresso_cb(recebido, total) é chamado a cada chunk.
    sha256_esperado: hash SHA256 esperado (opcional, para validação).
    Retorna True se o download foi concluído E validado com sucesso.
    """
    import hashlib
    
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=120) as resp:  # ← Timeout aumentado para 120s (internet lenta)
            total = int(resp.headers.get("Content-Length", 0))
            recebido = 0
            chunk_size = 64 * 1024  # 64 KB
            sha256_hash = hashlib.sha256() if sha256_esperado else None

            with open(destino, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    recebido += len(chunk)
                    if sha256_hash:
                        sha256_hash.update(chunk)
                    if progresso_cb:
                        progresso_cb(recebido, total)

        # ── Validação de integridade ────────────────────────────────────────
        if sha256_esperado and sha256_hash:
            hash_calculado = sha256_hash.hexdigest().lower()
            if hash_calculado != sha256_esperado.lower():
                # Hash não corresponde → arquivo corrompido!
                os.remove(destino)
                return False

        return True
    except (URLError, OSError):
        # Limpar arquivo incompleto/corrompido
        if os.path.exists(destino):
            try:
                os.remove(destino)
            except OSError:
                pass
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  Cleanup de arquivos temporários
# ══════════════════════════════════════════════════════════════════════════════

def limpar_arquivos_temporarios():
    """Remove arquivos de atualização temporários/orfãos."""
    import glob
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        temp_dir = tempfile.gettempdir()
        # Procurar por arquivos de atualização antiga
        pattern = os.path.join(temp_dir, "SEAGBH_*.exe")
        for arquivo in glob.glob(pattern):
            try:
                # Só deletar se older than 1 hour
                if time.time() - os.path.getmtime(arquivo) > 3600:
                    os.remove(arquivo)
                    logger.info(f"Arquivo temporário removido: {arquivo}")
            except (OSError, IOError) as e:
                logger.debug(f"Não foi possível remover {arquivo}: {e}")
        
        # Procurar por scripts .bat de atualização
        pattern = os.path.join(temp_dir, "seagbh_update_*.bat")
        for arquivo in glob.glob(pattern):
            try:
                if time.time() - os.path.getmtime(arquivo) > 3600:
                    os.remove(arquivo)
                    logger.info(f"Script de atualização removido: {arquivo}")
            except (OSError, IOError) as e:
                logger.debug(f"Não foi possível remover {arquivo}: {e}")
    except Exception as e:
        logger.warning(f"Erro ao limpar arquivos temporários: {e}")

def aplicar_atualizacao(novo_exe: str):
    """
    Cria um .bat temporário que:
      1. Espera o processo atual fechar
      2. Copia o novo .exe sobre o antigo
      3. Reinicia o app
      4. Se auto-deleta

    Após chamar esta função, o chamador deve encerrar o app (sys.exit).
    """
    # Segurança: só permite auto-update quando executando binário congelado
    # (PyInstaller). Em modo desenvolvimento, sys.executable aponta para
    # python.exe e nunca deve ser sobrescrito.
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Atualização automática disponível apenas no executável instalado. "
            "No modo desenvolvimento, atualize manualmente."
        )

    if not os.path.isfile(novo_exe):
        raise RuntimeError("Arquivo da atualização não encontrado.")

    exe_atual = sys.executable  # Caminho do .exe em execução (PyInstaller)

    # .bat robusto: espera o .exe ser liberado antes de iniciar o novo app
    bat_content = f"""@echo off
setlocal enableextensions
set "EXE_PATH={exe_atual}"
set "NEW_EXE={novo_exe}"

REM Aguarda o processo antigo liberar o arquivo (max 60 tentativas ~60s)
set /a _wait=0
:waitloop
if exist "%EXE_PATH%" (
    >nul 2>&1 (>>"%EXE_PATH%" (call )) && goto unlocked || (
        set /a _wait+=1
        if %_wait% geq 60 goto timeout
        timeout /t 1 /nobreak >nul
        goto waitloop
    )
)
:unlocked
copy /Y "%NEW_EXE%" "%EXE_PATH%"
del "%NEW_EXE%"
start "" "%EXE_PATH%" --updated
del "%~f0"
goto end
:timeout
echo Falha ao aguardar o SEAGBH fechar. Atualização não aplicada.
timeout /t 5 >nul
:end
endlocal
"""
    bat_path = os.path.join(tempfile.gettempdir(), f"seagbh_update_{os.getpid()}.bat")
    with open(bat_path, "w", encoding="cp1252" if os.name == "nt" else "utf-8") as f:
        f.write(bat_content)

    # Lança o .bat desacoplado do processo atual
    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
    
    # Log de informação para debugging
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Script de atualização iniciado: {bat_path}")
    
    # Retorna o caminho do batch para limpeza futura se necessário
    return bat_path
