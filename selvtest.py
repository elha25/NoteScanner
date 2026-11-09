"""Sjekk at NoteScanner virker på denne PC-en.  Kjør:  python selvtest.py

Tester: pakker, bildebehandling, PDF-lagring, OCR (tittel/stemme) og om skanneren finnes.
Skanner ikke noe ark – sjekker bare at skanneren svarer.
"""
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Pakkemappa kan hete «NoteScanner» etter utpakking – registrer den som «notescanner»
import importlib  # noqa: E402
for _n in os.listdir(HERE):
    if _n.lower() == "notescanner" and _n != "notescanner" and os.path.isfile(os.path.join(HERE, _n, "app.py")):
        sys.modules["notescanner"] = importlib.import_module(_n)
        for _m in ("processing", "ocr", "pdfout", "scanner", "icons", "theme"):
            sys.modules[f"notescanner.{_m}"] = importlib.import_module(f"{_n}.{_m}")

ok = True


def step(name, fn):
    global ok
    try:
        res = fn()
        print(f"[OK]   {name}" + (f": {res}" if res else ""))
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[FEIL] {name}: {e}")
        traceback.print_exc(limit=2)


def imports():
    import cv2, numpy, PIL, img2pdf, PySide6, PySide6.QtSvg  # noqa: F401,E401
    try:
        import pikepdf  # noqa: F401
        bm = "bokmerker OK"
    except ImportError:
        bm = "pikepdf mangler – PDF-ene får ikke bokmerker"
    return f"Python {sys.version.split()[0]}, PySide6 {PySide6.__version__}, OpenCV {cv2.__version__}, {bm}"


def gui():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from notescanner import app as A
    A.setup_app(app)
    w = A.MainWindow()
    # Ikke lukk vinduet: det ville lagret innstillinger og ryddet i autolagret økt
    w.proc_thread.stop()
    w.ocr_thread.stop()
    return f"vinduet bygges (v{A.APP_VERSION})"


def make_page():
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from notescanner import processing as p
    W, H = 2550, 3508
    im = Image.new("L", (W, H), 238)
    d = ImageDraw.Draw(im)

    def font(size):
        for f in ("arial.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
        return ImageFont.load_default(size)

    d.text((300, 330), "2. Kornett i Bb", font=font(48), fill=20)
    tw = d.textlength("Marsj i C", font=font(110))
    d.text(((W - tw) / 2, 380), "Marsj i C", font=font(110), fill=10)
    cw = d.textlength("Arr. Kari Nordmann", font=font(44))
    d.text((2250 - cw, 600), "Arr. Kari Nordmann", font=font(44), fill=20)
    d.text((300, 660), "Tempo di marcia", font=font(44), fill=20)
    a = np.array(im)
    for s in range(7):
        y0 = 760 + s * 330
        for k in range(5):
            cv2.line(a, (300, y0 + k * 25), (2250, y0 + k * 25), 30, 3)
        for n in range(14):
            cv2.ellipse(a, (380 + n * 130, y0 + 50 + (n % 5) * 12), (18, 13), -20, 0, 360, 10, -1)
    return p.rotate(cv2.cvtColor(a, cv2.COLOR_GRAY2BGR), 1.5)


state = {}


def processing():
    from notescanner import processing as p
    img = make_page()
    img, ang = p.deskew(img, 300)
    img, bb = p.auto_crop(img, 300, 5)
    state["img"] = p.enhance(img, 300)
    if bb is None:
        raise RuntimeError("auto-crop fant ikke innhold")
    return f"rettet opp {ang:+.1f}° (ventet ca. -1.5), beskåret til {img.shape[1]}x{img.shape[0]} px"


def pdf():
    from notescanner.pdfout import save_pdf
    from notescanner import processing as p
    path = os.path.join(tempfile.gettempdir(), "notescanner_selvtest.pdf")
    bw = p.enhance(state["img"], 300, "bw", 4)
    save_pdf([(p.encode_png(state["img"]), 300), (bw, 300, True)], path, "a4", title="Selvtest",
             bookmarks=[(0, "Gråtone"), (1, "Svart-hvitt")])
    return f"{path} ({os.path.getsize(path) // 1024} KB, gråtone + svart-hvitt med bokmerker)"


CI = bool(os.environ.get("CI"))     # GitHub Actions: ingen skanner, og kanskje ingen OCR-språk i Windows Server


def ocr():
    from notescanner import ocr as o
    eng = o.available_engine()
    if not eng:
        if CI:
            return "hoppet over på CI (ingen OCR-motor)"
        raise RuntimeError("ingen OCR-motor funnet")
    try:
        sug = o.read_header(state["img"], 300)
    except Exception as e:  # noqa: BLE001
        if CI:
            return f"hoppet over på CI ({e})"
        raise
    return f"[{sug.engine}] tittel={sug.title!r} stemme={sug.part!r} komponist={sug.composer!r}  " \
           f"(ventet 'Marsj i C', '2. Kornett i Bb', 'Kari Nordmann')"


def scanner():
    from notescanner import scanner as s
    if not s.available():
        return "hoppet over (ikke Windows)"
    if CI:
        return "hoppet over på CI"
    lst = s.list_scanners()
    if not lst:
        raise RuntimeError("fant ingen skanner via WIA – er skanneren koblet til og driveren installert?")
    return ", ".join(name for _, name in lst)


step("Pakker", imports)
step("Grensesnitt", gui)
step("Bildebehandling", processing)
if "img" in state:
    step("PDF-lagring", pdf)
    step("Tekstlesing (OCR)", ocr)
step("Skanner (WIA)", scanner)
print("\nAlt OK!" if ok else "\nNoe feilet – se over.")
sys.exit(0 if ok else 1)
