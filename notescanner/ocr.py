"""Les tittel, stemme og komponist fra toppen av en skannet noteside.

OCR-motor:
  1. Windows' innebygde OCR via PowerShell (Windows 10/11, ingen ekstra pakker, alle Python-versjoner)
  2. Windows' innebygde OCR via winrt-pakkene (hvis installert – raskere, finnes til Python 3.14)
  3. Tesseract, hvis installert (C:\\Program Files\\Tesseract-OCR)
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import cv2
import numpy as np

from . import paths
from . import processing as proc


@dataclass
class Line:
    text: str
    x: float
    y: float
    w: float
    h: float  # bokstavhøyde (median ordhøyde)
    words: list | None = None   # [(x, y, w, h, tekst), ...] når motoren gir ordene

    @property
    def cx(self):
        return self.x + self.w / 2


@dataclass
class Suggestion:
    title: str = ""
    part: str = ""
    composer: str = ""
    engine: str = ""
    header_mm: float = 0.0   # høyden på feltet over første notesystem
    title_mm: float = 0.0    # skrifthøyden på tittelen
    staff_mm: float = 0.0    # avstand mellom notelinjene (0 = fant ingen notelinjer)
    title_ok: bool = False   # tittelen er klart størst på siden, selv om den ser ut som et tempo-/sjangerord («Largo»)

    def __bool__(self):
        return bool(self.title or self.part)

    @property
    def is_title_page(self) -> bool:
        """Ser siden ut som første side av et nytt stykke (stor tittel over første notesystem)?"""
        if self.title and (_is_real_title(self.title) or self.title_ok):
            # Marsjbøker har ofte lavt tittelfelt (1–2 cm) og liten skrift. Tittelen måles derfor mot
            # avstanden mellom notelinjene: i A5-bøker og på A4-ark er tittelen 2,5–4 ganger så høy,
            # mens tempo, stemme og komponist er rundt én til to ganger.
            big = self.title_mm >= 4.5 or (self.staff_mm > 0 and self.title_mm >= 2.5 * self.staff_mm)
            return big and self.header_mm >= 10.0
        return self.header_mm >= 30.0  # uten OCR: bare et høyt, tomt-for-noter felt øverst


# ------------------------------------------------------------------ OCR-motorer

def _windows_ocr(img: np.ndarray) -> list[Line] | None:
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        return None

    engine = None
    for tag in ("nb-NO", "nb", "nn-NO", "da-DK", "sv-SE", "de-DE"):
        try:
            lang = Language(tag)
            if OcrEngine.is_language_supported(lang):
                engine = OcrEngine.try_create_from_language(lang)
                if engine:
                    break
        except Exception:
            pass
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return None

    gray = proc.to_gray(img)
    maxdim = int(OcrEngine.max_image_dimension or 4000)
    scale = min(1.0, maxdim / max(gray.shape))
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    bgra = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA)
    h, w = gray.shape

    async def run():
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
        return await engine.recognize_async(bmp)

    result = asyncio.run(run())
    lines = []
    for ln in result.lines:
        rects = [wd.bounding_rect for wd in ln.words]
        if not rects:
            continue
        x0 = min(r.x for r in rects)
        y0 = min(r.y for r in rects)
        x1 = max(r.x + r.width for r in rects)
        hs = sorted(r.height for r in rects)
        wds = [(r.x / scale, r.y / scale, r.width / scale, r.height / scale, wd.text)
               for r, wd in zip(rects, ln.words)]
        lines.append(Line(ln.text, x0 / scale, y0 / scale, (x1 - x0) / scale, hs[len(hs) // 2] / scale, wds))
    return lines


WIN_OCR_MAX = 2600   # OcrEngine.MaxImageDimension i Windows 10/11 – større bilder gir feil
_PS1 = str(paths.resource("notescanner", "winocr.ps1"))   # fungerer både fra kildekode og i exe-versjonen
LANGS = "nb-NO,nb,nn-NO,da-DK,sv-SE,en-US,de-DE"


def _powershell_exe():
    if sys.platform != "win32":
        return None
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return exe if os.path.exists(exe) else shutil.which("powershell.exe")


def _powershell_ocr(img: np.ndarray) -> list[Line] | None:
    exe = _powershell_exe()
    if not exe or not os.path.exists(_PS1):
        return None
    gray = proc.to_gray(img)
    scale = min(1.0, WIN_OCR_MAX / max(gray.shape))   # Windows OCR tar ikke større bilder
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="notescanner_")
    os.close(fd)
    try:
        # imencode + tofile i stedet for imwrite: OpenCV klarer ikke filstier med æøå på Windows
        ok, buf = cv2.imencode(".png", gray)
        if not ok:
            return None
        buf.tofile(tmp)
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", _PS1, tmp, LANGS],
                           capture_output=True, timeout=60,
                           # uten konsollvindu, og med lavere prioritet enn skanneren
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    out = r.stdout.decode("utf-8-sig", errors="replace").strip().lstrip("\ufeff")
    err = r.stderr.decode("utf-8", errors="replace").strip()
    if not out:
        # Tomt svar: skriptet fikk ikke kjøre (gruppepolicy, antivirus) eller krasjet – ikke late som siden er tom
        raise RuntimeError(f"PowerShell-OCR svarte ikke (kode {r.returncode}): {err[:300]}")
    try:
        data = json.loads(out.splitlines()[-1].lstrip("\ufeff"))
    except (json.JSONDecodeError, IndexError):
        raise RuntimeError(f"PowerShell-OCR ga uventet svar: {out[:200]} {err[:200]}")
    if not isinstance(data, dict):
        raise RuntimeError(f"PowerShell-OCR ga uventet svar: {out[:200]}")
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    if "lines" not in data:
        raise RuntimeError(f"PowerShell-OCR ga uventet svar: {out[:200]} {err[:200]}")
    lines = []
    raw = data.get("lines") or []
    for ln in raw if isinstance(raw, list) else [raw]:
        words = ln.get("words") or []
        words = words if isinstance(words, list) else [words]
        if not words:
            continue
        x0 = min(w["x"] for w in words)
        y0 = min(w["y"] for w in words)
        x1 = max(w["x"] + w["w"] for w in words)
        hs = sorted(w["h"] for w in words)
        wds = [(w["x"] / scale, w["y"] / scale, w["w"] / scale, w["h"] / scale, str(w.get("t", ""))) for w in words]
        ok = all(q[4] for q in wds)
        lines.append(Line(ln.get("text", ""), x0 / scale, y0 / scale, (x1 - x0) / scale,
                          _word_height(wds) if ok else hs[len(hs) // 2] / scale, wds if ok else None))
    return lines


def _tesseract_cmd():
    # shutil.which leter også i arbeidsmappa på Windows – bruk bare treff som ligger på PATH (absolutt sti
    # utenfor arbeidsmappa) eller standardplasseringen
    found = shutil.which("tesseract")
    if found and os.path.dirname(os.path.abspath(found)) == os.path.abspath(os.getcwd()):
        found = None
    for c in (found,
              r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if c and os.path.exists(c):
            return c
    return None


def _tesseract_ocr(img: np.ndarray) -> list[Line] | None:
    cmd = _tesseract_cmd()
    if not cmd:
        return None
    try:
        import pytesseract
    except ImportError:
        return None
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception:
        langs = {"eng"}
    lang = "+".join([l for l in ("nor", "eng", "deu") if l in langs]) or "eng"
    d = pytesseract.image_to_data(proc.to_gray(img), lang=lang, config="--psm 11",
                                  output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list[int]] = {}
    for i, t in enumerate(d["text"]):
        if t.strip() and float(d["conf"][i]) > 10:
            groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
    # psm 11 gir ofte ett ord per "linje" – slå sammen ord på samme høyde som står tett
    words = [(d["left"][i], d["top"][i], d["width"][i], d["height"][i], d["text"][i])
             for idx in groups.values() for i in idx]
    return _group_words(words)


def _group_words(words) -> list[Line]:
    """Slå sammen enkeltord (x, y, w, h, tekst) til linjer: samme høyde på siden, tett i bredden."""
    rows: list[list] = []
    for wd in sorted(words, key=lambda w: -w[3]):          # store ord først, små ord («er», «a») legges til
        y0, y1, h = wd[1], wd[1] + wd[3], wd[3]
        for row in rows:
            r0 = min(q[1] for q in row)
            r1 = max(q[1] + q[3] for q in row)
            rh = r1 - r0
            overlap = min(y1, r1) - max(y0, r0)
            if overlap > 0.6 * min(h, rh) and 0.4 < h / max(rh, 1) < 1.8:
                row.append(wd)
                break
        else:
            rows.append([wd])
    out = []
    for row in rows:
        row.sort(key=lambda q: q[0])
        seg = [row[0]]
        for wd in row[1:]:
            prev = seg[-1]
            rh = float(np.median([q[3] for q in seg]))
            if wd[0] - (prev[0] + prev[2]) > 2.5 * rh:
                out.append(seg)
                seg = [wd]
            else:
                seg.append(wd)
        out.append(seg)
    lines = []
    for seg in out:
        x0 = min(q[0] for q in seg)
        y0 = min(q[1] for q in seg)
        x1 = max(q[0] + q[2] for q in seg)
        lines.append(Line(" ".join(q[4] for q in seg), x0, y0, x1 - x0, _word_height(seg), list(seg)))
    return lines


def ocr_lines(img: np.ndarray):
    errors = []
    for name, fn in (("Windows OCR", _windows_ocr), ("Windows OCR (PowerShell)", _powershell_ocr),
                     ("Tesseract", _tesseract_ocr)):
        try:
            lines = fn(img)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            lines = None
        if lines is not None:
            return lines, name
    if errors:
        raise RuntimeError("; ".join(errors))
    return None, ""


def available_engine() -> str:
    try:
        import winrt.windows.media.ocr  # noqa: F401
        return "Windows OCR"
    except ImportError:
        pass
    if _powershell_exe() and os.path.exists(_PS1):
        return "Windows OCR (PowerShell)"
    if _tesseract_cmd():
        try:
            import pytesseract  # noqa: F401
            return "Tesseract"
        except ImportError:
            pass
    return ""


# ------------------------------------------------------------------ finn tittelfeltet

def header_region(img: np.ndarray, dpi: int):
    """Utsnitt over første notelinje (der tittel/stemme/komponist står)."""
    return header_info(img, dpi)[0]


def header_info(img: np.ndarray, dpi: int):
    """(utsnitt, høyde i mm) for feltet over første notesystem."""
    region, header_mm, _ = _header(img, dpi)
    return region, header_mm


def _staff_space(rows: np.ndarray, start: int, dpi: int) -> float:
    """Avstanden mellom notelinjene i første notesystem, i mm."""
    near = rows[(rows >= start) & (rows < start + proc.mm_to_px(15, dpi))]
    centers, grp = [], []
    for r in near:
        if grp and r - grp[-1] > 1:
            centers.append(float(np.mean(grp)))
            grp = []
        grp.append(r)
    if grp:
        centers.append(float(np.mean(grp)))
    d = np.diff(centers)
    d = d[(d > proc.mm_to_px(0.7, dpi)) & (d < proc.mm_to_px(3.5, dpi))]
    return float(np.median(d)) / dpi * 25.4 if len(d) else 0.0


def _header(img: np.ndarray, dpi: int):
    """(utsnitt, høyde over første notesystem i mm, notelinjeavstand i mm)."""
    gray = proc.to_gray(img)
    h, w = gray.shape
    bbox = proc.content_bbox(img, dpi)
    x0, y0, x1, y1 = bbox if bbox else (0, 0, w, h)
    mask = proc._ink_mask(gray, dpi)[y0:y1, x0:x1]
    # Notelinjer: rader der en stor del av bredden er blekk
    k = max(15, (x1 - x0) // 8)
    long_lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, k), np.uint8))
    rows = np.where((long_lines > 0).sum(axis=1) > 0.4 * (x1 - x0))[0]
    limit = int(0.30 * (y1 - y0))
    staff_top = None
    for r in rows:
        # krev minst 4 slike rader innen ca. 15 mm (5 notelinjer) for å regne det som et notesystem
        if (np.sum((rows >= r) & (rows < r + proc.mm_to_px(15, dpi))) >= 4):
            staff_top = r
            break
    cut = staff_top - proc.mm_to_px(1, dpi) if staff_top and staff_top > proc.mm_to_px(8, dpi) else limit
    cut = min(cut, int(0.40 * (y1 - y0)))
    pad = proc.mm_to_px(3, dpi)
    region = img[max(0, y0 - pad):y0 + cut, max(0, x0 - pad):min(w, x1 + pad)]
    header_mm = (staff_top if staff_top is not None else cut) / dpi * 25.4
    staff_mm = _staff_space(rows, staff_top, dpi) if staff_top is not None else 0.0
    return region, header_mm, staff_mm


# ------------------------------------------------------------------ tolk linjene

INSTRUMENTS = [
    # messing / korps
    "kornett", "cornet", "trompet", "trumpet", "tromba", "flygelhorn", "flugelhorn", "flügelhorn",
    "althorn", "alto horn", "tenorhorn", "tenor horn", "baryton", "baritone", "bariton", "euphonium",
    "eufonium", "trombone", "trombon", "basun", "posaune", "tuba", "tuba", "bass", "kontrabass",
    "horn", "valthorn", "waldhorn", "repiano", "sousafon", "sousaphone",
    # treblås
    "fløyte", "flute", "flöte", "piccolo", "pikkolo", "obo", "oboe", "fagott", "bassoon", "klarinett",
    "clarinet", "klarinette", "saksofon", "saxofon", "saxophone", "sax", "altsax", "tenorsax",
    "barytonsax",
    # slagverk og annet
    "slagverk", "percussion", "perc", "trommer", "drums", "skarptromme", "snare", "glockenspiel",
    "klokkespill", "xylofon", "xylophone", "pauker", "timpani", "cymbal", "bekken", "marimba",
    "piano", "partitur", "score", "direksjon", "conductor", "dirigent", "fiolin", "violin", "bratsj",
    "viola", "cello", "vokal", "sopran", "soprano", "alt", "alto", "tenor", "gitar", "guitar",
]
_INSTR_RE = re.compile(r"(?<![a-zæøåäöü])(" + "|".join(sorted({re.escape(i) for i in INSTRUMENTS},
                       key=len, reverse=True)) + r")(?:s|er|ene|en|e)?(?![a-zæøåäöü])", re.I)
# stemme uten instrumentnavn: «2. stemme», «C-stemme», «Stemme 3», «1st part»
_VOICE_RE = re.compile(r"(?<![\wæøå])(\d\.?\s*stemme|[A-H](b|es|is)?-stemme|stemme\s+\d|\d(st|nd|rd|th)?\.?\s+part)"
                       r"(?![\wæøå])", re.I)
_KEY_RE = re.compile(r"(?<![A-Za-z])(B|Bb|B♭|Eb|Es|E♭|F|C|Ess)(?![a-z])")
_COMPOSER_RE = re.compile(r"\b(arr|arrangert|arrangement|arranged|komp|musikk|music|musik|by|av|tekst|"
                          r"words|lyrics|text|komponert|composed|bearb|instr|transcr|satz)\b\.?:?", re.I)
# Komponist-nøkkelord: i starten av linja («Musikk: …», «By …») eller «music by», «composed by»
_COMP_KW_RE = re.compile(r"^\s*(komp|komponist|musikk|music|musik|melodi|melody|by|av|composed|komponert)\b"
                         r"|\b(music|musikk|composed|melody|melodi)\s+(by|av)\b", re.I)
_COMP_PREFIX_RE = re.compile(
    r"^\s*(?:(?:tekst|text|words|lyrics|music|musikk|musik|melodi|melody|komponist|komp|composed|komponert|"
    r"arr|arrangert|arranged|arrangement|bearb|bearbeidet|instr|transcr|satz)\b\.?\s*(?:og|and|&|/|,)?\s*)+"
    r"(?:by|av)?\b\s*[:.]?\s*|^\s*(?:by|av)\b\s*:?\s*", re.I)
# linjer som aldri er en tittel, uansett størrelse
_STRONG_COMP_RE = re.compile(r"^\s*(arr\b|arrangert|arranged|arr\.\s*by|komponert|composed|bearb|instr\b|satz\b|"
                             r"based\s+on|choruses\s+by|basert\s+på|music\s+by|words\s+by|lyrics\b"
                             r")", re.I)
_ARR_KW_RE = re.compile(r"\b(arr|arrangert|arrangement|arranged|bearb|instr|transcr|satz)\b", re.I)
_TEMPO_RE = re.compile(r"\b(allegro|allegretto|andante|andantino|moderato|adagio|largo|lento|presto|vivace|"
                       r"tempo|marcia|maestoso|rubato|grave|swing|bpm)\b|=\s*\d",
                       re.I)
_JUNK_RE = re.compile(r"(©|copyright|\(c\)|www\.|\.com|\.no|printed|all rights|edition|verlag|forlag|publish|commissioned|bestilt av)", re.I)


# Ord som står over notene på vanlige sider, og som ikke er titler
_MUSIC_TERMS = {"allegro", "allegretto", "andante", "andantino", "moderato", "adagio", "largo", "larghetto", "lento",
                "presto", "prestissimo", "vivace", "vivo", "spirito", "brio", "moto", "assai", "troppo", "non",
                "ma", "giocoso", "marziale", "maestoso", "tranquillo", "grazioso", "animato", "deciso",
                "risoluto", "pesante", "leggiero", "leggero", "sostenuto", "tenuto", "marcato", "dolce",
                "tempo", "primo", "secondo", "marcia", "swing", "ballad", "rock", "bpm",
                "con", "molto", "poco", "piu", "più", "meno", "quasi", "sempre", "subito", "rit", "rall",
                "accel", "cresc", "dim", "fine", "coda", "segno", "rubato", "cantabile", "espressivo",
"p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "sf", "sfz", "sffz", "fp", "rf", "rfz", "nf", "cresc",
                "decresc", "dim", "rall", "rit", "ritard", "accel", "solo", "soli", "tutti", "tempo", "a", "open",
                "mute", "cup", "straight", "molto", "poco", "piu", "più", "meno", "legato", "staccato", "fine",
                "coda", "segno", "dc", "ds", "al", "div", "unis", "senza", "con", "sord", "simile", "rubato",
                "cantabile", "dolce", "espr", "espressivo", "marc", "ten", "sim", "opt", "only", "play", "2nd",
                "1st", "time", "x", "rep", "repeat"}


# Sjangerord som står først i titler i korpsbøker: «MARCH—BEYOND THE BLUE», «SONG ARRANGEMENT—YES, JESUS LOVES ME»
GENRE_WORDS = ["march", "festival", "hymn", "tune", "setting", "song", "arrangement", "transcription",
               "selection", "fantasy", "fantasia", "meditation", "carol", "chorus", "prelude", "suite",
               "rhapsody", "variations", "air", "cornet", "solo", "duet", "trio", "quartet", "marsj", "salme",
               "sang", "arrangement", "fantasi", "meditasjon", "julesang"]
# Hele sjangernavn (ett ord alene som «song» er ikke nok – «Song Without Words» er en tittel)
GENRES = ["festival march", "march", "hymn tune arrangement", "hymn setting", "hymn arrangement",
          "song arrangement", "song setting", "carol arrangement", "carol setting", "chorus arrangement",
          "transcription", "selection", "fantasy", "fantasia", "meditation", "prelude", "suite", "rhapsody",
          "air varie", "cornet solo", "marsj", "salmeutsetting", "sangarrangement"]
_GENRE_SET = {tuple(g.split()) for g in GENRES}
_DASH_RE = re.compile(r"\s*[—–―‒‐‑−]+\s*|\s+-+\s*|\s*-+\s+")      # tankestrek, eller bindestrek med mellomrom
_SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "to", "for", "at", "by", "from", "with", "is",
                "i", "og", "på", "av", "til", "for", "med", "fra", "den", "det", "de", "der", "die", "und",
                "dur", "moll"}
# Titler/grader foran navn: «Captain DEAN GOFFIN», «Bandmaster G. C. CRANE»
_RANK_RE = re.compile(r"^\s*(captain|capt|major|colonel|lieut|lieutenant|general|commissioner|brigadier|"
                      r"bandmaster|bandsman|band\s*leader|y\.?\s*p\.?|corps|songster|sergeant|mrs|mr|dr|"
                      r"kaptein|major|oberst|korpsleder|musikkleder)\b", re.I)
# Komponistlinje: STARTER med et komponist-/arrangørord (ikke titler som «Song Arrangement – …»)
_COMP_START_RE = re.compile(r"^\s*(arr|arrangert|arranged|arr\.\s*by|komp|komponist|musikk|music\s+by|musik|"
                            r"words|lyrics|tekst|text|by|av|composed|komponert|bearb|bearbeidet|instr|satz|"
                            r"melodi|melody|based\s+on|choruses\s+by|basert\s+på)\b", re.I)


def _fuzzy_word(w: str, vocab, cutoff: float = 0.78) -> str | None:
    """Rett OCR-feil i kjente ord: «Arrangenient» -> «arrangement», «Allexro» -> «allegro»."""
    lw = re.sub(r"[^a-zæøåäöü]", "", w.lower())
    if len(lw) < 4:
        return lw if lw in vocab else None
    if lw in vocab:
        return lw
    m = difflib.get_close_matches(lw, vocab, n=1, cutoff=cutoff)
    return m[0] if m else None


# tempoord som ofte feilleses («Allexro», «Moderafo») – bare lange, typiske ord, så ekte titler ikke rammes
_TERM_VOCAB = ["allegro", "allegretto", "andante", "andantino", "moderato", "maestoso", "sostenuto",
               "espressivo", "cantabile", "spirito", "marziale", "tranquillo", "grazioso", "animato",
               "giocoso", "giojoso", "larghetto", "leggiero", "risoluto"]


def _is_real_title(t: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-zÆØÅæøåÄÖÜäöüß]+", t)]
    real = [w for w in words if len(w) >= 3 and w.lower() not in _MUSIC_TERMS
            and not (len(w) >= 5 and _fuzzy_word(w, _TERM_VOCAB, 0.8))]
    if not real:
        return False
    # bare sjangerord («MARCH», «SELECTION», «HYMN SETTING») er ikke en tittel
    long_ = words
    if long_ and all(_fuzzy_word(w, GENRE_WORDS) for w in long_) and \
            (len(long_) == 1 or tuple(_fuzzy_word(w, GENRE_WORDS) for w in long_) in _GENRE_SET):
        return False
    return sum(len(w) for w in real) >= 4


def _genre_prefix(words: list[str]) -> int:
    """Hvor mange av de første ordene som er et sjangernavn (0 = ingen)."""
    fixed = []
    for w in words[:3]:
        f = _fuzzy_word(w, GENRE_WORDS)
        if not f:
            break
        fixed.append(f)
    for n in range(len(fixed), 0, -1):
        if tuple(fixed[:n]) in _GENRE_SET:
            return n
    return 0


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" .,:;-_|~—–‐‑−")
    if s.count("'") % 2 or s.count('"') % 2:      # løse anførselstegn i kanten, men ikke «'Alceste'»
        s = s.strip("'\"")
    s = s.replace(" ,", ",")
    return s


def _cap(w: str, first: bool) -> str:
    """Ett ord i tittel-stil. Ord med blanding av store og små bokstaver (McDONALD, iPhone) beholdes."""
    core = re.sub(r"[^A-Za-zÆØÅæøåÄÖÜäöüßÉéÈèÁáÓóÜü]", "", w)
    if not core:
        return w
    if len(core) >= 2 and core.isupper() and re.fullmatch(r"[IVX]+", core) and re.fullmatch(r"[IVX]+[.:,)]?", w):
        return w                                 # romertall: «II. Andante»
    if not (core.isupper() or core.islower()) and not core[0].isupper():
        return w
    if w.startswith("(") and core.islower():
        return w                                 # «(continued)»
    if not core.isupper() and not core.islower():
        return w if not first or w[0].isupper() else w[:1].upper() + w[1:]
    lw = w.lower()
    if not first and core.lower() in _SMALL_WORDS:
        return lw
    # store bokstav etter bindestrek og apostrof i starten: «mid-winter» -> «Mid-Winter», «'alceste» -> «'Alceste»,
    # «o'brien» -> «O'Brien», men ikke «you'll», «king's», «i'm»
    def up(m):
        sep, k = m.group(1), m.start(2)
        if sep in "'’‘" and sep and not (k == 1 or (k == 2 and len(re.match(r"[\wæøåéü]*", lw[k:]).group(0)) >= 3)):
            return m.group(0)
        return sep + m.group(2).upper()
    return re.sub(r"(^|[-'’‘\"(])([a-zæøåäöüéèáó])", up, lw)


def _title_case(s: str) -> str:
    return " ".join(_cap(w, i == 0) for i, w in enumerate(s.split()))


def _nice_case(s: str) -> str:
    """«SOLO Bb CORNETS» -> «Solo Bb Cornets». Rører ikke tekst med vanlig skrift."""
    letters = [c for c in s if c.isalpha()]
    if len(letters) <= 3 or sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return s
    small = {"i", "og", "på", "av", "til", "for", "the", "of", "and", "in", "a", "de", "der", "die", "und"}
    out = []
    for i, w in enumerate(s.split()):
        if w.isupper() and len(w) > 1 or (w.isupper() and w.lower() in small):
            lw = w.lower()
            w = lw if (i and lw in small) else lw[:1].upper() + lw[1:]
        out.append(w)
    return " ".join(out)


_PART_PREFIX_RE = re.compile(r"^((\d(st|nd|rd|th)\s+)?(" + _INSTR_RE.pattern + r")(\s+[A-G](b|♭|>|}|\])?(?=\s))?)\s+",
                             re.I)


def _has_part_marker(m) -> bool:
    """«2nd CORNET …» eller «CORNET Bb …» – ikke «TRUMPET TUNE» (der instrumentet er en del av tittelen)."""
    return bool(m.group(2)) or bool(re.search(r"\s[A-G](b|♭|>|}|\])?$", m.group(1)))


def nice_title(raw: str) -> str:
    """Pen tittel: «MARCH—BEYOND THE BLUE» -> «March - Beyond the Blue»,
    «Song Arrangenient - and Hear!» -> «Song Arrangement - and Hear!», «No. 40 SONG …» -> «Song …»."""
    t = _join_spaced(_clean(raw))
    t = re.sub(r"^(no\.?|nr\.?)\s*\d+[a-z]?\b\.?\s*", "", t, flags=re.I)      # sidenummer i boka
    t = re.sub(r"^\d{1,3}\.\s+(?=[A-ZÆØÅ])", "", t)
    # stemmen foran tittelen («2nd CORNET Bb TRANSCRIPTION—…»)
    m = _PART_PREFIX_RE.match(t)
    if m and _has_part_marker(m) and len(t) > m.end() + 3:
        t = t[m.end():]
    if not t:
        return ""
    letters = [c for c in re.sub(r"\([^)]*\)", "", t) if c.isalpha()]
    caps = bool(letters) and sum(c.isupper() for c in letters) >= 0.8 * len(letters)   # STORE BOKSTAVER
    parts = [p for p in _DASH_RE.split(t) if p.strip(" -")]
    words = t.split()
    # sjanger og navn: «MARCH-JOYFUL HEART» / «MARCH STAND UP FOR JESUS» (streken ble ikke lest)
    if len(parts) == 1:
        first = re.split(r"-", words[0], maxsplit=1)
        if len(first) == 2 and first[1] and _genre_prefix([first[0]]):
            parts = [first[0], " ".join([first[1]] + words[1:])]
        else:
            n = _genre_prefix(words)
            if n and len(words) > n and words[n].lower() not in _SMALL_WORDS | {"from", "&", "no", "no.", "nr"} \
                    and caps:
                parts = [" ".join(words[:n]), " ".join(words[n:])]
    out = []
    for i, p in enumerate(parts):
        p = _clean(p)
        ws = p.split()
        if i == 0:
            n = _genre_prefix(ws)
            if n:      # rett stavefeil i sjangeren
                ws = [(_fuzzy_word(w, GENRE_WORDS) or w).capitalize() for w in ws[:n]] + ws[n:]
        p = " ".join(ws)
        if caps:
            p = _title_case(p)                  # «BEYOND THE BLUE» -> «Beyond the Blue»
        elif p[:1].islower():
            p = p[:1].upper() + p[1:]           # vanlig skrift beholdes («Marsj i C»)
        out.append(p)
    return " - ".join(o for o in out if o)


_YEARS_RE = re.compile(r"\(\s*(?:ca\.?\s*)?\d{3,4}\s*[-–—]?\s*(?:\d{2,4})?\s*\)")


def _letter_ratio(s: str) -> float:
    return sum(c.isalpha() for c in s) / max(1, len(s.replace(" ", "")))


_TALL = re.compile(r"[A-ZÆØÅÄÖÜ0-9bdfhkltåäöü]")


def _tall(text: str) -> bool:
    """Har ordet store bokstaver/oppstrek? Ord som «er», «a», «ei» er lavere og sier lite om skriftstørrelsen."""
    return bool(_TALL.search(text))


def _word_height(words) -> float:
    hs = sorted(q[3] for q in words if _tall(q[4])) or sorted(q[3] for q in words if re.search(r"\w", q[4])) \
        or sorted(q[3] for q in words)
    return float(hs[len(hs) // 2])


def _split_line(ln: Line) -> list[Line]:
    """Del en OCR-linje der det er stort mellomrom, eller tydelig hopp i skriftstørrelse med luft imellom
    («Et barn er født i Betlehem   Ludvig Lindemann» -> tittel + komponist). Små ord som «er» og «a» er lavere
    enn resten av ordene, så de sammenlignes ikke på høyde."""
    ws = sorted(ln.words or [], key=lambda q: q[0])
    if len(ws) < 2:
        return [ln]
    segs, cur = [], [ws[0]]
    for q in ws[1:]:
        p = cur[-1]
        gap = q[0] - (p[0] + p[2])
        hm = _word_height(cur)
        jump = False
        if _tall(q[4]) and any(_tall(c[4]) for c in cur):
            ratio = max(q[3], hm) / max(1.0, min(q[3], hm))
            jump = ratio > 1.25 and gap > 1.0 * min(q[3], hm)   # vanlig ordmellomrom er under halve høyden
        if gap > 1.8 * hm or jump:
            segs.append(cur)
            cur = [q]
        else:
            cur.append(q)
    segs.append(cur)
    if len(segs) == 1:
        return [ln]
    out = []
    for sg in segs:
        x0 = min(q[0] for q in sg)
        x1 = max(q[0] + q[2] for q in sg)
        out.append(Line(" ".join(q[4] for q in sg), x0, min(q[1] for q in sg), x1 - x0, _word_height(sg), sg))
    return out


_SPACED_RE = re.compile(r"(?<![\wæøåÆØÅéÉ.])([B-DF-HJ-NP-TV-XZbcdf-hj-np-tv-xz]{1,2}) ([a-zæøåäöü][\wæøåäöüé]+)")
# ord som står alene etter en bokstav: tonearter («B minor», «F dur», «Bb major») og småord («B the»)
_NO_JOIN = {"dur", "moll", "major", "minor", "flat", "sharp", "maj", "min", "and", "the", "of", "og", "for",
            "in", "on", "to", "at", "by", "av", "på", "til", "med", "fra", "den", "det", "de", "der", "die", "und",
            "is", "an", "or", "eller", "solo"}


def _join_spaced(t: str) -> str:
    """OCR deler noen ganger et ord etter første bokstav: «W hen You W ish» -> «When You Wish», «Bj ørn» -> «Bjørn».
    Bare biter uten vokal (ingen ekte ord) slås sammen, så «i», «å», «A» o.l. står i fred – og ikke tonearter
    («Mass in B minor», «Marsj i F dur»)."""
    def sub(m):
        if m.group(2).lower() in _NO_JOIN:
            return m.group(0)
        return m.group(1) + m.group(2)
    return _SPACED_RE.sub(sub, t)


def _cut_line(ln: Line, n_words: int) -> list[Line]:
    """Del en linje etter de n første ordene (bruker ordboksene hvis de finnes, ellers tegnandel)."""
    ws = ln.text.split()
    a, b = " ".join(ws[:n_words]), " ".join(ws[n_words:])
    if ln.words and len(ln.words) == len(ws):
        w1, w2 = ln.words[:n_words], ln.words[n_words:]
        mk = lambda sg: Line(" ".join(q[4] for q in sg), min(q[0] for q in sg), min(q[1] for q in sg),
                             max(q[0] + q[2] for q in sg) - min(q[0] for q in sg),
                             sorted(q[3] for q in sg)[len(sg) // 2], sg)
        return [mk(w1), mk(w2)]
    k = len(a) / max(1, len(ln.text))
    return [Line(a, ln.x, ln.y, ln.w * k, ln.h), Line(b, ln.x + ln.w * (k + 0.02), ln.y, ln.w * (1 - k), ln.h)]


MAX_LINES = 150   # et tittelfelt har sjelden over 50 linjer; støy (helt svart side o.l.) kan gi tusenvis


def _prepare(lines: list[Line]) -> list[Line]:
    if len(lines) > MAX_LINES:
        lines = sorted(lines, key=lambda l: -l.h)[:MAX_LINES]
    out = []
    for ln in lines:
        for s in _split_line(ln):
            # «2nd CORNET Bb TRANSCRIPTION—HERALDS OF SALVATION» -> stemme + tittel
            m = _PART_PREFIX_RE.match(s.text)
            rest = s.text[m.end():] if m else ""
            if m and _has_part_marker(m) and sum(c.isalpha() for c in rest) >= 6 and not re.match(r"^(solo|i|ii|iii|1|2|3)\b", rest, re.I):
                out.extend(_cut_line(s, len(m.group(1).split())))
            else:
                out.append(s)
    # biter av samme linje som OCR-en har delt («Ludvig» + «Lindemann»): samme rad, samme størrelse, tett
    out.sort(key=lambda l: (l.y, l.x))
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(out):
            for j, b in enumerate(out):
                if i == j or not re.search(r"\w", a.text) or not re.search(r"\w", b.text):
                    continue
                gap = b.x - (a.x + a.w)
                hh, hl = max(a.h, b.h), min(a.h, b.h)
                ov = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                if 0 <= gap < 0.8 * hh and hh < 1.3 * hl and ov > 0.5 * hl:
                    ws = (a.words or []) + (b.words or [])
                    out[i] = Line(a.text + " " + b.text, a.x, min(a.y, b.y), b.x + b.w - a.x, hh,
                                  ws if a.words and b.words else None)
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    # «arr.» + «ERIK SILFVERBERG», «BASED ON CHORUSES BY» + «RICHARD SLATER» på samme rad -> én komponistlinje
    out.sort(key=lambda l: (l.y, l.x))
    merged: dict[int, Line] = {}
    gone: set[int] = set()
    for i, a in enumerate(out):
        if i in gone or not (_COMP_START_RE.search(a.text) or re.search(r"\b(by|av|arr)\.?\s*$", a.text, re.I)):
            continue
        for j, b in enumerate(out):
            if j == i or j in gone or j in merged:
                continue
            hh = max(a.h, b.h)
            if abs((b.y + b.h / 2) - (a.y + a.h / 2)) < hh and 0 <= b.x - (a.x + a.w) < 2 * hh:
                merged[i] = Line(a.text + " " + b.text, a.x, min(a.y, b.y), b.x + b.w - a.x, hh)
                gone.add(j)
                break
    return [merged.get(i, ln) for i, ln in enumerate(out) if i not in gone]


def parse(lines: list[Line], width: float) -> Suggestion:
    lines = _prepare(lines)
    dashes = [ln for ln in lines if ln.text.strip() and not re.search(r"\w", ln.text)]
    lines = [ln for ln in lines if ln.text and sum(c.isalpha() for c in ln.text) >= 2
             and _letter_ratio(_YEARS_RE.sub("", ln.text)) > 0.55 and not _JUNK_RE.search(ln.text)]
    if not lines:
        return Suggestion()
    sug = Suggestion()
    used: set[int] = set()

    # Stemme: linje med instrumentnavn (ofte øverst til venstre)
    part_cands = []
    for i, ln in enumerate(lines):
        m = _INSTR_RE.search(ln.text) or _VOICE_RE.search(ln.text)
        if m and len(ln.text.split()) <= 7 and not _COMP_START_RE.search(ln.text) and not _genre_prefix(ln.text.split()):
            score = (2.0 if ln.cx < 0.4 * width else 1.0) - 0.1 * len(ln.text.split())
            part_cands.append((score, i))
    if part_cands:
        _, i = max(part_cands)
        part = lines[i].text
        pl = lines[i]
        if not re.match(r"^\d", part):      # «2nd» lest som egen linje foran «CORNET Bb»
            for j, o in enumerate(lines):
                if j != i and re.fullmatch(r"\d(st|nd|rd|th)", o.text.strip(), re.I) and \
                        abs((o.y + o.h / 2) - (pl.y + pl.h / 2)) < pl.h and 0 <= pl.x - (o.x + o.w) < 2 * pl.h:
                    part = o.text.strip() + " " + part
                    used.add(j)
                    break
        part = re.sub(r"\b([A-G])\s?[}\]|♭>]", r"\1b", part)  # «B}» / «B♭» / «B>» -> «Bb»
        sug.part = _nice_case(_clean(part))
        used.add(i)

    # Tittel: størst skrift, gjerne midtstilt, gjerne med sjangerord først («MARCH—…»)
    max_h = max(l.h for l in lines)

    def dominance(i, ln) -> float:
        """Hvor mye større enn all annen tekst linja er (0 = ikke midtstilt / ingen annen tekst å sammenligne med)."""
        others = [o.h for j, o in enumerate(lines) if j != i and sum(c.isalpha() for c in o.text) >= 2]
        if not others or abs(ln.cx - width / 2) >= 0.2 * width:
            return 0.0
        return ln.h / max(1.0, max(others))

    title_cands = []
    loose = []      # klart største linje som ellers ville blitt avvist («Largo», «Air», «Jul»)
    for i, ln in enumerate(lines):
        t = ln.text
        if i in used:
            continue
        if _COMP_START_RE.search(t) or _RANK_RE.search(t):
            # «By the Rivers of Babylon», «Mr. Sandman», «Musikk i fjellet»: en tittel kan starte med et
            # komponist- eller gradsord – men da er den den klart største, midtstilte linja og har ikke «:»
            if not (dominance(i, ln) >= 1.25 and ":" not in t and not _STRONG_COMP_RE.match(t)):
                continue
        if re.match(r"^\s*\(?\s*(op|opus)\b", t, re.I) or re.match(r"^\s*(no|nr)\.?\s*\d+\s*$", t, re.I):
            continue    # «(Op. 102, No. 6)», «No. 47»
        if _TEMPO_RE.search(t) and ln.h < 0.7 * max_h:
            continue  # tempoangivelse i liten skrift
        if ln.h < 0.55 * max_h:
            continue  # småtekst (rester av «Allegro», fingersetting o.l.)
        if not _is_real_title(nice_title(t) or t):
            nt = nice_title(t)
            if dominance(i, ln) >= 1.4 and sum(c.isalpha() for c in nt) >= 2 and len(nt.split()) <= 4:
                loose.append((ln.h, i))
            continue  # dynamikk, tempo («Allegro con spirito»), «solo», øvingsbokstaver o.l.
        centered = abs(ln.cx - width / 2) < 0.2 * width
        if ln.x > 0.55 * width and ln.x + ln.w > 0.9 * width and not centered:
            continue  # høyrestilt = komponist/arrangør, ikke tittel
        score = ln.h * (1.25 if centered else 1.0)
        words = nice_title(t).split()
        if _genre_prefix(words) or _DASH_RE.search(t):
            score *= 1.35
        if ln.x > 0.55 * width and len(t.split()) <= 3 and not centered:
            score *= 0.7      # kort navn ute til høyre = typisk komponisten
        title_cands.append((score, i))
    if not title_cands and loose:
        title_cands = [max(loose)]
        sug.title_ok = True
    if title_cands:
        _, bi = max(title_cands)
        best = lines[bi]
        parts = [best]
        cand_ids = {j for _, j in title_cands}
        for j, o in enumerate(lines):
            if j == bi or j in used:
                continue
            same_size = 0.8 * best.h < o.h < 1.25 * best.h
            # tittel over to linjer med samme skriftstørrelse
            below = j in cand_ids and o.h > 0.85 * best.h and 0 < abs(o.y - best.y) < 1.8 * best.h \
                and abs(o.cx - best.cx) < 0.25 * width
            # tittelen delt i biter på samme rad («MARCH—STAND» + «UP FOR JESUS»)
            lo, hi = min(o.h, best.h), max(o.h, best.h)
            gap = min(abs(o.x - (best.x + best.w)), abs(best.x - (o.x + o.w)))
            row = abs((o.y + o.h / 2) - (best.y + best.h / 2)) < 0.6 * hi and \
                (gap < 3.5 * lo if same_size else gap < 1.5 * lo and hi < 2.2 * lo) and \
                not _COMP_START_RE.search(o.text) and not _RANK_RE.search(o.text) and \
                not (o.x > 0.6 * width and gap > 1.5 * lo)
            if below or row:
                parts.append(o)
        parts.sort(key=lambda l: (round(l.y / max(1.0, best.h)), l.x))
        txt = " ".join(p.text for p in parts)
        ws0 = sorted(parts[0].words or [], key=lambda q: q[0])
        if len(ws0) >= 2 and re.fullmatch(r"\d{1,3}\.?", ws0[0][4]) and txt.startswith(ws0[0][4]) and \
                ws0[1][0] - (ws0[0][0] + ws0[0][2]) > 0.9 * parts[0].h:
            txt = txt[len(ws0[0][4]):].lstrip()
        # sjangeren på egen linje rett over («SELECTION» / «CHORUS TIME No. 1»)
        top = parts[0]
        for j, o in enumerate(lines):
            ws = o.text.split()
            if j in used or o in parts or not ws or _genre_prefix(ws) != len(ws):
                continue
            if 0 < top.y - o.y < 2.5 * max(o.h, top.h) and abs(o.cx - top.cx) < 0.3 * width \
                    and 0.7 * top.h < o.h < 1.8 * top.h \
                    and not _genre_prefix(txt.split()):
                txt = o.text + " - " + txt
                parts.append(o)
                break
        # en tankestrek som ble lest som egen «linje» mellom sjanger og navn
        if not _DASH_RE.search(txt) and any(
                abs((d.y + d.h / 2) - (best.y + best.h / 2)) < best.h and best.x < d.x < best.x + best.w + best.h
                for d in dashes):
            n = _genre_prefix(txt.split())
            if n:
                ws = txt.split()
                txt = " ".join(ws[:n]) + " - " + " ".join(ws[n:])
        sug.title = nice_title(txt)
        used.update(lines.index(p) for p in parts)

    # Komponist/arrangør: nøkkelord, titulert navn, eller kort linje til høyre
    comp_cands = []
    for i, ln in enumerate(lines):
        if i in used or _INSTR_RE.search(ln.text) and not _COMP_START_RE.search(ln.text):
            continue
        t = ln.text
        if re.match(r"^\s*\(?\s*(op|opus|no|nr)\b", t, re.I) or _genre_prefix(nice_title(t).split()):
            continue
        right = ln.x > 0.5 * width
        short = len(t.split()) <= 6 and not _TEMPO_RE.search(t)
        if _COMP_KW_RE.search(t):
            score = 2.0          # «Musikk: …», «Music by …»
        elif _RANK_RE.search(t):
            score = 1.6          # «Captain DEAN GOFFIN»
        elif _ARR_KW_RE.search(t):
            score = 0.5          # arrangør – bare hvis ingen komponist står der
        elif right and short and _is_real_title(t):
            score = 0.6          # kort navn ute til høyre (typisk komponisten)
        else:
            continue
        comp_cands.append((score + (1 if right else 0), -ln.y, i))
    for _, _, i in sorted(comp_cands, reverse=True):
        txt = lines[i].text
        m = _COMP_PREFIX_RE.match(txt)
        if m and m.end() < len(txt.rstrip()):
            txt = txt[m.end():]
        sug.composer = _join_spaced(_clean(txt))
        break
    return sug


def read_header(img: np.ndarray, dpi: int) -> Suggestion:
    if img is None or img.ndim < 2 or min(img.shape[:2]) < proc.MIN_SIDE or not dpi or dpi <= 0:
        return Suggestion()
    img, _ = proc.deskew(img, dpi)  # notelinjene må ligge vannrett for å finne første notesystem
    region, header_mm, staff_mm = _header(img, dpi)
    if region.size == 0:
        return Suggestion()
    lines, engine = ocr_lines(region)
    if lines is None:
        return Suggestion(engine="", header_mm=header_mm, staff_mm=staff_mm)
    sug = parse(lines, region.shape[1])
    sug.header_mm = header_mm
    sug.staff_mm = staff_mm
    sug.engine = engine
    if sug.title:
        key = re.sub(r"\W", "", sug.title.lower())
        best = [l for l in (s for ln in lines for s in _split_line(ln))
                if len(re.sub(r"\W", "", l.text)) >= 3 and re.sub(r"\W", "", nice_title(l.text).lower())
                and re.sub(r"\W", "", nice_title(l.text).lower()) in key]
        if best:
            sug.title_mm = max(l.h for l in best) / dpi * 25.4
    return sug
