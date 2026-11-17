# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Filttr Supervise.app (macOS).
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ('assets/filttr-supervisor.der', 'assets'),
    ('assets/filttr-supervisor.crt', 'assets'),
    ('assets/filttr-icon-64.png', 'assets'),
    ('assets/filttr-mark-40.png', 'assets'),
    ('assets/filttr-wordmark-30.png', 'assets'),
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
    'filttr_supervise.theme',
    'filttr_supervise.usbtunnel',
    'filttr_supervise.__main__',
    'asyncio',
    'cryptography',
    'tkinter',
    'websockets',
    'websockets.legacy',
    'websockets.legacy.client',
    'websockets.legacy.server',
    'websockets.exceptions',
]

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
    strip=False,
    upx=False,
    console=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
)

app = BUNDLE(
    exe,
    name='Filttr Supervise.app',
    icon='assets/filttr.icns' if __import__('os').path.exists('assets/filttr.icns') else None,
    bundle_identifier='org.filttr.supervise',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleDisplayName': 'Filttr Supervise',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSRequiresAquaSystemAppearance': False,
        'NSUSBMassStorageDeviceUsageDescription':
            'Filttr Supervise needs USB access to supervise iPhones.',
    },
)
