# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

a = Analysis(
    ['filttr_supervise_user_exe.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'filttr_supervise',
        'filttr_supervise.api',
        'filttr_supervise.auth',
        'filttr_supervise.backup_builder',
        'filttr_supervise.config',
        'filttr_supervise.device',
        'filttr_supervise.plist_builder',
        'filttr_supervise.safety',
        'filttr_supervise.supervise',
        'filttr_supervise.theme',
        'filttr_supervise.user_gui',
        'filttr_supervise.watcher',
        'tkinter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
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
    name='Filttr Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes are flagged far more often by antivirus
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/filttr.ico' if os.path.exists('assets/filttr.ico') else None,
    version='version_setup.txt',
)
