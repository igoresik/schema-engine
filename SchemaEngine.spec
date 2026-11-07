# PyInstaller build of the desktop app (one folder, no console): python -m PyInstaller SchemaEngine.spec
# The UI must be built first (ui: npm run build), because it ships inside schema_engine/app/static.

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

import schema_engine
from schema_engine.sandbox import ALLOWED_IMPORTS

numbers = tuple(int(part) for part in schema_engine.__version__.split(".")) + (0,)
about = {
    "CompanyName": "Georgy Esik",
    "FileDescription": "Schema Engine",
    "FileVersion": schema_engine.__version__,
    "InternalName": "SchemaEngine",
    "LegalCopyright": "Copyright 2026 Georgy Esik",
    "OriginalFilename": "SchemaEngine.exe",
    "ProductName": "Schema Engine",
    "ProductVersion": schema_engine.__version__,
}
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[
        StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in about.items()])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    ["launcher.py"],
    datas=[("src/schema_engine/app/static", "schema_engine/app/static")],
    # generated code may import any allowed module, so all of them ship even if nothing else uses them
    hiddenimports=["schema_engine._runner", "_strptime", *sorted(ALLOWED_IMPORTS)],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PIL", "typer"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SchemaEngine", console=False, icon="assets/icon.ico",
          version=version, upx=False)
COLLECT(exe, a.binaries, a.datas, name="SchemaEngine", upx=False)
