"""Tolking av tittelfeltet (uten selve tekstgjenkjenningen) og ett integrasjonstest med Tesseract."""
import random
import shutil

import pytest

from notescanner import ocr
from notescanner.ocr import Line, nice_title, parse


@pytest.mark.parametrize("raw, want", [
    ("MARCH—BEYOND THE BLUE", "March - Beyond the Blue"),
    ("SONG ARRANGENIENT - AND HEAR!", "Song Arrangement - And Hear!"),
    ("MARCH-JOYFUL HEART", "March - Joyful Heart"),
    ("MARCH FROM 'ALCESTE'", "March from 'Alceste'"),
    ("No. 40 SONG ARRANGEMENT LORD OF CREATION", "Song Arrangement - Lord of Creation"),
    ("SONG WITHOUT WORDS", "Song Without Words"),
    ("Marsj i C", "Marsj i C"),
    ("W hen You W ish Upon a Star", "When You Wish Upon a Star"),
    ("Det er jul", "Det er jul"),
    ("Mass in B minor", "Mass in B minor"),
    ("Marsj i F dur", "Marsj i F dur"),
    ("Æ har en drøm", "Æ har en drøm"),
    ("YOU'LL NEVER WALK ALONE", "You'll Never Walk Alone"),
    ("O'BRIEN'S MARCH", "O'Brien's March"),
    ("II. ANDANTE", "II. Andante"),
    ("76 TROMBONES", "76 Trombones"),
    ("12. Deilig er jorden", "Deilig er jorden"),
    ("MARCH‐JOYFUL HEART", "March - Joyful Heart"),
])
def test_nice_title(raw, want):
    assert nice_title(raw) == want


def test_nice_title_idempotent():
    for raw in ["MARCH—BEYOND THE BLUE", "Det hev ei rose sprunge", "HYMN SETTING— ACCRINGTON",
                "CHORUS TIME NO. 3 (continued)", "A FANTASY OF CAROLS", "B THE", "ÉN GANG", "PLAN B THE MUSICAL"]:
        once = nice_title(raw)
        assert nice_title(once) == once


def _words(text, x, y, h, cw=None):
    out, cx = [], x
    for tok in text.split():
        ww = (cw or h * 0.7) * len(tok)
        out.append((cx, y, ww, h, tok))
        cx += ww + h * 0.4
    return out


def _line(words):
    x0 = min(q[0] for q in words)
    x1 = max(q[0] + q[2] for q in words)
    return Line(" ".join(q[4] for q in words), x0, min(q[1] for q in words), x1 - x0,
                sorted(q[3] for q in words)[len(words) // 2], words)


W = 3900


def L(t, x, y, w, h):
    return Line(t, x, y, w, h)


@pytest.mark.parametrize("name, lines, title, composer", [
    ("komponistlinje er ikke tittel",
     [L("No. 51", 60, 20, 260, 68), L("2nd CORNET Bb", 60, 120, 600, 70),
      L("SONG ARRANGEMENT—YES, JESUS LOVES ME", 1100, 90, 1900, 74), L("Bandsman TERRY CAMSEY", 2800, 200, 1000, 59)],
     "Song Arrangement - Yes, Jesus Loves Me", "Bandsman TERRY CAMSEY"),
    ("komponist på samme linje skilles ut",
     [L("2nd CORNET Bb", 60, 120, 600, 68),
      _line(_words("MARCH - MORNING GLORY", 1200, 80, 88) + _words("JAMES ANDERSON", 3100, 95, 66, 40))],
     "March - Morning Glory", "JAMES ANDERSON"),
    ("music i tittel", [L("MARCH—MUSIC CAMP", 1300, 80, 1100, 83), L("ERIK SILFVERBERG", 3000, 230, 800, 66)],
     "March - Music Camp", "ERIK SILFVERBERG"),
    ("tempo er ikke tittel", [L("MARCH—ON THE MARCH", 1300, 90, 1270, 73), L("Allexro moderato", 300, 285, 540, 69)],
     "March - On the March", ""),
])
def test_parse_layouts(name, lines, title, composer):
    s = parse(lines, W)
    assert s.title == title, name
    if composer:
        assert s.composer == composer, name


def test_parse_never_crashes_on_garbage():
    rnd = random.Random(7)
    alphabet = "abcæøåXYZ0123456789 .,-—'\"()!?*/\\|<>"
    for _ in range(400):
        lines = []
        for _ in range(rnd.randint(0, 8)):
            t = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
            lines.append(Line(t, rnd.uniform(0, W), rnd.uniform(0, 400), rnd.uniform(0, 2000), rnd.uniform(0, 120)))
        parse(lines, W)
        for ln in lines:
            nice_title(ln.text)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract er ikke installert")
def test_read_header_on_synthetic_page():
    from tests.synth import make_page, scanned
    img = scanned(make_page(300, title="Bjelleklang", composer="Arr: Kari Nordmann"), 300)
    s = ocr.read_header(img, 300)
    assert s.title == "Bjelleklang"
    assert s.is_title_page


def _std(title, comp="Arr: Per Ørjan Kjærvik"):
    """Typisk tittelside: stemme øverst til venstre, stor midtstilt tittel, komponist til høyre, tempo."""
    def line(text, x, y, h, align="left"):
        ws = _words(text, 0, y, h)
        tw = ws[-1][0] + ws[-1][2]
        off = {"left": x, "center": W / 2 - tw / 2, "right": W - x - tw}[align]
        return _line([(q[0] + off, q[1], q[2], q[3], q[4]) for q in ws])
    return [line("Trompet 1 i Bb", 60, 70, 34), line(title, 0, 120, 62, "center"),
            line(comp, 60, 190, 30, "right"), line("Allegro moderato", 120, 250, 28)]


@pytest.mark.parametrize("title", ["By the Rivers of Babylon", "Mr. Sandman", "Musikk i fjellet", "Av og til",
                                   "Melody in F", "Major Tom", "Largo", "Air", "Jul"])
def test_title_that_looks_like_composer_or_tempo(title):
    sug = parse(_std(title), W)
    assert sug.title == title
    assert sug.composer == "Per Ørjan Kjærvik"


@pytest.mark.parametrize("comp, want", [("Tekst og musikk: Ola Nordmann", "Ola Nordmann"),
                                        ("Melodi: Ludvig M. Lindeman", "Ludvig M. Lindeman"),
                                        ("Words and Music by John Smith", "John Smith"),
                                        ("Edv. Grieg (1843–1907)", "Edv. Grieg (1843–1907)")])
def test_composer_prefix(comp, want):
    assert parse(_std("Deilig er jorden", comp), W).composer == want


def test_odd_header_images():
    import numpy as np
    for img in (np.zeros((1, 1), np.uint8), np.zeros((0, 0), np.uint8)):
        assert not ocr.read_header(img, 300).title
