# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

datas = [('D:/Games/Euro Truck Simulator 2/Tools/SCS Mega Manager/assets', 'assets'), ('D:/Games/Euro Truck Simulator 2/Tools/SCS Mega Manager/LICENSE', '.')]
binaries = []
hiddenimports = ['PyQt5', 'PyQt5.QtWebEngineWidgets', 'PyQt5.QtWebChannel']
datas += collect_data_files('certifi')
tmp_ret = collect_all('PyQt5.QtWebEngineCore')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:/Games/Euro Truck Simulator 2/Tools/SCS Mega Manager/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='SCS_Tools_Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/Games/Euro Truck Simulator 2/Tools/SCS Mega Manager/assets/icon.ico'],
    contents_directory='redist',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SCS_Tools_Manager',
)
