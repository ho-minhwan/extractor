"""Specification to create a Windows EXE file."""

import os
import sys

if os.name == "nt":
    script_path = "proarc_extractor\\__main__.py"
else:
    script_path = "proarc_extractor/__main__.py"

spec_root = os.path.abspath(SPECPATH)

a = Analysis(
    [script_path],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f"proarc-extractor-standalone-{sys.platform}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon="logo.ico",
)
