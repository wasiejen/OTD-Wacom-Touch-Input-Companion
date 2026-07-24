# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['touch_controller.py'],
    pathex=[],
    binaries=[],
    datas=[('../.venv/Lib/site-packages/hid.cp312-win_amd64.pyd', '.')],
    hiddenimports=['hid', 'pynput.keyboard._win32', 'pynput.mouse._win32', 'pystray._win32'],
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
    name='OTD_Wacom_Touch_Driver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
