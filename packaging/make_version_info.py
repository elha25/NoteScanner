"""Lag packaging/version_info.txt (versjonsinfo i exe-fila: Egenskaper → Detaljer i Windows)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from notescanner import __version__  # noqa: E402

parts = [int(p) for p in (__version__.split(".") + ["0", "0", "0"])[:4]]
t = tuple(parts)
(ROOT / "packaging" / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[StringFileInfo([StringTable('041404b0', [
      StringStruct('CompanyName', 'Elias Haaland'),
      StringStruct('FileDescription', 'NoteScanner – skann noter til PDF'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'NoteScanner'),
      StringStruct('LegalCopyright', 'MIT-lisens'),
      StringStruct('OriginalFilename', 'NoteScanner.exe'),
      StringStruct('ProductName', 'NoteScanner'),
      StringStruct('ProductVersion', '{__version__}')])]),
        VarFileInfo([VarStruct('Translation', [0x0414, 1200])])]
)
""", encoding="utf-8")
print("Laget packaging/version_info.txt for", __version__)
