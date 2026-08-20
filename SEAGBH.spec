# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Gabriel\\SEAGBH\\src\\main_qt.py'],
    pathex=['C:\\Users\\Gabriel\\SEAGBH\\src'],
    binaries=[],
    datas=[
        ('C:\\Users\\Gabriel\\SEAGBH\\src\\seaghb.db', '.'),
        ('C:\\Users\\Gabriel\\SEAGBH\\src\\seaghb_icon.ico', '.'),
        ('C:\\Users\\Gabriel\\SEAGBH\\src\\Backups', 'Backups'),
        ('C:\\Users\\Gabriel\\SEAGBH\\src\\Relatórios', 'Relatórios'),
        ('C:\\Users\\Gabriel\\SEAGBH\\src\\ui\\styles_dark.qss', 'ui'),
        ('C:\\Users\\Gabriel\\SEAGBH\\src\\ui\\styles_light.qss', 'ui'),
    ],
    hiddenimports=[
        'reportlab', 'reportlab.pdfgen', 'reportlab.pdfbase.ttfonts',
        'reportlab.platypus', 'reportlab.lib',
        'sqlite3',
        'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SEAGBH',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    onefile=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\Gabriel\\SEAGBH\\src\\seaghb_icon.ico'],
)
