"""PyInstaller specification for the P6 standalone executable."""

import os
import sys

script_path = "p6_extractor\\__main__.py" if os.name == "nt" else "p6_extractor/__main__.py"

a = Analysis(
    [script_path],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    name=f"p6-extractor-standalone-{sys.platform}",
    console=True,
)
