# -*- mode: python ; coding: utf-8 -*-
# Spec exclusivo da árvore criada por preparar.py.
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import platform
import stat
from struct import calcsize
import sys


if (sys.platform != "win32" or sys.version_info[:2] != (3, 12)
        or platform.machine().upper() != "AMD64" or calcsize("P") != 8
        or sys.prefix == sys.base_prefix):
    raise SystemExit("Use uma venv CPython 3.12 no Windows AMD64.")

raiz = Path(SPECPATH)
if not raiz.is_absolute() or ".." in raiz.parts or str(raiz).startswith(("\\\\", "//")):
    raise SystemExit("A árvore de homologação deve estar em caminho absoluto local.")
for pasta in (*reversed(raiz.parents), raiz):
    info = pasta.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_file_attributes & 0x400:
        raise SystemExit("Diretório indireto na árvore de homologação.")

# Verifica os bytes do validador antes de carregá-lo. A própria origem deste
# spec precisa ter sido conferida antes de entregar o arquivo ao PyInstaller.
validador = raiz / "preparar.py"
info = validador.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_file_attributes & 0x400:
    raise SystemExit("Validador de insumos em caminho não permitido.")
with validador.open("rb") as arquivo:
    codigo = arquivo.read(65537)
if sha256(codigo).hexdigest() != "3428f847b151a2e54be0cedf8ab33444a0b499b23fd8e65f8b6e8a2e981b7a05":
    raise SystemExit("Validador de insumos diverge da revisão aprovada.")
escopo = {"__name__": "_insumos_k70_u1"}
exec(compile(codigo, "preparar.py", "exec"), escopo)
pacote = escopo["validar_arvore"](raiz)

for linha in (raiz / escopo["REQUISITOS"]).read_text(encoding="utf-8").splitlines():
    if linha and not linha.startswith("#"):
        nome, fixada = linha.split()[0].split("==")
        if version(nome) != fixada:
            raise SystemExit("Versão das dependências difere do lock da homologação.")

analise = Analysis(
    [str(pacote / "lancador.py")],
    pathex=[str(pacote)],
    binaries=[],
    datas=[(str(pacote / "variante.json"), ".")],
    hiddenimports=["core._acl_historico_windows"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "core.licenca", "core.firebase_auth", "core.database", "core.historico_licencas",
        "ui", "tools", "sqlite3", "_sqlite3", "PyQt6", "psycopg", "reportlab",
    ],
)
pyz = PYZ(analise.pure)
exe = EXE(
    pyz, analise.scripts, analise.binaries, analise.datas, [],
    name="SEAGBH-HML-K70-U1",
    console=True,
    upx=False,
    exclude_binaries=False,
)
