# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for FilttrSupervise.exe (Windows).
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ('assets/filttr-supervisor.der', 'assets'),
    ('assets/filttr-supervisor.crt', 'assets'),
    ('config.json', '.'),
]

hiddenimports = collect_submodules('pymobiledevice3') + [
    'filttr_supervise',
    'filttr_supervise.gui',
    'filttr_supervise.supervise',
    'filttr_supervise.device',
    'filttr_supervise.plist_builder',
    'filttr_supervise.backup_builder',
    'filttr_supervise.mbdb',
    'filttr_supervise.config',
    'filttr_supervise.api',
    'filttr_supervise.users',
    'filttr_supervise.auth',
    'filttr_supervise.safety',
    'filttr_supervise.watcher',
    'filttr_supervise.__main__',
    'asyncio',
    'cryptography',
    'tkinter',
]

# Pull in pymobiledevice3 package data if any
tmp_ret = collect_all('pymobiledevice3')
datas += tmp_ret[0]
hiddenimports += tmp_ret[2]

a = Analysis(
    ['run_app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FilttrSupervise',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app; use --supervise from a console build if needed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
