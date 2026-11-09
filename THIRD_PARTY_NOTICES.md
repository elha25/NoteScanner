# Tredjepartsprogramvare i NoteScanner

NoteScanner er selv under MIT-lisensen (se `LICENSE`). Den installerte versjonen inneholder disse
bibliotekene, uendret, med sine egne lisenser:

| Bibliotek | Lisens | Kildekode |
|---|---|---|
| Python | PSF License | https://www.python.org/ |
| Qt 6 / PySide6 (QtCore, QtGui, QtWidgets, QtNetwork, QtSvg) | LGPL v3 | https://code.qt.io/ · https://download.qt.io/official_releases/QtForPython/ |
| OpenCV (opencv-python-headless) | Apache 2.0 (inkl. FFmpeg under LGPL v2.1) | https://github.com/opencv/opencv-python |
| NumPy | BSD 3-Clause | https://github.com/numpy/numpy |
| Pillow | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| img2pdf | LGPL v3 | https://gitlab.mister-muffin.de/josch/img2pdf |
| pikepdf / qpdf | MPL 2.0 / Apache 2.0 | https://github.com/pikepdf/pikepdf · https://github.com/qpdf/qpdf |
| pywin32 | PSF License | https://github.com/mhammond/pywin32 |
| pytesseract (valgfri) | Apache 2.0 | https://github.com/madmaze/pytesseract |
| PyInstaller-bootloader | GPL v2 med unntak for bygde programmer | https://github.com/pyinstaller/pyinstaller |
| Inno Setup (installasjonsprogrammet) | Inno Setup License | https://jrsoftware.org/isinfo.php |

## LGPL-bibliotekene (Qt/PySide6 og img2pdf)

De ligger som egne, uendrede filer i installasjonsmappa (`_internal\PySide6`, `_internal\img2pdf.py`
m.fl.), ikke bakt inn i programfila, så du kan bytte dem ut med en annen versjon du har bygget selv. Den fullstendige
lisensteksten for LGPL v3 finnes på https://www.gnu.org/licenses/lgpl-3.0.html, og kildekoden til versjonene som
er brukt kan lastes ned fra lenkene over (versjonsnummeret står i `_internal`-mappa).

## Tekstgjenkjenning

Appen bruker tekstgjenkjenningen som følger med Windows (Windows.Media.Ocr). Tesseract er valgfritt og følger
ikke med.
