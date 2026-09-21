# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# 内置 AI 抠图模型（可选）：本机没有就跳过，软件会自动回退到简易去背景
_u2netp = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'PetCursor', 'models', 'u2netp.onnx')

datas = [('web', 'web'), ('catalog.json', '.'), ('icons', 'icons')]
if os.path.exists(_u2netp):
    datas.append((_u2netp, 'models'))
else:
    print('[warn] 未找到 %s，打出来的包将不含内置抠图模型' % _u2netp)
binaries = []
hiddenimports = ['numpy', 'onnxruntime']
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['rembg', 'numba', 'llvmlite', 'pymatting', 'scipy', 'skimage', 'tkinter', 'PyQt5', 'PySide2', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='小虫光标',
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
    icon=['app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='小虫光标',
)
