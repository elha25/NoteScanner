"""Hele appen uten skjerm: importer syntetiske sider, la den behandle og lese dem, lagre PDF, hent fram økta."""
import os
import time

import cv2
import pikepdf
import pytest

from tests.synth import make_page, scanned


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTESCANNER_HOME", str(tmp_path / "home æøå"))   # også norske tegn i stien
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    from notescanner import app as A
    qa = QApplication.instance() or QApplication([])
    A.setup_app(qa)
    return qa


def _pump(qa, cond, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout and not cond():
        qa.processEvents()
        time.sleep(0.02)
    qa.processEvents()
    return cond()


def _window(monkeypatch, answer):
    from PySide6.QtWidgets import QMessageBox
    from notescanner import app as A
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: answer))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    w = A.MainWindow()
    w.auto_update.setChecked(False)
    return w


def _write(tmp, name, img):
    p = os.path.join(tmp, name)
    ok, buf = cv2.imencode(".png", img)
    buf.tofile(p)
    return p


def test_scan_split_save_restore(qapp, home, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w = _window(monkeypatch, QMessageBox.No)
    out = home / "ut"
    w.outdir.setText(str(out))
    w.clear_after.setChecked(False)
    assert _pump(qapp, lambda: w._session_ready, 20)
    files = [
        _write(str(home), "a1.png", scanned(make_page(300, title="Bjelleklang", systems=6), 300)),
        _write(str(home), "a2.png", scanned(make_page(300, title="", part="", composer="", systems=8, seed=3), 300)),
        _write(str(home), "b1.png", scanned(make_page(300, title="Musevisa", systems=6, seed=4), 300)),
    ]
    for f in files:
        w.import_file(f)
    assert _pump(qapp, lambda: len(w.pages) == 3 and all(p.ocr_state != "pending" for p in w.pages))
    docs = w.documents()
    # Windows Server (GitHub Actions) mangler ofte OCR-språk – da testes resten uten tittel-lesingen
    ocr_ok = all(p.sug is not None and p.sug.engine and not p.sug.engine.startswith("feil") for p in w.pages)
    if ocr_ok:
        assert len(docs) == 2, [p.sug for p in w.pages]
        assert w.pages[0].meta.title == "Bjelleklang"
    w.save_all()
    assert _pump(qapp, lambda: w.save_thread is None, 120)
    pdfs = sorted(out.glob("*.pdf"))
    assert len(pdfs) == len(docs)
    with pikepdf.open(pdfs[0]) as pdf:
        assert len(pdf.pages) in (1, 2)
    # økta hentes fram av et nytt vindu
    w._flush_session()
    w._session_ready = False          # ikke slett noe når testvinduet lukkes
    w.close()
    w2 = _window(monkeypatch, QMessageBox.Yes)
    assert _pump(qapp, lambda: w2._session_ready, 20)
    assert len(w2.pages) == 3
    w2._session_ready = False
    w2.close()


def test_corrupt_session_is_kept_not_deleted(qapp, home, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from notescanner import paths
    d = paths.session_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "p1.png").write_bytes(b"x")
    (d / "session.json").write_text('{"version": 2, "pages": [{"id": 1, "dpi": "tull"}]}', encoding="utf-8")
    w = _window(monkeypatch, QMessageBox.Yes)
    assert _pump(qapp, lambda: w._session_ready, 20)
    kept = list(paths.data_dir().glob("session-uleselig-*")) + ([d] if (d / "p1.png").exists() else [])
    assert kept, "den uleselige økta ble slettet"
    w._session_ready = False
    w.close()


def test_transparent_png_is_put_on_white(tmp_path):
    import numpy as np
    from notescanner import app as A
    rgba = np.zeros((200, 300, 4), np.uint8)
    rgba[50:60, 20:280, 3] = 255                 # svart strek, resten helt gjennomsiktig
    p = _write(str(tmp_path), "t.png", rgba)
    img = A.read_image(p)
    assert img.shape == (200, 300, 3)
    assert img[0, 0].min() == 255 and img[55, 100].max() == 0


def test_safe_name():
    from notescanner.app import safe_name
    assert safe_name("CON") == "_CON"
    assert safe_name("Marsj: i C?") == "Marsj_ i C_"
    assert safe_name(" . ", "Noter") == "Noter"
    assert len(safe_name("x" * 400)) <= 150
