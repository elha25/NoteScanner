"""Lag packaging/NoteScanner.ico fra logoen i notescanner/icons.py (kjøres ved bygging)."""
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from notescanner import icons  # noqa: E402


def main(out: Path):
    QGuiApplication.instance() or QGuiApplication([])
    pm = icons._render(icons.LOGO.encode(), 256, 1.0)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Laget {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "packaging" / "NoteScanner.ico")
