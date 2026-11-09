"""PDF-utdata: sidestørrelser, svart-hvitt, bokmerker."""
import numpy as np
import pikepdf
import pytest

from notescanner.pdfout import save_pdf
from tests.synth import make_page, scanned

DPI = 300


@pytest.fixture(scope="module")
def img():
    return scanned(make_page(DPI, size_mm=(148, 210), systems=4), DPI)


@pytest.mark.parametrize("layout", ["a4", "a4fill", "native"])
@pytest.mark.parametrize("bw", [False, True])
def test_layouts(tmp_path, img, layout, bw):
    out = tmp_path / f"t-{layout}-{bw}.pdf"
    save_pdf([(img, DPI, bw), (img, DPI, bw)], str(out), layout=layout, title="Test – æøå")
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2
        w, h = (float(v) for v in pdf.pages[0].mediabox[2:])
        if layout == "native":
            assert abs(w - 148 / 25.4 * 72) < 3 and abs(h - 210 / 25.4 * 72) < 3
        else:
            assert abs(w - 595.3) < 2 and abs(h - 841.9) < 2


def test_bookmarks_with_norwegian_titles(tmp_path, img):
    out = tmp_path / "bok.pdf"
    marks = [(0, "Deilig er jorden"), (1, "Å, kom nå med lovsang"), (2, "Jeg er så glad hver julekveld")]
    save_pdf([(img, DPI)] * 3, str(out), layout="native", bookmarks=marks)
    with pikepdf.open(out) as pdf:
        with pdf.open_outline() as ol:
            names = [it.title for it in ol.root]
    assert names == [m[1] for m in marks]


def test_failed_save_leaves_no_partial_file(tmp_path, img):
    out = tmp_path / "missing_dir" / "x.pdf"
    with pytest.raises(Exception):
        save_pdf([(img, DPI)], str(out))
    assert not out.exists()


def test_locked_pdf_gives_clear_error_and_no_temp_files(tmp_path, monkeypatch):
    """På Windows kan ikke en PDF som er åpen i PDF-leseren skrives over."""
    import os
    from notescanner import pdfout
    img = np.full((600, 400), 255, np.uint8)
    img[100:110, 50:350] = 0
    target = tmp_path / "Låst æøå.pdf"
    target.write_bytes(b"gammel")

    def locked(src, dst):
        raise PermissionError(13, "Ingen tilgang")
    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(RuntimeError, match="PDF-leseren"):
        pdfout.save_pdf([(img, 300)], str(target), bookmarks=[(0, "Sang")])
    assert target.read_bytes() == b"gammel"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Låst æøå.pdf"]
