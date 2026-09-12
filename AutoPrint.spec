# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(__file__).resolve().parent
datas = []
icon_file = ROOT / 'assets' / 'icons' / 'autoprint.ico'
datas.append((str(icon_file), 'assets/icons'))

for src, dest in [
    (ROOT / 'product_catalog.xlsx', '.'),
    (ROOT / 'tmp_batch.csv', '.'),
    (ROOT / 'config' / 'stats.json', 'config'),
    (ROOT / 'config' / 'config.json', 'config'),
]:
    if src.exists():
        datas.append((str(src), dest))

templates_dir = ROOT / 'templates'
if templates_dir.exists():
    for template_path in templates_dir.glob('*.btw'):
        datas.append((str(template_path), 'templates'))

binaries = []
hiddenimports = ['darkdetect', 'tkcalendar']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [str(ROOT / 'autoprint_main.py')],
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
    name='AutoPrint',
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
    icon=str(icon_file),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoPrint',
)
