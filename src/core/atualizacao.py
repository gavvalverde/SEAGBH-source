"""Consulta, valida e prepara downloads de atualizações, sem confiar no tamanho."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import http.client
import json
import logging
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from core.versao import __version__

logger = logging.getLogger(__name__)
_CHUNK = 64 * 1024
_MAX_MANIFESTO = 64 * 1024
_MAX_ARQUIVO = 1024 * 1024 * 1024
_PREFIXO = ".seagbh-atualizacao-"


class ErroAtualizacao(RuntimeError):
    def __init__(self, codigo):
        super().__init__(codigo)
        self.codigo = codigo


@dataclass(frozen=True)
class Download:
    caminho: Path
    digest: str


def validar_sha256(valor):
    if not isinstance(valor, str) or re.fullmatch(r"[0-9a-fA-F]{64}", valor) is None:
        raise ErroAtualizacao("manifesto")
    return valor.lower()


def _versao(valor):
    if not isinstance(valor, str) or re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,9}){0,4}(?:-[A-Za-z0-9._-]{1,32})?", valor) is None:
        raise ErroAtualizacao("manifesto")
    return tuple(int(n) for n in valor.split("-")[0].split("."))


def _url_https(valor):
    if not isinstance(valor, str) or any(ord(c) <= 32 or ord(c) == 127 for c in valor):
        raise ErroAtualizacao("manifesto")
    try:
        partes = urlsplit(valor)
        if (partes.scheme != "https" or not partes.hostname or partes.username is not None
                or partes.password is not None or partes.fragment or "\\" in valor):
            raise ValueError
        _ = partes.port
    except ValueError:
        raise ErroAtualizacao("manifesto") from None
    return valor


def validar_manifesto(dados):
    if not isinstance(dados, dict):
        raise ErroAtualizacao("manifesto")
    _versao(dados.get("version"))
    resultado = dict(dados, url=_url_https(dados.get("url")), sha256=validar_sha256(dados.get("sha256")))
    for campo in ("changelog", "data"):
        if campo in resultado and not isinstance(resultado[campo], str):
            raise ErroAtualizacao("manifesto")
    if "obrigatoria" in resultado and type(resultado["obrigatoria"]) is not bool:
        raise ErroAtualizacao("manifesto")
    return resultado


def verificar_atualizacao():
    """None para ausência real; falhas de transporte e de contrato são distintas."""
    # O worker de instalação não usa nem carrega Firebase/licenciamento.
    from core.licenca import FIREBASE_URL
    try:
        req = Request(f"{FIREBASE_URL}/updates/latest.json", headers={"Accept": "application/json"})
        with urlopen(req, timeout=15) as resposta:
            bruto = resposta.read(_MAX_MANIFESTO + 1)
    except (URLError, OSError, http.client.HTTPException):
        raise ErroAtualizacao("transporte") from None
    if len(bruto) > _MAX_MANIFESTO:
        raise ErroAtualizacao("manifesto")
    try:
        dados = json.loads(bruto)
    except (ValueError, UnicodeError, RecursionError):
        raise ErroAtualizacao("manifesto") from None
    if dados is None:
        return None
    info = validar_manifesto(dados)
    return info if _versao(info["version"]) > _versao(__version__) else None


def _sem_link(caminho):
    st = caminho.lstat()
    atributos = getattr(st, "st_file_attributes", 0)
    if stat.S_ISLNK(st.st_mode) or atributos & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise PermissionError("caminho_indireto")
    return st


def _validar_ancestrais(pasta):
    if os.name == "nt":
        from core import _acl_historico_windows as acl
        acl.validar_volume(pasta.anchor)
        sid = acl.operador()
        for ancestral in (*reversed(pasta.parents), pasta):
            _sem_link(ancestral)
            acl.validar_acl(ancestral, sid, privado=False, diretorio=True)
    else:
        _sem_link(pasta)


def _criar_area(pai):
    pai = Path(os.path.abspath(pai))
    _validar_ancestrais(pai)
    if os.name != "nt":
        caminho = pai / (_PREFIXO + secrets.token_hex(16))
        caminho.mkdir(mode=0o700)
        return caminho
    from core import _acl_historico_windows as acl
    caminho = pai / (_PREFIXO + secrets.token_hex(16))
    sid = acl.operador()
    acl.criar_privado(caminho, sid)
    _sem_link(caminho)
    acl.validar_acl(caminho, sid, privado=True, diretorio=True)
    return caminho


def _validar_area(pasta):
    if not re.fullmatch(re.escape(_PREFIXO) + r"[0-9a-f]{32}", pasta.name):
        raise PermissionError("area_invalida")
    _validar_ancestrais(pasta.parent)
    if not stat.S_ISDIR(_sem_link(pasta).st_mode):
        raise PermissionError("area_invalida")
    if os.name == "nt":
        from core import _acl_historico_windows as acl
        acl.validar_acl(pasta, acl.operador(), privado=True, diretorio=True)


def descartar_download(download):
    """Só remove a área privada específica, nunca pesquisa por arquivos no temp."""
    caminho = download.caminho
    if caminho.name != "arquivo.bin" or not caminho.parent.name.startswith(_PREFIXO):
        return
    try:
        _validar_area(caminho.parent)
        caminho.unlink(missing_ok=True)
        caminho.parent.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Não foi possível remover a área de download")


def _hash_stream(arquivo):
    digest = sha256()
    total = 0
    while bloco := arquivo.read(_CHUNK):
        total += len(bloco)
        if total > _MAX_ARQUIVO:
            raise ErroAtualizacao("integridade")
        digest.update(bloco)
    if total == 0:
        raise ErroAtualizacao("integridade")
    return digest.hexdigest()


def baixar_atualizacao(url, sha256_esperado, progresso_cb=None, *, cancelado=None, pasta=None):
    """Retorna Download apenas após verificação obrigatória; falhas são tipadas."""
    digest = validar_sha256(sha256_esperado)
    _url_https(url)

    def verificar_cancelamento():
        if cancelado is not None and cancelado():
            raise ErroAtualizacao("cancelado")

    verificar_cancelamento()
    try:
        area = _criar_area(pasta if pasta is not None else tempfile.gettempdir())
    except OSError:
        raise ErroAtualizacao("armazenamento") from None
    download = Download(area / "arquivo.bin", digest)
    concluido = False
    try:
        try:
            resposta = urlopen(Request(url, method="GET"), timeout=120)
        except (URLError, OSError, http.client.HTTPException):
            raise ErroAtualizacao("transporte") from None
        with resposta:
            if hasattr(resposta, "geturl"):
                try:
                    _url_https(resposta.geturl() or url)
                except ErroAtualizacao:
                    raise ErroAtualizacao("transporte") from None
            tamanho = resposta.headers.get("Content-Length")
            if tamanho is None:
                total = 0
            elif not isinstance(tamanho, str) or not re.fullmatch(r"[0-9]{1,10}", tamanho):
                raise ErroAtualizacao("integridade")
            else:
                total = int(tamanho)
                if not 0 < total <= _MAX_ARQUIVO:
                    raise ErroAtualizacao("integridade")
            recebido = 0
            hash_atual = sha256()
            with download.caminho.open("xb") as destino:
                while True:
                    verificar_cancelamento()
                    try:
                        bloco = resposta.read(_CHUNK)
                    except http.client.IncompleteRead:
                        raise ErroAtualizacao("integridade") from None
                    except (URLError, OSError, http.client.HTTPException):
                        raise ErroAtualizacao("transporte") from None
                    if not bloco:
                        break
                    recebido += len(bloco)
                    if recebido > (total or _MAX_ARQUIVO):
                        raise ErroAtualizacao("integridade")
                    destino.write(bloco)
                    hash_atual.update(bloco)
                    if progresso_cb:
                        progresso_cb(recebido, total)
                destino.flush()
                os.fsync(destino.fileno())
        verificar_cancelamento()
        if not recebido or (total and recebido != total) or hash_atual.hexdigest() != digest:
            raise ErroAtualizacao("integridade")
        concluido = True
        return download
    except OSError:
        raise ErroAtualizacao("armazenamento") from None
    finally:
        if not concluido:
            descartar_download(download)


def validar_aplicacao_atualizacao(download):
    if not getattr(sys, "frozen", False) or os.name != "nt":
        raise ErroAtualizacao("plataforma")
    validar_sha256(download.digest)
    try:
        if not stat.S_ISREG(_sem_link(download.caminho).st_mode):
            raise ErroAtualizacao("integridade")
    except OSError:
        raise ErroAtualizacao("integridade") from None


def aplicar_atualizacao(download):
    validar_aplicacao_atualizacao(download)
    from core import _instalador_atualizacao
    return _instalador_atualizacao.agendar(download)


# Somente o utilitário de manutenção legado chama a limpeza legada.
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
