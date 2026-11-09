# PyInstaller-oppsett for NoteScanner (mappeinstallasjon, ikke én exe – raskere oppstart, og LGPL-bibliotekene
# Qt/PySide6 og img2pdf ligger som egne filer som kan byttes ut).
# Bygg:  pyinstaller --noconfirm packaging/NoteScanner.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "NoteScanner.pyw")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "notescanner" / "winocr.ps1"), "notescanner"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=collect_submodules("notescanner") + ["win32com.client", "pythoncom", "pywintypes"],
    excludes=["tkinter", "pytest", "matplotlib", "pandas", "scipy", "IPython", "PyQt5", "PyQt6", "contourpy",
              "kiwisolver", "sklearn", "numba", "llvmlite", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts"],
    module_collection_mode={"img2pdf": "py"},   # LGPL: ligger som vanlig .py-fil, ikke pakket inn
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NoteScanner",
    icon=str(ROOT / "packaging" / "NoteScanner.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "packaging" / "version_info.txt") if (ROOT / "packaging" / "version_info.txt").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="NoteScanner")
