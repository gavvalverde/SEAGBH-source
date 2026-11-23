"""Caminhos e proteção no sistema operacional do histórico administrativo."""

import os
import re
import stat
import sys
from pathlib import Path, PureWindowsPath


_VOLUMES_LINUX_LOCAIS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "overlay", "f2fs", "zfs"}


def _validar_volume_linux(pasta):
    profundidade = -1
    formatos = set()
    for linha in Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        antes, _, depois = linha.partition(" - ")
        campos, tipo = antes.split(), depois.split(maxsplit=1)
        if len(campos) < 6 or not tipo:
            raise PermissionError("montagens_indisponiveis")
        montagem = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), campos[4]))
        if pasta.is_relative_to(montagem):
            nivel = len(montagem.parts)
            if nivel > profundidade:
                profundidade, formatos = nivel, set()
            if nivel == profundidade:
                formatos.add(tipo[0])
    if not formatos or not formatos <= _VOLUMES_LINUX_LOCAIS:
        raise PermissionError("volume_nao_suportado")


def caminho_privado(pasta):
    caminho = Path(pasta)
    if (not caminho.is_absolute() or not caminho.name or ".." in caminho.parts
            or str(caminho).startswith(("\\\\", "//"))
            or any(":" in parte for parte in caminho.parts[1:])):
        raise ValueError("caminho_inseguro")
    if os.name == "nt" and any(
        parte.endswith((".", " ")) or PureWindowsPath(parte).is_reserved()
        for parte in caminho.parts[1:]
    ):
        raise ValueError("caminho_inseguro")
    return caminho


class PastaHistorico:
    def __init__(self, pasta, *, criar=False):
        self.pasta = caminho_privado(pasta)
        self.windows = None
        if os.name == "nt":
            from . import _acl_historico_windows

            self.windows = _acl_historico_windows
            self.operador = self.windows.operador()
            self.windows.validar_volume(self.pasta.anchor)
        elif sys.platform == "linux":
            self.operador = os.geteuid()
            _validar_volume_linux(self.pasta)
        else:
            raise PermissionError("plataforma_nao_suportada")
        for caminho in (*reversed(self.pasta.parents), self.pasta):
            try:
                info = caminho.lstat()
            except FileNotFoundError:
                if not criar:
                    raise
                self._criar(caminho)
                info = caminho.lstat()
            self._validar(caminho, info, privado=caminho == self.pasta, diretorio=True)

    def _criar(self, caminho):
        if self.windows is not None:
            self.windows.criar_privado(caminho, self.operador)
        else:
            caminho.mkdir(mode=0o700, exist_ok=True)

    def _validar(self, caminho, info, *, privado, diretorio):
        modo = info.st_mode
        tipo = stat.S_ISDIR if diretorio else stat.S_ISREG
        if (not tipo(modo) or getattr(info, "st_file_attributes", 0) & 0x400
                or (not diretorio and info.st_nlink != 1)):
            raise PermissionError("caminho_inseguro")
        if self.windows is not None:
            self.windows.validar_acl(caminho, self.operador, privado=privado, diretorio=diretorio)
        elif privado:
            exigido = 0o700 if diretorio else 0o600
            if info.st_uid != self.operador or modo & 0o777 != exigido:
                raise PermissionError("permissoes_posix_inseguras")
        elif (info.st_uid not in (0, self.operador)
              or (modo & 0o022 and not (info.st_uid == 0 and modo & stat.S_ISVTX))):
            raise PermissionError("ancestral_inseguro")

    def arquivo(self, nome):
        caminho = self.pasta / nome
        try:
            info = caminho.lstat()
            self._validar(caminho, info, privado=True, diretorio=False)
        except FileNotFoundError:
            return None
        return info
