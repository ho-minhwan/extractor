"""PyInstaller specification for the P6 Windows service."""

import os
import sys

script_path = "p6_extractor\\winservice.py" if os.name == "nt" else "p6_extractor/winservice.py"
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
        "cronex",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f"p6-extractor-winservice-{sys.platform}",
    console=True,
)
