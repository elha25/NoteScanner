"""Bildebehandling på syntetiske sider: oppretting, beskjæring, deling av sanger, forbedring."""
import numpy as np
import pytest

from notescanner import processing as P
from tests.synth import add_gutter, make_page, mm, scanned

DPI = 300


@pytest.fixture(scope="module")
def page():
    return make_page(DPI)


@pytest.mark.parametrize("angle", [-2.0, -0.7, 0.6, 1.8])
def test_deskew_finds_angle(page, angle):
    img = scanned(page, DPI, canvas_mm=(216, 300), offset_mm=(3, 2), angle=angle)
    _out, a = P.deskew(img, DPI)
    assert abs(a + angle) < 0.35, (angle, a)


def test_deskew_leaves_straight_page(page):
    img = scanned(page, DPI)
    _out, a = P.deskew(img, DPI)
    assert abs(a) < 0.2


def test_crop_keeps_title_and_last_system(page):
    img = scanned(page, DPI, canvas_mm=(216, 300), offset_mm=(3, 2))
    x0, y0, x1, y1 = P.content_bbox(img, DPI)
    oy = mm(2, DPI)
    title_top = oy + mm(16, DPI)                        # tittelen starter 16 mm ned på arket
    assert y0 <= title_top + mm(1, DPI)
    last_system_bottom = oy + mm(16 + 20 + 6 * 13, DPI) + mm(1.75 * 4, DPI)
    assert y1 >= last_system_bottom
    assert x0 <= mm(3 + 18, DPI) + 2 and x1 >= mm(3 + 210 - 18, DPI) - 2


def test_crop_title_close_to_scan_edge(page):
    """Tittel nær kanten av skannet skal ikke tolkes som papirkant (feil i v2.1.7)."""
    img = scanned(page[mm(10, DPI):], DPI)              # skåret slik at tittelen er ~6 mm fra kanten
    _x0, y0, _x1, _y1 = P.content_bbox(img, DPI)
    assert y0 <= mm(7, DPI)


def test_neighbour_page_below_is_removed(page):
    top = scanned(page, DPI)
    other = scanned(make_page(DPI, title="Neste sang", seed=5), DPI)
    gap = np.full((mm(24, DPI), top.shape[1]), 238, np.uint8)
    img = np.vstack([top[: mm(150, DPI)], gap, other[: mm(40, DPI)]])
    img = add_gutter(img, mm(150 + 12, DPI), axis=0)
    _x0, _y0, _x1, y1 = P.content_bbox(img, DPI)
    assert y1 < mm(150 + 2, DPI)


def test_two_songs_on_one_page_are_split():
    img = scanned(make_page(DPI, systems=3, second_song="Musevisa"), DPI)
    cuts = P.song_splits(img, DPI)
    assert len(cuts) == 1
    second_title = mm(16 + 20 + 3 * 13 + 6, DPI)
    assert mm(16 + 20 + 2 * 13, DPI) < cuts[0] < second_title


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("faint", [1.0, 0.45])
def test_single_song_is_not_split(seed, faint):
    img = scanned(make_page(DPI, seed=seed), DPI, faint=faint, seed=seed)
    assert P.song_splits(img, DPI) == []


@pytest.mark.parametrize("mode", ["gray", "bw", "color"])
def test_enhance_keeps_shape_and_type(page, mode):
    img = scanned(page, DPI)
    if mode == "color":
        img = np.dstack([img, img, img])
    out = P.enhance(img, DPI, mode, 5)
    assert out.dtype == np.uint8 and out.shape[:2] == img.shape[:2]
    # papiret blir lyst (hvitt i gråtone/svart-hvitt; farge justerer bare lys/kontrast), blekket mørkt
    assert np.percentile(out, 50) > (230 if mode == "color" else 245)
    assert out.min() < 60


def test_blank_page():
    blank = scanned(np.full((mm(297, DPI), mm(210, DPI)), 255, np.uint8), DPI)
    assert P.is_blank(blank, DPI)
    assert P.content_bbox(blank, DPI) is None


def test_odd_inputs_do_not_crash():
    for img in (np.zeros((1, 1), np.uint8), np.zeros((40, 3000), np.uint8), np.full((500, 500), 255, np.uint8),
                np.zeros((300, 300, 3), np.uint8)):
        P.content_bbox(img, DPI)
        P.deskew(img, DPI)
        P.song_splits(img, DPI)


def test_cream_paper_on_white_lid_keeps_everything():
    """Gulnet/kremfarget ark på hvitt lokk: tomrommet mellom systemene er ingen «brett» (feil før 2.3.0)."""
    pg = make_page(DPI, size_mm=(180, 260), systems=8, seed=7)
    sheet = scanned(pg, DPI, paper=222)
    img = np.full((mm(297, DPI), mm(216, DPI)), 250, np.uint8)
    oy, ox = mm(12, DPI), mm(10, DPI)
    img[oy:oy + sheet.shape[0], ox:ox + sheet.shape[1]] = sheet
    ys = np.where((pg < 128).any(axis=1))[0]
    _x0, y0, _x1, y1 = P.content_bbox(img, DPI)
    assert y0 <= oy + ys.min() + 2 and y1 >= oy + ys.max() - 2


def test_faint_page_is_not_skipped(page):
    """Bleik kopi (blekk ~185 på papir ~232) skal ikke regnes som tom side."""
    img = scanned(page, DPI, paper=232, faint=0.27)
    assert not P.is_blank(img, DPI)


def test_low_dpi_finds_staves():
    pg = make_page(150, systems=7)
    staves, _s = P.find_staves(pg, 150)
    assert len(staves) == 7
    assert P.song_splits(scanned(pg, 150), 150) == []


def test_sparse_straight_page_is_not_rotated():
    from PIL import Image, ImageDraw
    im = Image.new("L", (mm(210, DPI), mm(297, DPI)), 238)
    d = ImageDraw.Draw(im)
    d.ellipse([600, 900, 1700, 2000], outline=40, width=12)        # omslag med en stor figur og lite tekst
    d.rectangle([700, 2300, 1600, 2380], fill=40)
    assert abs(P.detect_skew(np.asarray(im), DPI)) < 0.15


def test_black_page_is_fast():
    import time
    img = np.full((mm(297, DPI), mm(210, DPI)), 15, np.uint8)
    t0 = time.time()
    P.deskew(img, DPI)
    assert time.time() - t0 < 2.0
