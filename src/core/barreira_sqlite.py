"""Coordenação cooperativa isolada: nenhum SQLite, configuração ou executor.

Cada caminho canônico de banco usa um arquivo lateral estável. Somente esse
arquivo pode ser criado/aberto; nunca é truncado, escrito ou removido. Diretórios
locais precisam ser controlados pelo chamador: metadados não eliminam TOCTOU.
Uma concessão protege contra participantes do mesmo protocolo, não contra outros
clientes SQLite. ATIVA é estado local, nunca autorização global de restauração.
"""

from __future__ import annotations

from enum import Enum
import errno
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
import warnings


__all__ = ["ErroBarreira", "TipoConcessao", "EstadoConcessao", "Concessao",
           "caminho_coordenacao", "adquirir_uso_operacional", "adquirir_manutencao"]
_SUFIXO = ".seagbh-access-v1.lock"
_EMISSAO = object()


class ErroBarreira(Exception):
    """Códigos e tipo/errno da causa, sem caminhos ou mensagem bruta do SO."""

    def __init__(self, fase: str, codigo: str, causa: BaseException | None = None):
        self.fase, self.codigo = fase, codigo
        self.causa = type(causa).__name__ if causa else None
        self.erro_sistema = getattr(causa, "errno", None)
        self.erro_windows = getattr(causa, "winerror", None)
        super().__init__(f"Barreira SQLite: fase={fase}; código={codigo}.")


class TipoConcessao(str, Enum):
    OPERACIONAL = "operacional"
    MANUTENCAO = "manutencao"


class EstadoConcessao(str, Enum):
    ATIVA = "ativa"
    LIBERADA = "liberada"
    HERDADA = "herdada_sem_validade_local"
    ERRO_LIBERACAO = "erro_liberacao"


class _DescritoresTemporarios:
    """Propriedade até a emissão; serializa somente transições curtas com fork.

    O mutex nunca cobre tentar/flock, conferências ou espera por contenção.
    Os callbacks coordenam os.fork do CPython; não fork nativo que os ignore.
    """

    def __init__(self):
        self.mutex = threading.Lock()
        self.descritores: set[int] = set()
        self.erro: OSError | None = None

    def _verificar(self) -> None:
        if self.erro is not None:
            raise ErroBarreira("aquisicao", "fechamento_temporario_nao_confirmado", self.erro)

    def verificar(self) -> None:
        with self.mutex:
            self._verificar()

    def abrir(self, api, caminho: Path) -> int:
        with self.mutex:
            self._verificar()
            fd = api.abrir(caminho)
            try:
                self.descritores.add(fd)
            except BaseException:
                # Ainda sob o mutex: fork não pode observar abertura sem registro.
                self.descritores.discard(fd)
                self._fechar(fd)
                raise
            return fd

    def emitir(self, fd: int, **dados) -> Concessao:
        with self.mutex:
            self._verificar()
            if fd not in self.descritores:
                raise ErroBarreira("aquisicao", "descritor_temporario_nao_registrado")
            concessao = Concessao(_emissao=_EMISSAO, _fd=fd, **dados)
            self.descritores.remove(fd)
            return concessao

    def _fechar(self, fd: int) -> None:
        try:
            os.close(fd)  # Nunca LOCK_UN: a descrição pode estar compartilhada com o pai.
        except OSError as exc:
            self.erro = exc
            raise

    def fechar(self, fd: int) -> None:
        with self.mutex:
            if fd in self.descritores:
                self.descritores.remove(fd)  # Não repetir close de número reutilizável.
                self._fechar(fd)

    def antes_do_fork(self) -> None:
        self.mutex.acquire()

    def depois_no_pai(self) -> None:
        self.mutex.release()

    def depois_no_filho(self) -> None:
        pendentes = self.descritores
        self.descritores = set()
        self.mutex = threading.Lock()  # Não reutilizar mutex herdado de outra thread.
        for fd in pendentes:
            try:
                self._fechar(fd)
            except OSError:
                # Exceções de callbacks são ignoradas por os.fork. Recusar uso depois.
                # Não tentar close novamente: o número pode já ter sido reutilizado.
                continue


_temporarios = _DescritoresTemporarios()
if sys.platform.startswith("linux"):
    os.register_at_fork(before=_temporarios.antes_do_fork,
                        after_in_parent=_temporarios.depois_no_pai,
                        after_in_child=_temporarios.depois_no_filho)


def _sqlite(backend: str) -> None:
    if backend != "sqlite":
        raise ErroBarreira("argumentos", "backend_nao_suportado")


def _lstat(caminho: Path) -> os.stat_result | None:
    try:
        return caminho.lstat()
    except FileNotFoundError:
        return None


def _entrada_segura(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ErroBarreira("caminhos", "link_ou_reparse_point")


def _caminho(valor: str | Path, *, ausente: bool) -> Path:
    caminho = Path(valor)
    if (not caminho.is_absolute() or ".." in caminho.parts or "\x00" in str(caminho)
            or str(caminho).startswith(("\\\\", "//"))):
        raise ErroBarreira("caminhos", "caminho_inseguro")
    if os.name == "nt":
        _Windows().validar_volume(caminho)  # Antes de lstat em possível drive remoto.
    for parte in (*reversed(caminho.parents), caminho):
        if os.name == "nt" and parte != Path(parte.anchor):
            reservado = parte.name.split(".")[0].upper()
            if (":" in parte.name or parte.name.endswith((" ", "."))
                    or reservado in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                    or reservado in {p + n for p in ("COM", "LPT") for n in "123456789¹²³"}):
                raise ErroBarreira("caminhos", "caminho_inseguro")
        info = _lstat(parte)
        if info is None:
            if parte != caminho or not ausente:
                raise ErroBarreira("caminhos", "diretorio_ou_arquivo_ausente")
        else:
            _entrada_segura(info)
            if parte != caminho and not stat.S_ISDIR(info.st_mode):
                raise ErroBarreira("caminhos", "componente_nao_diretorio")
            if parte == caminho and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ErroBarreira("caminhos", "arquivo_nao_regular_ou_com_hardlinks")
    return caminho


def _banco(valor: str | Path) -> Path:
    try:
        caminho = _caminho(valor, ausente=True)
        resolvido = _caminho(caminho.resolve(strict=False), ausente=True)
        if any(os.path.normcase(p.name).endswith(_SUFIXO) for p in (caminho, resolvido)):
            raise ErroBarreira("caminhos", "banco_e_coordenacao_confundidos")
        return resolvido
    except (OSError, TypeError, ValueError) as exc:
        raise ErroBarreira("caminhos", "caminho_nao_verificado", exc) from None


def caminho_coordenacao(*, backend: str, banco: str | Path) -> Path:
    """Deriva o nome canônico; somente consulta metadados, sem criar diretórios."""
    _sqlite(backend)
    caminho = _banco(banco)
    return caminho.with_name(caminho.name + _SUFIXO)


class _Posix:
    def __init__(self):
        import fcntl
        self.api = fcntl

    def abrir(self, caminho: Path) -> int:
        return os.open(caminho, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)

    def tentar(self, fd: int, exclusiva: bool) -> bool:
        try:
            self.api.flock(fd, (self.api.LOCK_EX if exclusiva else self.api.LOCK_SH) | self.api.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

    def liberar(self, fd: int) -> None:
        self.api.flock(fd, self.api.LOCK_UN)


class _Windows:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                        ("hEvent", wintypes.HANDLE)]

        self.ctypes, self.crt = ctypes, msvcrt
        self.overlapped = Overlapped()
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.api.GetDriveTypeW.restype = wintypes.UINT
        self.api.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
        self.api.LockFileEx.restype = wintypes.BOOL
        self.api.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
        self.api.UnlockFileEx.restype = wintypes.BOOL

    def validar_volume(self, caminho: Path) -> None:
        if self.api.GetDriveTypeW(caminho.anchor) != 3:  # DRIVE_FIXED, sem drive remoto mapeado.
            raise ErroBarreira("caminhos", "volume_local_nao_confirmado")

    def abrir(self, caminho: Path) -> int:
        self.validar_volume(caminho)
        # OPEN_ALWAYS, sem TRUNCATE; compartilhamento R/W, sem FILE_SHARE_DELETE.
        # Handle síncrono e não herdável; OPEN_REPARSE_POINT evita seguir o link final.
        handle = self.api.CreateFileW(str(caminho), 0xC0000000, 3, None, 4, 0x00200080, None)
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            return self.crt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT)
        except BaseException:
            if not self.api.CloseHandle(handle):
                raise self.ctypes.WinError(self.ctypes.get_last_error()) from None
            raise

    def tentar(self, fd: int, exclusiva: bool) -> bool:
        # Um byte além do EOF também pode ser bloqueado; não escrever marcador.
        flags = 1 | (2 if exclusiva else 0)  # FAIL_IMMEDIATELY e EXCLUSIVE_LOCK.
        if self.api.LockFileEx(self.crt.get_osfhandle(fd), flags, 0, 1, 0,
                               self.ctypes.byref(self.overlapped)):
            return True
        codigo = self.ctypes.get_last_error()
        if codigo == 33:  # ERROR_LOCK_VIOLATION: somente contenção é retentável.
            return False
        raise self.ctypes.WinError(codigo)

    def liberar(self, fd: int) -> None:
        if not self.api.UnlockFileEx(self.crt.get_osfhandle(fd), 0, 1, 0,
                                     self.ctypes.byref(self.overlapped)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())


def _plataforma():
    if os.name == "nt":
        return _Windows()
    if sys.platform.startswith("linux"):
        return _Posix()
    raise ErroBarreira("aquisicao", "plataforma_nao_suportada")


def _conferir(fd: int, caminho: Path) -> None:
    _caminho(caminho, ausente=False)
    aberto, atual = os.fstat(fd), caminho.lstat()
    _entrada_segura(aberto)
    if (not stat.S_ISREG(aberto.st_mode) or aberto.st_nlink != 1 or aberto.st_ino == 0
            or (aberto.st_dev, aberto.st_ino) != (atual.st_dev, atual.st_ino)):
        raise ErroBarreira("aquisicao", "identidade_coordenacao_nao_confirmada")


class Concessao:
    """Recurso local emitido pela aquisição real; não serializável nem copiável.

    O chamador fecha TODAS as conexões antes de fechar a concessão. Ela não abre
    ou acompanha conexões SQLite. Métodos são serializados; não há upgrade de modo.
    """

    __slots__ = ("__fd", "__api", "__pid", "__estado", "__tipo", "__banco",
                 "__caminho", "__mutex", "__emissao")

    def __init__(self, *, _emissao=None, _fd=None, _api=None, _tipo=None, _banco=None, _caminho=None):
        if _emissao is not _EMISSAO:
            raise ErroBarreira("contrato", "concessao_nao_emitida")
        self.__mutex = threading.RLock()
        self.__pid, self.__estado = os.getpid(), EstadoConcessao.ATIVA
        self.__api, self.__tipo = _api, _tipo
        self.__banco, self.__caminho = _banco, _caminho
        self.__emissao, self.__fd = _emissao, _fd

    def _emitida(self) -> None:
        if getattr(self, "_Concessao__emissao", None) is not _EMISSAO:
            raise ErroBarreira("contrato", "concessao_nao_emitida")

    @property
    def banco(self) -> Path:
        self._emitida()
        return self.__banco

    @property
    def arquivo_coordenacao(self) -> Path:
        self._emitida()
        return self.__caminho

    @property
    def tipo(self) -> TipoConcessao:
        self._emitida()
        return self.__tipo

    def consultar_estado(self) -> EstadoConcessao:
        """Somente estado local conhecido: não consulta/prova exclusividade global."""
        self._emitida()
        # Não adquirir mutex herdado de outra thread depois de fork.
        if self.__pid != os.getpid():
            return EstadoConcessao.HERDADA if self.__fd is not None else self.__estado
        with self.__mutex:
            return self.__estado

    def fechar(self) -> None:
        self._emitida()
        if self.__pid != os.getpid():
            # No Linux o fd herdado referencia o mesmo flock: nunca executar UNLOCK.
            fd = self.__fd
            if fd is None:
                if self.__estado == EstadoConcessao.ERRO_LIBERACAO:
                    raise ErroBarreira("liberacao", "liberacao_nao_confirmada")
                return
            self.__fd = None
            try:
                os.close(fd)
            except OSError as exc:
                self.__estado = EstadoConcessao.ERRO_LIBERACAO
                raise ErroBarreira("liberacao", "fechamento_falhou", exc) from None
            self.__estado = EstadoConcessao.LIBERADA
            return
        with self.__mutex:
            fd = self.__fd
            if fd is None:
                if self.__estado == EstadoConcessao.ERRO_LIBERACAO:
                    raise ErroBarreira("liberacao", "liberacao_nao_confirmada")
                return
            self.__fd = None  # Não repetir close de fd potencialmente reutilizado.
            erro = None
            try:
                self.__api.liberar(fd)
            except OSError as exc:
                erro = exc
            finally:
                try:
                    os.close(fd)
                except OSError as exc:
                    erro = exc
            self.__estado = EstadoConcessao.ERRO_LIBERACAO if erro else EstadoConcessao.LIBERADA
            if erro is not None:
                raise ErroBarreira("liberacao", "liberacao_falhou", erro) from None

    def __enter__(self) -> Concessao:
        if self.consultar_estado() != EstadoConcessao.ATIVA:
            raise ErroBarreira("contrato", "concessao_nao_ativa")
        return self

    def __exit__(self, *args) -> bool:
        self.fechar()
        return False

    def __reduce_ex__(self, protocol):
        raise TypeError("Concessões não podem ser copiadas ou serializadas.")

    def __del__(self):
        fd = getattr(self, "_Concessao__fd", None)
        if fd is not None:
            self.__fd = None
            mensagem = "Concessão SQLite não fechada explicitamente."
            try:
                os.close(fd)  # Sem UNLOCK: seguro também em uma cópia herdada por fork.
            except OSError:
                mensagem = "Fechamento de concessão SQLite abandonada não confirmado."
            if not sys.is_finalizing():
                warnings.warn(mensagem, ResourceWarning, stacklevel=2)


def _adquirir(backend: str, banco: str | Path, prazo: float, tipo: TipoConcessao) -> Concessao:
    _sqlite(backend)
    if isinstance(prazo, bool) or not isinstance(prazo, (int, float)):
        raise ErroBarreira("argumentos", "prazo_invalido")
    try:
        prazo = float(prazo)
    except (OverflowError, TypeError, ValueError):
        raise ErroBarreira("argumentos", "prazo_invalido") from None
    if not math.isfinite(prazo) or prazo < 0:
        raise ErroBarreira("argumentos", "prazo_invalido")
    fim = time.monotonic() + prazo
    fd = None
    try:
        _temporarios.verificar()  # Falha de close anterior impede novas operações de I/O.
        api = _plataforma()
        alvo = _banco(banco)
        coordenacao = _caminho(alvo.with_name(alvo.name + _SUFIXO), ausente=True)
        fd = _temporarios.abrir(api, coordenacao)
        os.set_inheritable(fd, False)
        while True:
            _conferir(fd, coordenacao)
            if api.tentar(fd, tipo == TipoConcessao.MANUTENCAO):
                _conferir(fd, coordenacao)
                if prazo > 0 and time.monotonic() >= fim:
                    raise ErroBarreira("aquisicao", "prazo_excedido")
                concessao = _temporarios.emitir(fd, _api=api,
                    _tipo=tipo, _banco=alvo, _caminho=coordenacao)
                fd = None  # A concessão passa a ser a única proprietária do recurso.
                return concessao
            if prazo == 0:
                raise ErroBarreira("aquisicao", "ocupado")
            restante = fim - time.monotonic()
            if restante <= 0:
                raise ErroBarreira("aquisicao", "prazo_excedido")
            time.sleep(min(0.025, restante))
    except OSError as exc:
        codigo = "acesso_negado" if exc.errno in (errno.EACCES, errno.EPERM) else "erro_sistema"
        raise ErroBarreira("aquisicao", codigo, exc) from None
    finally:
        if fd is not None:
            try:
                _temporarios.fechar(fd)
            except OSError as exc:
                raise ErroBarreira("liberacao", "fechamento_falhou", exc) from None


def adquirir_uso_operacional(*, backend: str, banco: str | Path, prazo: float = 0) -> Concessao:
    """Lock compartilhado antes de SQLite; prazo=0 recusa imediatamente se ocupado."""
    return _adquirir(backend, banco, prazo, TipoConcessao.OPERACIONAL)


def adquirir_manutencao(*, backend: str, banco: str | Path, prazo: float = 0) -> Concessao:
    """Lock exclusivo cooperativo; não inspeciona conexões externas nem restaura."""
    return _adquirir(backend, banco, prazo, TipoConcessao.MANUTENCAO)
