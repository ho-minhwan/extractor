"""Specification to create a Windows Service."""

import os
import sys

if os.name == "nt":
    script_path = "aveva_technical_extractor\\winservice.py"
else:
    script_path = "aveva_technical_extractor/winservice.py"

spec_root = os.path.abspath(SPECPATH)

a = Analysis(
    [script_path],
    pathex=[spec_root],
    binaries=[],
    datas=[],
    hiddenimports=[
        "win32timezone",
        "win32service",
        "win32serviceutil",
        "servicemanager",
        "win32event",
        "win32evtlog",
        "win32evtlogutil",
        "cronex"
    ],
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
    name=f"aveva-technical-extractor-winservice-{sys.platform}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon="logo.ico",
)
