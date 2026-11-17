# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Filttr Agent.app (macOS).

block_cipher = None

a = Analysis(
    ['filttr_supervise_agent_exe.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'filttr_supervise',
        'filttr_supervise.agent_gui',
        'filttr_supervise.config',
        'filttr_supervise.theme',
        'filttr_supervise.usbtunnel',
        'websockets',
        'websockets.legacy',
        'websockets.legacy.client',
        'websockets.legacy.server',
        'websockets.exceptions',
        'tkinter',
    ],
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
    name='Filttr Agent',
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
    name='Filttr Agent.app',
    icon='assets/filttr.icns' if __import__('os').path.exists('assets/filttr.icns') else None,
    bundle_identifier='org.filttr.agent',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleDisplayName': 'Filttr Agent',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSRequiresAquaSystemAppearance': False,
        'NSUSBMassStorageDeviceUsageDescription':
            'Filttr Agent needs USB access to share your iPhone connection.',
    },
)
