"""NoteScanner – skann mange noter i ett strekk, la appen dele dem opp, sjekk og lagre som PDF-er."""
from __future__ import annotations

import itertools
import json
import os
import queue
import re
import shutil
import sys
import difflib
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QLockFile, QObject, QPoint, QRect, QRectF, QSettings, QSize, Qt, \
    QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QDesktopServices, QFontMetrics, QImage, QKeySequence, QPainter, \
    QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, \
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, \
    QProgressDialog, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QStatusBar, QStyle, \
    QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractSpinBox, \
    QListWidget, QListWidgetItem

from . import __version__, bugreport, icons, ocr, paths, scanner, updater
from . import processing as proc
from .pdfout import save_pdf
from .theme import C, font, palette, stylesheet

APP_VERSION = __version__
APP_ID = "NoteScanner-single-instance"
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
NAME_PATTERNS = ["Tittel - Stemme", "Tittel - Komponist - Stemme", "Komponist - Tittel - Stemme",
                 "Stemme - Tittel"]
FIELDS = ("title", "part", "composer")
DECODED_PAGES = 4        # så mange sider holdes utpakket i minnet samtidig
HISTORY_STEPS = 8        # angresteg per side (en beskjæring av en forbedret side bruker to)
# Skanneområde målt fra hjørnemerket: (bredde, lengde) i mm. Lengden er hvor langt skannerhodet går.
AREAS = [("full", "Hele glasset", None),
         ("a5l", "A5 liggende – 210 × 148 mm (raskest)", (210, 148)),
         ("a5p", "A5 stående – 148 × 210 mm", (148, 210)),
         ("b5p", "B5 stående – 176 × 250 mm", (176, 250)),
         ("a4", "A4 – 210 × 297 mm", (210, 297)),
         ("custom", "Egendefinert …", None)]
ROTATIONS = [("0", "Ikke snu"), ("cw", "Snu 90° med klokka"), ("ccw", "Snu 90° mot klokka"), ("180", "Snu 180°")]
SCAN_TIMEOUT_S = 180            # så lenge venter appen på skanneren før den gir opp
ARCHIVE_DAYS = 14               # lagrede dokumenter ligger så lenge under «Lagret nylig»
SCAN_IDLE = threading.Event()   # nullstilt mens skanneren jobber – da venter behandling og tekstlesing
SCAN_IDLE.set()
ROLE_KEY = Qt.UserRole
ROLE_INFO = Qt.UserRole + 1
_ids = itertools.count(1)


# ================================================================ data

@dataclass(eq=False)
class DocMeta:
    """Opplysninger om ett dokument (ett stykke, én stemme eller én bok). Ligger på dokumentets første side."""
    title: str = ""
    part: str = ""
    composer: str = ""
    edited: set = field(default_factory=set)   # felt brukeren har skrevet selv – overskrives aldri
    saved_path: str = ""
    rev: int = 1           # øker ved hver endring
    saved_rev: int = 0     # rev da dokumentet sist ble lagret

    @property
    def saved(self) -> bool:
        return bool(self.saved_path) and self.rev == self.saved_rev

    def bump(self):
        self.rev += 1

    def fill_from(self, sug) -> bool:
        changed = False
        if sug is None:
            return False
        for f in FIELDS:
            v = getattr(sug, f, "")
            if v and f not in self.edited and getattr(self, f) != v:
                setattr(self, f, v)
                changed = True
        return changed

    def to_json(self):
        return {"title": self.title, "part": self.part, "composer": self.composer, "edited": sorted(self.edited),
                "saved_path": self.saved_path, "rev": self.rev, "saved_rev": self.saved_rev}

    @classmethod
    def from_json(cls, d):
        m = cls(d.get("title", ""), d.get("part", ""), d.get("composer", ""), set(d.get("edited", [])),
                d.get("saved_path", ""))
        if "rev" in d:
            m.rev, m.saved_rev = int(d["rev"]), int(d.get("saved_rev", 0))
        else:   # økt fra versjon 1
            m.rev, m.saved_rev = 1, (1 if m.saved_path and not d.get("changed", True) else 0)
        return m


_DECODED: "OrderedDict[int, tuple[int, np.ndarray]]" = OrderedDict()


def _cache_put(pid: int, ver: int, arr: np.ndarray):
    _DECODED[pid] = (ver, arr)
    _DECODED.move_to_end(pid)
    while len(_DECODED) > DECODED_PAGES:
        _DECODED.popitem(last=False)


@dataclass(eq=False)
class Page:
    """Én skannet side. Bildet ligger komprimert (PNG) i minnet og pakkes ut bare når det trengs,
    så 100+ sider tar et par hundre MB i stedet for flere GB."""
    png: bytes
    dpi: int
    thumb_arr: np.ndarray | None = None
    history: list = field(default_factory=list)   # [(bilde, bw, last_op, join, orig)] – eldste først
    last_op: str = ""
    bw: bool = False             # gjøres om til ren svart/hvitt (600 dpi) i PDF-en
    id: int = field(default_factory=lambda: next(_ids))
    meta: DocMeta | None = None  # satt = siden starter et nytt dokument
    sug: object = None           # OCR-resultat (ocr.Suggestion)
    ocr_state: str = "pending"   # pending | done | off
    manual: bool = False         # brukeren har bestemt grupperingen – tekstlesingen skal ikke flytte den
    section: str | None = None   # sangnavn/bokmerke du har skrevet selv: None = fra tekstlesingen, "" = ingen
    ver: int = 0
    thumb_pm: QPixmap | None = None
    orig: bytes | None = None    # hele skannet (rettet opp, ikke beskåret) – for «Tilbakestill beskjæring»
    hidden_meta: DocMeta | None = None   # dokumentet siden startet før «Én bok» (tilbake ved «Auto»)

    @property
    def img(self) -> np.ndarray:
        hit = _DECODED.get(self.id)
        if hit is not None and hit[0] == self.ver:
            _DECODED.move_to_end(self.id)
            return hit[1]
        arr = proc.decode(self.png)
        _cache_put(self.id, self.ver, arr)
        return arr

    def _set(self, arr: np.ndarray):
        self.png = proc.encode_png(arr)
        self.ver += 1
        _cache_put(self.id, self.ver, arr)
        self.thumb_arr = proc.thumbnail(arr)
        self.thumb_pm = None

    def push(self, arr: np.ndarray, join: bool = False):
        """Nytt angresteg. join=True: hører sammen med steget før (Ctrl+Z angrer begge på én gang)."""
        self.history.append((self.png, self.bw, self.last_op, join, self.orig))
        del self.history[:-HISTORY_STEPS]
        self.last_op = ""
        self._set(arr)

    def replace(self, arr: np.ndarray):
        """Bytt bildet uten nytt angresteg (brukes når forbedringen justeres)."""
        self._set(arr)

    def before_enhance(self) -> np.ndarray:
        if self.last_op == "enhance" and self.history:
            return proc.decode(self.history[-1][0])
        return self.img

    def undo(self) -> bool:
        if not self.history:
            return False
        def restore(e):
            self.png, self.bw, self.last_op = e[:3]
            if len(e) > 4:
                self.orig = e[4]
        e = self.history.pop()
        restore(e)
        if len(e) > 3 and e[3] and self.history:     # beskjæring/tilbakestilling = to steg, angres samlet
            restore(self.history.pop())
        if self.last_op == "enhance" and not self.history:
            self.last_op = ""          # råbildet før forbedringen finnes ikke lenger – ikke forbedre to ganger
        self.ver += 1
        self.thumb_arr = proc.thumbnail(self.img)
        self.thumb_pm = None
        return True

    def ocr_png(self) -> bytes:
        """Bildet tekstlesingen skal bruke: råskannet før forbedring (tynn tekst leses best der)."""
        if self.last_op == "enhance" and self.history:
            return self.history[-1][0]
        return self.png

    def thumb(self) -> QPixmap:
        if self.thumb_pm is None:
            a = self.thumb_arr if self.thumb_arr is not None else proc.thumbnail(self.img)
            self.thumb_pm = QPixmap.fromImage(to_qimage(a))
        return self.thumb_pm


def to_qimage(img: np.ndarray) -> QImage:
    if img.ndim == 2:
        h, w = img.shape
        return QImage(np.ascontiguousarray(img).data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w, _ = img.shape
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return QImage(np.ascontiguousarray(rgb).data, w, h, 3 * w, QImage.Format_RGB888).copy()


def file_dpi(path: str) -> int | None:
    """Oppløsningen skannerprogrammet har skrevet i bildefila, eller None. 72/96 dpi er bare standardverdier fra
    kamera og skjermbilder, så bare 150–2400 regnes som en ekte skanneoppløsning."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            d = im.info.get("dpi")
        if d:
            x, y = float(d[0]), float(d[1])
            if 150 <= x <= 2400 and abs(x - y) < 1:
                return int(round(x))
    except Exception:  # noqa: BLE001
        pass
    return None


def read_image(path: str):
    data = np.fromfile(path, np.uint8)  # håndterer æøå i filstier på Windows
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.dtype != np.uint8:
        img = cv2.convertScaleAbs(img, alpha=255.0 / max(1, img.max()))
    if img.ndim == 3 and img.shape[2] == 4:
        # Gjennomsiktig bakgrunn (eksport fra noteprogram): legg bildet på hvitt papir
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * a + 255.0 * (1.0 - a)).astype(np.uint8)
    return img


def image_dpi(path: str, default: int) -> int:
    try:
        from PIL import Image
        with Image.open(path) as im:
            d = im.info.get("dpi")
            # Mange programmer skriver 72 eller 96 dpi uansett. Stoler vi på det, blir PDF-siden enorm.
            if d and 150 <= d[0] <= 1200:
                return int(round(d[0]))
    except Exception:  # noqa: BLE001
        pass
    return default


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_NAME = 150     # tegn – Windows tåler 255 per navn og 260 i hele stien


def safe_name(s: str, fallback: str = "Noter") -> str:
    """Gyldig filnavn på Windows: ingen ulovlige tegn eller styretegn, ikke CON/NUL/COM1 o.l., ikke for langt."""
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    if len(s) > MAX_NAME:
        s = s[:MAX_NAME].rstrip(" .-–")
    if not s.strip(" ._-"):
        s = fallback
    if s.split(".")[0].strip().lower() in _RESERVED:
        s = "_" + s
    return s


_CONT_RE = re.compile(r"\b(forts(ettelse|\.)?|cont(inued|\.)?|fortsetzung|suite)\b|\(\s*\d\s*\)", re.I)


def is_continuation(title: str) -> bool:
    """«Vi tenner våre lykter (forts.)» o.l."""
    return bool(title and _CONT_RE.search(title))


def same_title(a: str, b: str) -> bool:
    """Er dette samme sang? Tåler små lesefeil og «(forts.)»."""
    def norm(t):
        return re.sub(r"[^a-zæøåäöü0-9]", "", _CONT_RE.sub("", t or "").lower())
    na, nb = norm(a), norm(b)
    if len(na) < 3 or len(nb) < 3:
        return False
    return na == nb or na.startswith(nb) or nb.startswith(na) or \
        difflib.SequenceMatcher(None, na, nb).ratio() >= 0.85


def same_song(title: str, part: str, cur_title: str, cur_part: str) -> bool:
    """Hører en side med denne tittelen til sangen/stemmen før? Samme tittel og annen stemme = nytt dokument
    (du skanner gjerne flere stemmer av samme stykke etter hverandre)."""
    if is_continuation(title):
        return True
    if not same_title(title, cur_title):
        return False
    if not part:
        return True   # fortsettelsessider har ofte ikke stemmen øverst

    def norm(t):     # stemmer må være helt like – «Kornett 1» og «Kornett 2» er ikke samme stemme
        return re.sub(r"[^a-zæøåäöü0-9]", "", (t or "").lower())
    return norm(part) == norm(cur_part)


def process_scan(img: np.ndarray, dpi: int, o: dict) -> dict:
    """Alt som skjer med en ny side før den havner i lista. Kjøres i bakgrunnen."""
    if o["mode"] != "color" and img.ndim == 3:
        img = proc.to_gray(img)       # gråtone tar en tredjedel av plassen
    rot = o.get("rotate", "0")
    if rot in ("cw", "ccw"):
        img = proc.rotate90(img, rot == "cw")
    elif rot == "180":
        img = cv2.rotate(img, cv2.ROTATE_180)
    if o["skip_blank"] and proc.is_blank(img, dpi):
        return {"skipped": True}
    notes = []
    if o["deskew"]:
        img, ang = proc.deskew(img, dpi)
        if abs(ang) >= 0.15:
            notes.append(f"rettet opp {ang:+.1f}°")
    parts = [img]
    if o["crop"] and o.get("split_songs", True):
        cuts = proc.song_splits(img, dpi)
        if cuts:
            ys = [0] + cuts + [img.shape[0]]
            parts = [img[a:b] for a, b in zip(ys[:-1], ys[1:])]
            notes.append(f"delt i {len(parts)} sanger")
    results = [r for r in (_finish_part(part, dpi, o, notes, len(parts) > 1) for part in parts) if r is not None]
    if not results:
        return {"skipped": True}
    first = results[0]
    first["extra"] = results[1:]       # flere sanger på samme bokside -> én side i lista per sang
    return first


def _finish_part(img: np.ndarray, dpi: int, o: dict, notes: list, drop_blank: bool = False) -> dict | None:
    notes = list(notes)
    inner = None
    orig = proc.encode_jpg(img, 92) if o["crop"] else None   # for å kunne tilbakestille beskjæringen
    if o["crop"]:
        img, bb, inner = proc.auto_crop_inner(img, dpi, o["margin"])
        if bb:
            notes.append("beskåret")
        elif drop_blank:
            return None
    if o["despeckle"]:
        img = proc.despeckle(img, dpi)
    res = {"skipped": False, "notes": notes, "history": [], "bw": False, "last_op": "", "orig": orig}
    if o["enhance"] or o["mode"] == "bw":
        out = proc.enhance(img, dpi, o["mode"], o["strength"])
        if inner is not None and o["margin"] > 0:
            out = proc.clean_outside(out, inner, 255)            # helt hvit marg rundt notene
        res["history"] = [(proc.encode_jpg(img, 95), False, "")]   # råskannet, for angre og ny justering
        res["bw"] = o["mode"] == "bw"
        res["last_op"] = "enhance"
    else:
        out = proc.clean_outside(img, inner) if inner is not None and o["margin"] > 0 else img
    res.update(png=proc.encode_png(out), arr=out, thumb=proc.thumbnail(out))
    return res


# ================================================================ små byggeklosser til grensesnittet

def card(title: str | None = None, right: QWidget | None = None, margins=(16, 14, 16, 16)):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(10)
    if title:
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(title)
        lbl.setObjectName("cardtitle")
        row.addWidget(lbl)
        row.addStretch(1)
        if right is not None:
            row.addWidget(right)
        lay.addLayout(row)
    return f, lay


def small_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    return lbl


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


class Segmented(QWidget):
    """Knapperad der én er valgt (som «Video / Kun lyd / Video + lydfil» i Nedlaster)."""
    changed = Signal(str)

    def __init__(self, options):   # [(verdi, tekst, ikon)]
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for value, text, ic in options:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icons.icon(ic, C["muted"], 16, on_color="#ffffff"))
            b.setIconSize(QSize(16, 16))
            b.setProperty("value", value)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[value] = b
        self.group.buttonToggled.connect(self._toggled)

    def _toggled(self, b, on):
        if on:
            self.changed.emit(b.property("value"))

    def value(self) -> str:
        b = self.group.checkedButton()
        return b.property("value") if b else ""

    def setValue(self, v: str):
        b = self.buttons.get(v)
        if b:
            self.group.blockSignals(True)
            b.setChecked(True)
            self.group.blockSignals(False)


class BigButton(QPushButton):
    """Stor blå knapp med ikon, tekst og tastetips (som «Last ned  Ctrl+Enter»)."""

    def __init__(self, text: str, kbd: str = "", icon_name: str | None = None):
        super().__init__()
        self.setObjectName("primary")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(9)
        lay.addStretch(1)
        self._icon_name = icon_name
        self.ic = QLabel()
        if icon_name:
            self.ic.setPixmap(icons.pixmap(icon_name, "#ffffff", 18, 2.0))
            lay.addWidget(self.ic)
        self.lbl = QLabel(text)
        self.lbl.setStyleSheet("color:#ffffff; font-weight:700; font-size:10.5pt;")
        lay.addWidget(self.lbl)
        self.kbd = QLabel(kbd)
        self.kbd.setObjectName("kbd")
        self.kbd.setFixedHeight(20)
        self.kbd.setStyleSheet(self._kbd_css("#ffffff"))
        if kbd:
            lay.addWidget(self.kbd, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        for w in (self.ic, self.lbl, self.kbd):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)

    def setLabel(self, text: str):
        self.lbl.setText(text)

    @staticmethod
    def _kbd_css(col: str) -> str:
        return (f"color:{col}; background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.45);"
                "border-radius: 4px; padding: 0px 5px; font-size: 8pt;")

    def changeEvent(self, e):
        if e.type() == QEvent.EnabledChange:
            col = "#ffffff" if self.isEnabled() else "#9fb3cc"
            self.lbl.setStyleSheet(f"color:{col}; font-weight:700; font-size:10.5pt;")
            self.kbd.setStyleSheet(self._kbd_css(col))
            if self._icon_name:
                self.ic.setPixmap(icons.pixmap(self._icon_name, col, 18, 2.0))
        super().changeEvent(e)


def tool_button(action: QAction) -> QToolButton:
    b = QToolButton()
    b.setObjectName("tool")
    b.setDefaultAction(action)
    b.setIconSize(QSize(19, 19))
    b.setCursor(Qt.PointingHandCursor)
    b.setAutoRaise(True)
    return b


class NoWheel(QObject):
    """Rullehjulet skal rulle panelet, ikke endre verdien i nedtrekkslister og tallfelt du tilfeldigvis
    har musepekeren over."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox)):
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QScrollArea):
                w = w.parentWidget()
            if w is not None:
                QApplication.sendEvent(w.verticalScrollBar(), ev)
            return True
        return False


class _ClickFilter(QObject):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            return bool(self.fn(ev))
        return False


class Collapsible(QFrame):
    """Kort som kan foldes ut («> Flere valg»)."""
    toggled = Signal(bool)

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(10)
        self.head = QToolButton()
        self.head.setObjectName("expander")
        self.head.setText(title)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setIconSize(QSize(14, 14))
        self.head.clicked.connect(lambda: self.setExpanded(not self.body.isVisible()))
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 4, 0, 6)
        self.body_lay.setSpacing(10)
        lay.addWidget(self.body)
        self.setExpanded(False)

    def setExpanded(self, on: bool):
        self.body.setVisible(on)
        self.head.setIcon(icons.icon("chev_down" if on else "chev_right", C["text"], 14, stroke=2.2))
        self.toggled.emit(on)


# ================================================================ forhåndsvisning med beskjæring

class CropView(QWidget):
    """Viser siden. Dra med musa over området du vil beholde – siden beskjæres når du slipper knappen."""
    selectionChanged = Signal(bool)
    cropRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._pix: QPixmap | None = None
        self._img_size = (1, 1)
        self._sel: QRect | None = None   # i bildekoordinater
        self._drag_start: QPoint | None = None
        self._logo = icons.logo_pixmap(64)
        self.setCursor(Qt.CrossCursor)

    def set_image(self, img: np.ndarray | None):
        self._sel = None
        if img is None:
            self._pix = None
            self.setCursor(Qt.ArrowCursor)
        else:
            self._pix = QPixmap.fromImage(to_qimage(img))
            self._img_size = (img.shape[1], img.shape[0])
            self.setCursor(Qt.CrossCursor)
        self.selectionChanged.emit(False)
        self.update()

    def selection(self):
        if self._sel is None or self._sel.width() < 10 or self._sel.height() < 10:
            return None
        r = self._sel.normalized()
        return r.left(), r.top(), r.right(), r.bottom()

    def clear_selection(self):
        self._sel = None
        self.selectionChanged.emit(False)
        self.update()

    def _geom(self):
        iw, ih = self._img_size
        s = min((self.width() - 48) / iw, (self.height() - 40) / ih)
        ox = (self.width() - iw * s) / 2
        oy = (self.height() - ih * s) / 2
        return s, ox, oy

    def _to_img(self, p: QPoint) -> QPoint:
        s, ox, oy = self._geom()
        iw, ih = self._img_size
        return QPoint(int(min(max((p.x() - ox) / s, 0), iw)), int(min(max((p.y() - oy) / s, 0), ih)))

    def mousePressEvent(self, e):
        if self._pix and e.button() == Qt.LeftButton:
            self._drag_start = self._to_img(e.position().toPoint())
            self._sel = QRect(self._drag_start, self._drag_start)
            self.update()
        elif e.button() == Qt.RightButton:
            self.clear_selection()

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            self._sel = QRect(self._drag_start, self._to_img(e.position().toPoint()))
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_start is not None:
            self._drag_start = None
            ok = self.selection() is not None
            s, _, _ = self._geom()
            if ok and self._sel.normalized().width() * s >= 12 and self._sel.normalized().height() * s >= 12:
                self.cropRequested.emit()     # dratt over et område: beskjær med en gang
                return
            self._sel = None                  # bare et klikk – ingen markering
            self.selectionChanged.emit(False)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 8, 8)
        p.fillPath(path, QColor(C["canvas"]))
        if not self._pix:
            self._paint_empty(p)
            return
        s, ox, oy = self._geom()
        iw, ih = self._img_size
        target = QRect(int(ox), int(oy), int(iw * s), int(ih * s))
        for k, a in ((10, 18), (6, 26), (3, 40)):   # myk skygge under arket
            p.fillRect(target.adjusted(-k // 2, -k // 3, k // 2, k), QColor(0, 0, 0, a))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(target, self._pix)
        if self._sel is not None:
            r = self._sel.normalized()
            sr = QRect(int(ox + r.left() * s), int(oy + r.top() * s), int(r.width() * s), int(r.height() * s))
            shade = QColor(0, 0, 0, 120)
            p.fillRect(QRect(target.left(), target.top(), target.width(), sr.top() - target.top()), shade)
            p.fillRect(QRect(target.left(), sr.bottom(), target.width(), target.bottom() - sr.bottom()), shade)
            p.fillRect(QRect(target.left(), sr.top(), sr.left() - target.left(), sr.height()), shade)
            p.fillRect(QRect(sr.right(), sr.top(), target.right() - sr.right(), sr.height()), shade)
            p.setPen(QPen(QColor(C["accent"]), 2, Qt.DashLine))
            p.drawRect(sr)

    def _paint_empty(self, p: QPainter):
        cx, cy = self.width() / 2, self.height() / 2
        p.drawPixmap(int(cx - 32), int(cy - 96), self._logo)
        p.setPen(QColor(C["text"]))
        p.setFont(font(13, font().Weight.DemiBold))
        p.drawText(QRect(0, int(cy - 18), self.width(), 30), Qt.AlignCenter, "Klar til å skanne")
        p.setPen(QColor(C["muted"]))
        p.setFont(font(10))
        p.drawText(QRect(24, int(cy + 16), self.width() - 48, 70), Qt.AlignHCenter | Qt.TextWordWrap,
                   "Legg noten på glasset og trykk COPY på skanneren – eller trykk Mellomrom.\n"
                   "Skann gjerne mange sider i ett strekk. Appen deler dem opp i stykker selv.")


# ================================================================ lista over dokumenter og sider

class ListDelegate(QStyledItemDelegate):
    DOC_H, PAGE_H = 56, 116

    def sizeHint(self, opt, idx):
        kind = (idx.data(ROLE_KEY) or ("doc", 0))[0]
        return QSize(opt.rect.width(), self.DOC_H if kind == "doc" else self.PAGE_H)

    def paint(self, p: QPainter, opt, idx):
        kind, _ = idx.data(ROLE_KEY) or ("doc", 0)
        info = idx.data(ROLE_INFO) or {}
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(opt.rect).adjusted(6, 3, -6, -3)
        sel = bool(opt.state & QStyle.State_Selected)
        hover = bool(opt.state & QStyle.State_MouseOver)
        if sel or hover:
            path = QPainterPath()
            path.addRoundedRect(r, 8, 8)
            p.fillPath(path, QColor(C["select"] if sel else C["card_hi"]))
            if sel:
                p.setPen(QPen(QColor(C["accent"]), 1.2))
                p.drawPath(path)
        if kind == "doc":
            self._paint_doc(p, r, info)
        else:
            self._paint_page(p, r, info)
        p.restore()

    def _paint_doc(self, p, r, info):
        if info.get("pages", 0) > 0:     # pil til høyre: fold sammen / ut
            p.drawPixmap(int(r.right() - 24), int(r.top() + 12),
                         icons.pixmap("chev_down" if info.get("open", True) else "chev_right", C["muted"], 16, 2.2))
            r = r.adjusted(0, 0, -26, 0)
        state = info.get("state", "unsaved")
        dot = {"saved": C["green"], "pending": C["accent"], "unsaved": C["amber"]}[state]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(dot))
        p.drawEllipse(QRectF(r.left() + 10, r.top() + 15, 8, 8))
        p.setPen(QColor(C["text"]))
        f = font(10, font().Weight.DemiBold)
        p.setFont(f)
        tr = QRect(int(r.left() + 26), int(r.top() + 6), int(r.width() - 34), 22)
        p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(f).elidedText(info.get("title", ""), Qt.ElideRight, tr.width()))
        f2 = font(8.8)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        sr = QRect(int(r.left() + 26), int(r.top() + 27), int(r.width() - 34), 18)
        p.drawText(sr, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(f2).elidedText(info.get("sub", ""), Qt.ElideRight, sr.width()))

    def _paint_page(self, p, r, info):
        pm: QPixmap = info.get("pixmap")
        box = QRectF(r.left() + 26, r.top() + 6, 74, r.height() - 12)
        if pm is not None and not pm.isNull():
            s = min(box.width() / pm.width(), box.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            tgt = QRectF(box.left() + (box.width() - w) / 2, box.top() + (box.height() - h) / 2, w, h)
            p.fillRect(tgt.adjusted(-1, -1, 2, 2), QColor(0, 0, 0, 90))
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(tgt, pm, QRectF(pm.rect()))
        x = box.right() + 12
        w = r.right() - x - 6
        f = font(9.5, font().Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(x, r.top() + 10, w, 20), Qt.AlignLeft | Qt.AlignVCenter, info.get("label", ""))
        y = r.top() + 32
        if info.get("section"):
            p.drawPixmap(int(x), int(y + 2), icons.pixmap("bookmark", C["accent"], 14, 2.0))
            f2 = font(8.8)
            p.setFont(f2)
            p.setPen(QColor(C["accent"]))
            lines = QFontMetrics(f2).elidedText(info["section"], Qt.ElideRight, int(w - 20))
            p.drawText(QRectF(x + 18, y, w - 18, 18), Qt.AlignLeft | Qt.AlignVCenter, lines)
            y += 20
        if info.get("pending"):
            p.setFont(font(8.8))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(x, y, w, 18), Qt.AlignLeft | Qt.AlignVCenter, "Leser tekst …")
        elif info.get("error"):
            p.setFont(font(8.8))
            p.setPen(QColor(C["amber"]))
            p.drawText(QRectF(x, y, w, 18), Qt.AlignLeft | Qt.AlignVCenter, "Tekstlesing feilet")


# ================================================================ arbeid i bakgrunnen

class ScanWorker(QObject):
    done = Signal(object, float)   # bilde, sekunder skanneren brukte
    failed = Signal(str)

    def __init__(self, dpi, mode, device_id, area_mm=None):
        super().__init__()
        self.dpi, self.mode, self.device_id, self.area_mm = dpi, mode, device_id, area_mm

    def run(self):
        t = time.perf_counter()
        try:
            img = scanner.scan(self.dpi, self.mode, self.device_id, self.area_mm)
            self.done.emit(img, time.perf_counter() - t)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class QueueThread(QThread):
    """Felles grunnlag: tar jobber fra en kø én etter én, i rekkefølge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.q: queue.Queue = queue.Queue()

    def pending(self) -> int:
        return self.q.qsize()

    def stop(self):
        self.q.put(None)
        self.wait(10000)

    def run(self):
        while True:
            job = self.q.get()
            if job is None:
                return
            # Ikke konkurrer med skanneren: LiDE 300 sender bildet mens hodet går, og får PC-en det ikke
            # unna fort nok, stopper hodet og rygger litt. Da tar skanningen lengre tid.
            SCAN_IDLE.wait(180)
            self.work(job)

    def work(self, job):
        raise NotImplementedError


class ProcessThread(QueueThread):
    """Retter opp, beskjærer, forbedrer og komprimerer nye sider, så vinduet ikke henger."""
    done = Signal(object)

    def add(self, opts: dict, img=None, dpi: int = 300, path: str = ""):
        self.q.put({"img": img, "dpi": dpi, "path": path, "opts": opts})

    def work(self, job):
        res = {"source": job["path"], "scan_s": job["opts"].get("scan_s"), "area_name": job["opts"].get("area_name"),
               "replace_id": job["opts"].get("replace_id")}
        try:
            img, dpi = job["img"], job["dpi"]
            if img is None:
                img = read_image(job["path"])
                if img is None:
                    raise RuntimeError(f"Klarte ikke å lese {Path(job['path']).name}")
                dpi = image_dpi(job["path"], dpi)
            res.update(process_scan(img, dpi, job["opts"]))
            res["dpi"] = dpi
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)
        extra = res.pop("extra", [])
        self.done.emit(res)
        for part in extra:
            self.done.emit({**part, "source": res["source"], "dpi": res["dpi"]})


class OcrThread(QueueThread):
    """Leser tittelfeltet på sidene én etter én, i den rekkefølgen de ble skannet."""
    result = Signal(int, object)   # side-id, ocr.Suggestion

    def add(self, page_id: int, png: bytes, dpi: int):
        self.q.put((page_id, png, dpi))

    def work(self, job):
        pid, png, dpi = job
        try:
            img = proc.decode(png)
        except Exception as e:  # noqa: BLE001
            self.result.emit(pid, ocr.Suggestion(engine=f"feil: {e}"))
            return
        try:
            sug = ocr.read_header(img, dpi)
        except Exception as e:  # noqa: BLE001
            sug = ocr.Suggestion(engine=f"feil: {e}")
            try:
                _, sug.header_mm = ocr.header_info(img, dpi)
            except Exception:  # noqa: BLE001
                pass
        self.result.emit(pid, sug)


class SaveThread(QThread):
    """Lager PDF-ene i bakgrunnen, så du kan fortsette å skanne mens en stor bok lagres."""
    progress = Signal(int, int, str)
    job_done = Signal(int, str, str)    # jobbnummer, filsti, feilmelding

    def __init__(self, jobs: list[dict], layout: str, parent=None):
        super().__init__(parent)
        self.jobs, self.layout = jobs, layout

    def run(self):
        total = sum(len(j["pages"]) for j in self.jobs)
        done = 0
        for k, j in enumerate(self.jobs):
            base = done
            try:
                save_pdf(j["pages"], j["path"], self.layout, title=j["title"] or None, author=j["author"] or None,
                         subject=j["subject"] or None, bookmarks=j["bookmarks"],
                         progress=lambda n, b=base, name=j["name"]: self.progress.emit(b + n, total, name))
                self.job_done.emit(k, j["path"], "")
            except Exception as e:  # noqa: BLE001
                self.job_done.emit(k, "", str(e))
            done = base + len(j["pages"])


class FuncThread(QThread):
    """Kjør en funksjon i bakgrunnen og send resultatet (eller feilen) tilbake til hovedtråden."""
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn, parent=None, with_progress=False):
        super().__init__(parent)
        self.fn, self.with_progress = fn, with_progress

    def run(self):
        try:
            if self.with_progress:
                self.result.emit(self.fn(lambda a, b: self.progress.emit(int(a), int(b))))
            else:
                self.result.emit(self.fn())
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e) or type(e).__name__)


class ScannerProbe(QThread):
    found = Signal(list)     # [(device_id, navn), ...]

    def run(self):
        try:
            self.found.emit(list(scanner.list_scanners()))
        except Exception:  # noqa: BLE001
            self.found.emit([])


def _atomic_write(path: Path, data: bytes):
    """Skriv fila trygt: først til en midlertidig fil (som tvinges ned på disken), så bytt navn.
    Et strømbrudd midt i lagringen gir da enten den gamle eller den nye fila – aldri en halv."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _advance_ids(session_dir: Path):
    """Nye sider skal aldri få samme id som en side i den lagrede økta (filnavnene p{id}.png er id-baserte)."""
    global _ids
    top = 0
    try:
        for f in session_dir.iterdir():
            m = re.fullmatch(r"[pto](\d+)\.(png|jpg)", f.name)
            if m:
                top = max(top, int(m.group(1)))
        data = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        top = max([top] + [int(it.get("id", 0)) for it in data.get("pages") or [] if str(it.get("id", "")).isdigit()])
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    _ids = itertools.count(max(top + 1, next(_ids)))


def _suggestion_from_json(d: dict):
    """Suggestion fra økt/arkiv – ukjente felt (fra en nyere versjon) ignoreres, manglende får standardverdi."""
    import dataclasses
    known = {f.name for f in dataclasses.fields(ocr.Suggestion)}
    return ocr.Suggestion(**{k: v for k, v in (d or {}).items() if k in known})


def _make_settings() -> QSettings:
    """Innstillingene (registeret på Windows). Testene setter NOTESCANNER_HOME og får en egen ini-fil."""
    if os.environ.get("NOTESCANNER_HOME"):
        return QSettings(str(paths.data_dir() / "innstillinger.ini"), QSettings.IniFormat)
    return QSettings("NoteScanner", "NoteScanner")


# ================================================================ hovedvindu

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NoteScanner")
        self.setWindowIcon(icons.logo_icon())
        self.resize(1440, 940)
        self.setMinimumSize(1150, 720)
        self.settings = _make_settings()
        self.pages: list[Page] = []
        self.cur_id: int | None = None
        self._thread: QThread | None = None
        self._scan_queue: list = []          # trykk mens skanneren går: [replace_id eller None, …]
        self._active_replace: int | None = None
        self._wia_device: str | None = None
        self._loading_fields = False
        self.save_thread: SaveThread | None = None
        self._save_jobs: list[dict] = []
        self._save_errors: list[str] = []
        self._saved_names: list[str] = []

        self.session_dir = paths.session_dir()
        self.archive_dir = paths.archive_dir()
        _advance_ids(self.session_dir)
        self._folded: set[int] = set()       # dokumenter (første side-id) som er foldet sammen i lista
        self._scanner_name = "?"
        self._abandoned: list = []           # skanninger som hang i driveren (se _scan_timeout)
        self._session_dirty: set[int] = set()
        # Autolagringen rører ikke økta på disk før du har svart på om forrige økt skal hentes fram.
        # (Ellers kan en tom liste bli lagret over sidene mens spørsmålet står åpent.)
        self._session_ready = False
        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.timeout.connect(self._flush_session)

        self.proc_thread = ProcessThread(self)
        self.proc_thread.done.connect(self._page_ready)
        self.proc_thread.start(QThread.LowPriority)
        self.ocr_thread = OcrThread(self)
        self.ocr_thread.result.connect(self._ocr_result)
        self.ocr_thread.start(QThread.LowPriority)

        self._build_actions()
        self._build_ui()
        self._load_settings()

        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._poll_watch_folder)
        self.watch_timer.start(1500)
        self._prime_watch_folder()
        self.probe = ScannerProbe(self)
        self.probe.found.connect(self._scanner_found)
        QTimer.singleShot(300, self.probe.start)
        self._release = None
        self._update_threads: list = []
        if self.auto_update.isChecked():
            QTimer.singleShot(5000, lambda: self.check_updates(manual=False))
        QTimer.singleShot(0, self._offer_restore)
        self._purge_archive()
        self._refresh_archive()

    # ================================================================ handlinger og taster
    def _build_actions(self):
        def act(name, text, slot, shortcut=None, ic=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            tip = text
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                tip += f"   ·   {QKeySequence(shortcut).toString(QKeySequence.NativeText)}"
            a.setToolTip(tip)
            if ic:
                a.setIcon(icons.icon(ic, C["text"], 19))
            self.addAction(a)
            setattr(self, f"a_{name}", a)
            return a

        act("scan", "Skann side", lambda: self.scan_page(), "Space", "scan")
        act("import", "Importer bilder …", self.import_dialog, "Ctrl+O", "import")
        act("rescan", "Skann denne siden på nytt – det nye skannet erstatter siden", self.rescan_page,
            "Shift+Space", "scan")
        act("autocrop", "Auto-crop – beskjær til noter og tekst med marg", self.do_autocrop, "A", "autocrop")
        act("crop", "Beskjær til området du har markert", self.do_crop_selection, "Return", "select")
        act("resetcrop", "Tilbakestill beskjæring – vis hele skannet igjen", self.do_reset_crop, "O", "fit")
        act("deskew", "Rett opp skjev side", self.do_deskew, "D", "deskew")
        act("rotl", "Roter 90° mot klokka", lambda: self.do_rot90(False), "L", "rotl")
        act("rotr", "Roter 90° med klokka", lambda: self.do_rot90(True), "R", "rotr")
        act("enhance", "Forbedre – hvitt papir, svart blekk", self.do_enhance, "E", "enhance")
        act("dust", "Fjern støvprikker", self.do_despeckle, "S", "dust")
        act("read", "Les tittel, stemme og komponist på nytt", self.reread_name, "T", "text")
        act("undo", "Angre", self.do_undo, "Ctrl+Z", "undo")
        act("split", "Del her – start et nytt dokument fra denne siden", self.split_here, "N", "split")
        act("merge", "Slå sammen med dokumentet over", self.merge_with_previous, "M", "merge")
        act("joinimg", "Lim siden under siden over (én bokside som ble delt i to)", self.join_with_previous_image,
            "Ctrl+J", "merge")
        act("up", "Flytt siden opp", lambda: self.move_page(-1), "Ctrl+Up", "up")
        act("down", "Flytt siden ned", lambda: self.move_page(1), "Ctrl+Down", "down")
        act("delete", "Slett siden", self.delete_page, "Delete", "trash")
        act("foldall", "Fold sammen alle dokumenter (vis bare én linje per stykke)", lambda: self.fold_all(True),
            "Ctrl+Left", "fold")
        act("unfoldall", "Fold ut alle dokumenter", lambda: self.fold_all(False), "Ctrl+Right", "unfold")
        act("fold", "Fold sammen / ut dokumentet du står i", self.toggle_fold, "F")
        act("prev", "Forrige side", lambda: self.step_page(-1), "PgUp")
        act("next", "Neste side", lambda: self.step_page(1), "PgDown")
        act("save", "Lagre dokumentet", self.save_current_doc, "Ctrl+S", "save")
        act("saveall", "Lagre alle", self.save_all, "Ctrl+Shift+S", "saveall")
        act("removesaved", "Fjern lagrede dokumenter fra lista", self.remove_saved, "Ctrl+Shift+Del")
        act("clear", "Tøm alt", self.new_session, "Ctrl+N")
        self.a_crop.setEnabled(False)

    # ================================================================ oppsett av vinduet
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 10)
        outer.setSpacing(14)

        # ---------- topplinje: logo, navn, skann-knapp
        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(46))
        head.addWidget(logo)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        h1 = QLabel("NoteScanner")
        h1.setObjectName("h1")
        sub = QLabel("Fra skanneren til ferdige PDF-er – mange sider i ett strekk")
        sub.setObjectName("sub")
        tcol.addStretch(1)
        tcol.addWidget(h1)
        tcol.addWidget(sub)
        tcol.addStretch(1)
        head.addLayout(tcol)
        head.addStretch(1)
        b_imp = QPushButton("  Importer …")
        b_imp.setIcon(icons.icon("import", C["text"], 17))
        b_imp.setIconSize(QSize(17, 17))
        b_imp.setMinimumHeight(44)
        b_imp.setCursor(Qt.PointingHandCursor)
        b_imp.setToolTip(self.a_import.toolTip())
        b_imp.clicked.connect(self.import_dialog)
        head.addWidget(b_imp)
        self.b_scan = BigButton("Skann side", "Mellomrom", "scan")
        self.b_scan.setMinimumWidth(230)
        self.b_scan.setToolTip("Skanner det som ligger på glasset. Du kan også trykke knappen på skanneren.")
        self.b_scan.clicked.connect(lambda: self.scan_page())
        head.addWidget(self.b_scan)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("version")
        ver.setAlignment(Qt.AlignTop)
        head.addWidget(ver)
        outer.addLayout(head)

        # ---------- venstre: dokumenter og sider
        self.count_lbl = small_label("")
        left, ll = card("Dokumenter", self.count_lbl, margins=(10, 14, 10, 10))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setIndentation(0)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setUniformRowHeights(False)
        self.tree.setMouseTracking(True)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.verticalScrollBar().setSingleStep(24)
        self.tree.setItemDelegate(ListDelegate(self.tree))
        self.tree.currentItemChanged.connect(self._tree_selected)
        self.tree.itemDoubleClicked.connect(self._edit_name)
        self._tree_click = _ClickFilter(self._tree_pressed)
        self.tree.viewport().installEventFilter(self._tree_click)
        empty = QLabel("Ingen sider ennå.\n\nSkannede sider havner her,\nsortert i dokumenter.")
        empty.setObjectName("muted")
        empty.setAlignment(Qt.AlignCenter)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(empty)
        self.list_stack.addWidget(self.tree)
        ll.addWidget(self.list_stack, 1)
        row = QHBoxLayout()
        row.setSpacing(2)
        for a in (self.a_split, self.a_merge, self.a_joinimg, self.a_up, self.a_down, self.a_foldall,
                  self.a_unfoldall):
            row.addWidget(tool_button(a))
        row.addStretch(1)
        row.addWidget(tool_button(self.a_delete))
        ll.addLayout(row)

        # «Lagret nylig»: dokumenter som er lagret og tatt ut av lista (ligger på disk i ARCHIVE_DAYS dager)
        self.arch_head = QToolButton()
        self.arch_head.setObjectName("expander")
        self.arch_head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.arch_head.setCursor(Qt.PointingHandCursor)
        self.arch_head.setIconSize(QSize(14, 14))
        self.arch_head.clicked.connect(lambda: self._show_archive(not self.arch_body.isVisible()))
        ll.addWidget(self.arch_head)
        self.arch_body = QWidget()
        al = QVBoxLayout(self.arch_body)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(4)
        self.arch_list = QListWidget()
        self.arch_list.setObjectName("archive")
        self.arch_list.setIconSize(QSize(34, 44))
        self.arch_list.setMinimumHeight(150)
        self.arch_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.arch_list.setTextElideMode(Qt.ElideRight)
        self.arch_list.setUniformItemSizes(True)
        self.arch_list.itemDoubleClicked.connect(lambda _it: self.open_archived())
        self.arch_list.currentItemChanged.connect(lambda *_: self._arch_buttons())
        al.addWidget(self.arch_list)
        arow = QHBoxLayout()
        arow.setSpacing(6)
        self.b_arch_open = QPushButton("Åpne PDF")
        self.b_arch_open.clicked.connect(self.open_archived)
        self.b_arch_back = QPushButton("Hent tilbake")
        self.b_arch_back.setToolTip("Legg dokumentet tilbake i lista for å endre det. Lagrer du igjen, "
                                    "overskrives samme PDF.")
        self.b_arch_back.clicked.connect(self.restore_archived)
        self.b_arch_del = QPushButton("Fjern")
        self.b_arch_del.setToolTip("Fjern fra «Lagret nylig». PDF-filen blir liggende.")
        self.b_arch_del.clicked.connect(self.delete_archived)
        for b in (self.b_arch_open, self.b_arch_back, self.b_arch_del):
            arow.addWidget(b)
        al.addLayout(arow)
        ll.addWidget(self.arch_body)
        self.arch_body.hide()
        left.setMinimumWidth(260)

        # ---------- midten: forhåndsvisning
        center, cl = card(margins=(12, 10, 12, 12))
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.where = QLabel("")
        self.where.setObjectName("cardtitle")
        bar.addWidget(self.where)
        bar.addSpacing(10)
        for a in (self.a_prev, self.a_next):
            a.setIcon(icons.icon("up" if a is self.a_prev else "down", C["text"], 19))
            bar.addWidget(tool_button(a))
        bar.addStretch(1)
        for a in (self.a_autocrop, self.a_resetcrop, self.a_rescan, self.a_deskew, self.a_rotl, self.a_rotr, self.a_enhance,
                  self.a_dust, self.a_read):
            bar.addWidget(tool_button(a))
        sep = QFrame()
        sep.setFixedSize(1, 22)
        sep.setStyleSheet(f"background:{C['border']};")
        bar.addSpacing(4)
        bar.addWidget(sep)
        bar.addSpacing(4)
        bar.addWidget(tool_button(self.a_undo))
        cl.addLayout(bar)
        self.view = CropView()
        self.view.selectionChanged.connect(self.a_crop.setEnabled)
        self.view.cropRequested.connect(self.do_crop_selection)   # beskjær når museknappen slippes
        cl.addWidget(self.view, 1)

        # ---------- høyre: dokument, skanning, lagring
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 6, 0)
        sl.setSpacing(12)

        self.pill = QLabel("")
        doc, dl = card("Dokument", self.pill)
        self.title = QLineEdit()
        self.title.setPlaceholderText("f.eks. Be Still My Soul")
        self.part = QLineEdit()
        self.part.setPlaceholderText("f.eks. Solo Bb Cornet")
        self.composer = QLineEdit()
        self.composer.setPlaceholderText("(valgfritt)")
        self.edits = {"title": self.title, "part": self.part, "composer": self.composer}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.field_labels = {}
        for r_, (key, lbl, e) in enumerate((("title", "Tittel", self.title), ("part", "Stemme", self.part),
                                            ("composer", "Komponist", self.composer))):
            self.field_labels[key] = small_label(lbl)
            grid.addWidget(self.field_labels[key], r_, 0)
            grid.addWidget(e, r_, 1)
        # Sangnavn for siden som vises (bokmerke i PDF-en) – brukes i «Én bok»
        self.song = QLineEdit()
        self.song.setPlaceholderText("(ingen – siden hører til sangen over)")
        self.song.setToolTip("Navnet på sangen som starter på denne siden. Blir bokmerke i PDF-en.\n"
                             "Tøm feltet hvis siden ikke starter en ny sang. T leser siden på nytt.")
        self.song.textEdited.connect(self._song_edited)
        self.song_label = small_label("Sang her")
        grid.addWidget(self.song_label, 3, 0)
        grid.addWidget(self.song, 3, 1)
        dl.addLayout(grid)
        for name, e in self.edits.items():
            e.textEdited.connect(lambda _t, n=name: self._field_edited(n))
        self.name_preview = QLabel()
        self.name_preview.setObjectName("hint")
        self.name_preview.setWordWrap(True)
        dl.addWidget(self.name_preview)
        self.b_save_doc = QPushButton("  Lagre dette dokumentet")
        self.b_save_doc.setIcon(icons.icon("save", C["text"], 16))
        self.b_save_doc.setCursor(Qt.PointingHandCursor)
        self.b_save_doc.clicked.connect(self.save_current_doc)
        dl.addWidget(self.b_save_doc)
        sl.addWidget(doc)

        scan, scl = card("Skanning")
        scl.addWidget(small_label("Resultat"))
        self.mode_seg = Segmented([("gray", "Gråtone", "gray"), ("bw", "Svart-hvitt", "bw"),
                                   ("color", "Farge", "color")])
        self.mode_seg.changed.connect(self._mode_changed)
        scl.addWidget(self.mode_seg)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("hint")
        self.mode_hint.setWordWrap(True)
        scl.addWidget(self.mode_hint)
        scl.addSpacing(2)
        scl.addWidget(small_label("Oppdeling"))
        self.split_seg = Segmented([("auto", "Del opp automatisk", "docs"), ("book", "Én bok", "book")])
        self.split_seg.changed.connect(self._split_mode_changed)
        scl.addWidget(self.split_seg)
        self.split_hint = QLabel()
        self.split_hint.setObjectName("hint")
        self.split_hint.setWordWrap(True)
        scl.addWidget(self.split_hint)
        g4 = QGridLayout()
        g4.setHorizontalSpacing(10)
        g4.setVerticalSpacing(8)
        self.dpi = QComboBox()
        for d, t in ((150, "150 dpi – utkast"), (200, "200 dpi"), (300, "300 dpi – anbefalt"), (400, "400 dpi"),
                     (600, "600 dpi – små noter")):
            self.dpi.addItem(t, d)
        self.area = QComboBox()
        for key, text, _ in AREAS:
            self.area.addItem(text, key)
        self.area.currentIndexChanged.connect(self._area_changed)
        self.area_w = QSpinBox()
        self.area_h = QSpinBox()
        for sb, tip in ((self.area_w, "Bredde (på tvers av skannerhodet)"),
                        (self.area_h, "Lengde (så langt skannerhodet går – det er denne som bestemmer tiden)")):
            sb.setRange(30, 300)
            sb.setSuffix(" mm")
            sb.setToolTip(tip)
        self.custom_row = QWidget()
        cr = QHBoxLayout(self.custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.setSpacing(6)
        cr.addWidget(self.area_w, 1)
        x = small_label("×")
        cr.addWidget(x)
        cr.addWidget(self.area_h, 1)
        self.rotate = QComboBox()
        for key, text in ROTATIONS:
            self.rotate.addItem(text, key)
        self.rotate.setToolTip("Legger du arket på tvers for å skanne raskere, snur appen det tilbake")
        self.scanner_box = QComboBox()
        self.scanner_box.addItem("Automatisk", "")
        self.scanner_box.setToolTip("Hvilken skanner som brukes når du trykker Skann side (mellomrom). "
                                    "Automatisk = den første som svarer.")
        self.scanner_box.activated.connect(self._scanner_chosen)
        g4.addWidget(small_label("Skanner"), 0, 0)
        g4.addWidget(self.scanner_box, 0, 1)
        g4.addWidget(small_label("Oppløsning"), 1, 0)
        g4.addWidget(self.dpi, 1, 1)
        g4.addWidget(small_label("Område"), 2, 0)
        g4.addWidget(self.area, 2, 1)
        g4.addWidget(self.custom_row, 3, 1)
        g4.addWidget(small_label("Snu"), 4, 0)
        g4.addWidget(self.rotate, 4, 1)
        scl.addLayout(g4)
        self.area_hint = QLabel()
        self.area_hint.setObjectName("hint")
        self.area_hint.setWordWrap(True)
        scl.addWidget(self.area_hint)
        self.auto_name = QCheckBox("Les tittel, stemme og komponist")
        eng = ocr.available_engine()
        self.auto_name.setToolTip(f"Tekstlesing: {eng}" if eng else "Ingen tekstlesing funnet – se LES_MEG.md")
        self.auto_name.toggled.connect(lambda _: self._update_hints())
        scl.addWidget(self.auto_name)
        sl.addWidget(scan)

        save, svl = card("Lagres i")
        orow = QHBoxLayout()
        orow.setSpacing(8)
        self.outdir = QLineEdit()
        orow.addWidget(self.outdir, 1)
        b_out = QPushButton("Bla gjennom …")
        b_out.setCursor(Qt.PointingHandCursor)
        b_out.clicked.connect(lambda: self._pick_dir(self.outdir))
        b_open = QPushButton("Åpne")
        b_open.setCursor(Qt.PointingHandCursor)
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.outdir.text())))
        orow.addWidget(b_out)
        orow.addWidget(b_open)
        svl.addLayout(orow)
        g2 = QGridLayout()
        g2.setHorizontalSpacing(10)
        g2.setVerticalSpacing(8)
        self.pattern = QComboBox()
        self.pattern.addItems(NAME_PATTERNS)
        self.pattern.currentIndexChanged.connect(self._update_name_preview)
        self.pagesize = QComboBox()
        # Rekkefølgen må stå fast – innstillingen lagres som nummer
        self.pagesize.addItems(["A4-ark, originalstørrelse", "A4-ark, forstørret til hele arket",
                                "Som beskåret (minst marg) – standard"])
        self.pagesize.currentIndexChanged.connect(self._pagesize_changed)
        g2.addWidget(small_label("Filnavn"), 0, 0)
        g2.addWidget(self.pattern, 0, 1)
        g2.addWidget(small_label("PDF-side"), 1, 0)
        g2.addWidget(self.pagesize, 1, 1)
        svl.addLayout(g2)
        self.pagesize_hint = QLabel()
        self.pagesize_hint.setObjectName("hint")
        self.pagesize_hint.setWordWrap(True)
        svl.addWidget(self.pagesize_hint)
        self.clear_after = QCheckBox(f"Flytt lagrede dokumenter til «Lagret nylig» ({ARCHIVE_DAYS} dager)")
        self.clear_after.setToolTip("Lagrede stykker forsvinner fra lista, men kan åpnes eller hentes tilbake "
                                    f"fra «Lagret nylig» nederst til venstre i {ARCHIVE_DAYS} dager.")
        svl.addWidget(self.clear_after)
        sl.addWidget(save)


        self.more = Collapsible("Flere valg")
        ml = self.more.body_lay
        ml.addWidget(small_label("Gjør automatisk på nye sider"))
        self.auto_deskew = QCheckBox("Rett opp skjeve sider")
        self.auto_crop = QCheckBox("Auto-crop")
        self.auto_enhance = QCheckBox("Forbedre (hvitt papir, svart blekk)")
        self.auto_despeckle = QCheckBox("Fjern støvprikker")
        self.skip_blank = QCheckBox("Hopp over tomme sider")
        self.split_songs = QCheckBox("Del sider med flere sanger")
        self.split_songs.setToolTip("Står det to korte sanger på samme bokside, blir hver sang en egen side i lista "
                                    "(krever auto-crop)")
        for w in (self.auto_deskew, self.auto_crop, self.auto_enhance, self.auto_despeckle, self.skip_blank,
                  self.split_songs):
            ml.addWidget(w)
        g3 = QGridLayout()
        g3.setHorizontalSpacing(10)
        g3.setVerticalSpacing(8)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 40)
        self.margin.setSuffix(" mm")
        self.strength = QSpinBox()
        self.strength.setRange(0, 10)
        self.strength.setToolTip("Hvor svart blekket blir når siden forbedres (0 = svakt, 10 = kraftig)")
        self.strength.valueChanged.connect(self._reenhance_current)
        g3.addWidget(small_label("Marg ved crop"), 0, 0)
        g3.addWidget(self.margin, 0, 1)
        g3.addWidget(small_label("Svarthet (0–10)"), 1, 0)
        g3.addWidget(self.strength, 1, 1)
        ml.addLayout(g3)
        b_all = QPushButton("  Bruk resultat og svarthet på alle sider")
        b_all.setIcon(icons.icon("apply", C["text"], 16))
        b_all.setCursor(Qt.PointingHandCursor)
        b_all.setToolTip("Forbedrer alle sidene på nytt med valgene over, f.eks. etter å ha byttet til svart-hvitt")
        b_all.clicked.connect(self.apply_to_all)
        ml.addWidget(b_all)
        b_read = QPushButton("  Les tittel og stemme på alle sider på nytt")
        b_read.setIcon(icons.icon("text", C["text"], 16))
        b_read.setCursor(Qt.PointingHandCursor)
        b_read.setToolTip("Leser tittelfeltet på alle sidene i lista på nytt. Navn du har skrevet selv beholdes.")
        b_read.clicked.connect(self.reread_all)
        ml.addWidget(b_read)
        ml.addSpacing(4)
        ml.addWidget(small_label("Overvåk mappe – nye bilder her blir sider (skanneknapp via Canon/Brother-programmet)"))
        wrow = QHBoxLayout()
        self.watchdir = QLineEdit()
        self.watchdir.setPlaceholderText("(av)")
        b_watch = QPushButton("Velg …")
        b_watch.setCursor(Qt.PointingHandCursor)
        b_watch.clicked.connect(lambda: (self._pick_dir(self.watchdir), self._prime_watch_folder()))
        wrow.addWidget(self.watchdir, 1)
        wrow.addWidget(b_watch)
        ml.addLayout(wrow)
        b_clear = QPushButton("  Tøm hele lista")
        b_clear.setIcon(icons.icon("trash", C["text"], 16))
        b_clear.clicked.connect(self.new_session)
        ml.addWidget(b_clear)
        ml.addSpacing(6)
        ml.addWidget(small_label(f"NoteScanner {APP_VERSION}"))
        if sys.platform == "win32":
            b_btn = QPushButton("  Koble skannerknappen til NoteScanner")
            b_btn.setIcon(icons.icon("scan", C["text"], 16))
            b_btn.setToolTip("Gjør at knappen på skanneren kan starte NoteScanner (Windows spør om "
                             "administrator-tillatelse). Velg deretter NoteScanner under Skanneregenskaper → Hendelser.")
            b_btn.clicked.connect(self.register_button)
            ml.addWidget(b_btn)
        b_upd = QPushButton("  Se etter ny versjon")
        b_upd.setIcon(icons.icon("down", C["text"], 16))
        b_upd.clicked.connect(lambda: self.check_updates(manual=True))
        ml.addWidget(b_upd)
        self.auto_update = QCheckBox("Se etter ny versjon når appen starter")
        ml.addWidget(self.auto_update)
        b_bug = QPushButton("  Rapporter feil …")
        b_bug.setIcon(icons.icon("text", C["text"], 16))
        b_bug.setToolTip("Åpner en ferdig utfylt feilmelding på GitHub (versjon og logg, uten personlige "
                         "mappenavn og uten bilder av notene)")
        b_bug.clicked.connect(self.report_bug)
        ml.addWidget(b_bug)
        sl.addWidget(self.more)
        sl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)
        rl.addWidget(scroll, 1)
        self.b_save_all = BigButton("Lagre alle", "Ctrl+Shift+S", "saveall")
        self.b_save_all.setMinimumHeight(48)
        self.b_save_all.clicked.connect(self.save_all)
        rl.addWidget(self.b_save_all)
        right.setMinimumWidth(372)
        right.setMaximumWidth(420)

        split = QSplitter()
        split.setHandleWidth(14)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([300, 760, 380])
        split.setCollapsible(1, False)
        # stripe som vises når en ny versjon er klar
        self.update_bar = QFrame()
        self.update_bar.setObjectName("banner")
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(14, 8, 10, 8)
        ub.setSpacing(10)
        self.update_lbl = QLabel()
        self.update_lbl.setWordWrap(True)
        ub.addWidget(self.update_lbl, 1)
        self.b_news = QPushButton("Hva er nytt?")
        self.b_news.clicked.connect(self._show_release_notes)
        self.b_update = QPushButton("Oppdater nå")
        self.b_update.setObjectName("primary")
        self.b_update.clicked.connect(self.install_update)
        b_later = QPushButton("Senere")
        b_later.clicked.connect(self.update_bar.hide)
        for b in (self.b_news, self.b_update, b_later):
            b.setCursor(Qt.PointingHandCursor)
            ub.addWidget(b)
        self.update_bar.hide()
        outer.addWidget(self.update_bar)
        outer.addWidget(split, 1)
        self.setCentralWidget(root)

        # ---------- statuslinje
        self.status = QStatusBar()
        self.status.setSizeGripEnabled(False)
        self.setStatusBar(self.status)
        self.prog_lbl = QLabel("")
        self.prog = QProgressBar()
        self.prog.setFixedWidth(180)
        self.prog.setFixedHeight(10)
        self.prog.setTextVisible(False)
        self.prog_lbl.hide()
        self.prog.hide()
        self.scan_lbl = QLabel()
        self.ocr_lbl = QLabel()
        self.busy_lbl = QLabel()
        for w in (self.prog_lbl, self.prog, self.busy_lbl, self.scan_lbl, self.ocr_lbl):
            self.status.addPermanentWidget(w)
        self._set_dot(self.scan_lbl, "muted", "Ser etter skanner …")
        eng = ocr.available_engine()
        self._set_dot(self.ocr_lbl, "green" if eng else "amber", f"Tekstlesing: {eng}" if eng else "Ingen tekstlesing")
        self._refresh_tree()

    def _set_dot(self, lbl: QLabel, color: str, text: str):
        lbl.setText(f'<span style="color:{C[color]}">●</span>&nbsp; {text}')

    def _scanner_chosen(self, _i=None):
        self.settings.setValue("scanner_id", self.scanner_box.currentData() or "")
        self.settings.setValue("scanner_name", self.scanner_box.currentText() if self.scanner_box.currentData() else "")
        self._wia_device = None        # valget i lista gjelder fra nå (ikke skanneren som startet appen)

    def _scanner_found(self, devices: list):
        names = [n for _, n in devices]
        saved = str(self.settings.value("scanner_id", "") or "")
        self.scanner_box.blockSignals(True)
        self.scanner_box.clear()
        self.scanner_box.addItem("Automatisk" + (f" ({names[0]})" if names else ""), "")
        for dev_id, name in devices:
            self.scanner_box.addItem(name, dev_id)
        if saved and self.scanner_box.findData(saved) < 0:      # valgt skanner er ikke koblet til nå
            self.scanner_box.addItem("(ikke tilkoblet) " + str(self.settings.value("scanner_name", "")), saved)
        i = self.scanner_box.findData(saved)
        self.scanner_box.setCurrentIndex(i if i >= 0 else 0)
        self.scanner_box.blockSignals(False)
        if not scanner.available():
            self._set_dot(self.scan_lbl, "amber", "Skanning virker bare på Windows")
        elif names:
            self._scanner_name = ", ".join(names)
            self._set_dot(self.scan_lbl, "green", ", ".join(names))
        else:
            self._set_dot(self.scan_lbl, "amber", "Fant ingen skanner")

    def _pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Velg mappe", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)

    # ================================================================ innstillinger
    _CHECKS = (("auto_deskew", "true"), ("auto_crop", "true"), ("auto_enhance", "true"),
               ("auto_despeckle", "false"), ("skip_blank", "true"), ("auto_name", "true"),
               ("split_songs", "true"), ("auto_update", "true"))

    def _num(self, key: str, default, typ=int):
        """Tall fra innstillingene – en ødelagt verdi skal aldri hindre appen i å starte."""
        try:
            return typ(self.settings.value(key, default))
        except (TypeError, ValueError):
            return typ(default)

    def _load_settings(self):
        s = self.settings
        self.outdir.setText(str(s.value("outdir", str(paths.documents_dir() / "Noter")) or ""))
        self.watchdir.setText(str(s.value("watchdir", "") or ""))
        i = self.dpi.findData(self._num("dpi", 300, int))
        self.dpi.setCurrentIndex(i if i >= 0 else 2)
        mode = s.value("result_mode")
        if mode not in ("gray", "bw", "color"):   # innstillinger fra versjon 1
            old_mode, old_enh = self._num("mode", 0, int), self._num("enhance_mode", 0, int)
            mode = "color" if old_mode == 1 else "bw" if (old_mode == 2 or old_enh == 1) else "gray"
        self.mode_seg.setValue(mode)
        self.split_seg.setValue(s.value("split_mode") if s.value("split_mode") in ("auto", "book") else "auto")
        self.margin.setValue(self._num("margin", 5.0, float))
        self.area_w.setValue(self._num("area_w", 148, int))
        self.area_h.setValue(self._num("area_h", 210, int))
        ai = self.area.findData(s.value("area", "full"))
        self.area.setCurrentIndex(ai if ai >= 0 else 0)
        ri = self.rotate.findData(s.value("rotate", "0"))
        self.rotate.setCurrentIndex(ri if ri >= 0 else 0)
        self._area_changed()
        self.strength.setValue(self._num("strength_v2", 4, int))
        self.pagesize.blockSignals(True)
        self.pagesize.setCurrentIndex(self._num("pagesize_v2", 2, int))   # standard: som beskåret
        self.pagesize.blockSignals(False)
        self._pagesize_hint()
        self.pattern.setCurrentIndex(self._num("pattern", 0, int))
        self.clear_after.setChecked(s.value("clear_after_v3", "true") == "true")
        for key, dflt in self._CHECKS:
            getattr(self, key).setChecked(s.value(key, dflt) == "true")
        self.more.setExpanded(s.value("more_open", "false") == "true")
        geo = s.value("geometry")
        if geo:
            try:
                self.restoreGeometry(geo)
            except (TypeError, ValueError):
                pass
        self._update_hints()

    def _save_settings(self):
        s = self.settings
        s.setValue("outdir", self.outdir.text())
        s.setValue("watchdir", self.watchdir.text())
        s.setValue("dpi", self.dpi.currentData())
        s.setValue("result_mode", self.mode)
        s.setValue("split_mode", self.split_mode)
        s.setValue("margin", self.margin.value())
        s.setValue("area", self.area.currentData())
        s.setValue("area_w", self.area_w.value())
        s.setValue("area_h", self.area_h.value())
        s.setValue("rotate", self.rotate.currentData())
        s.setValue("strength_v2", self.strength.value())
        s.setValue("pagesize_v2", self.pagesize.currentIndex())
        s.setValue("pattern", self.pattern.currentIndex())
        s.setValue("clear_after_v3", "true" if self.clear_after.isChecked() else "false")
        for key, _ in self._CHECKS:
            s.setValue(key, "true" if getattr(self, key).isChecked() else "false")
        s.setValue("more_open", "true" if self.more.body.isVisible() else "false")
        s.setValue("geometry", self.saveGeometry())

    @property
    def mode(self) -> str:
        return self.mode_seg.value() or "gray"

    @property
    def split_mode(self) -> str:
        return self.split_seg.value() or "auto"

    def _update_hints(self):
        self.mode_hint.setText({
            "gray": "Beholder blyant og gråtoner. Rundt 1 MB per side i PDF-en.",
            "bw": "Skarpest og minst fil, rundt 50–150 kB per side. Lurt for store bøker.",
            "color": "Til noter med farger. Største filer.",
        }[self.mode])
        reading = self.auto_name.isChecked()
        self.split_hint.setText({
            "auto": "En side med stor tittel starter et nytt dokument. Sidene etter følger med."
                    if reading else "Slå på tekstlesing for å dele opp automatisk – ellers deler du med N.",
            "book": "Alle sidene havner i én PDF. Titlene inni boka blir bokmerker i PDF-en."
                    if reading else "Alle sidene havner i én PDF.",
        }[self.split_mode])

    def _pagesize_hint(self):
        self.pagesize_hint.setText([
            "Noten havner i samme størrelse som originalen midt på et A4-ark. Riktig for utskrift av A4-noter – "
            "mindre bøker får bred hvit marg.",
            "Noten forstørres så den fyller et A4-ark. Fint for å skrive ut små bøker i større format.",
            "PDF-siden blir akkurat like stor som det beskårne bildet – slik det ser ut i appen. "
            "Best på nettbrett og skjerm.",
        ][self.pagesize.currentIndex()])

    def _pagesize_changed(self, *_):
        self._pagesize_hint()
        changed = 0
        for s, _ in self.documents():
            m = self.pages[s].meta
            if m.saved:          # lagrede dokumenter må lagres på nytt for å få ny sidestørrelse
                m.bump()
                changed += 1
        if changed:
            self._refresh_tree()
            self.status.showMessage(f"PDF-siden er endret. Trykk «Lagre alle» for å lagre {changed} dokument(er) "
                                    "på nytt – de gamle filene blir erstattet.", 12000)

    def _area_mm(self):
        key = self.area.currentData()
        if key == "custom":
            return (self.area_w.value(), self.area_h.value())
        return dict((k, a) for k, _, a in AREAS).get(key)

    def _area_changed(self, *_):
        self.custom_row.setVisible(self.area.currentData() == "custom")
        a = self._area_mm()
        if a is None:
            self.area_hint.setText("Skanner hele glassplata.")
        else:
            pct = round(100 * (1 - min(a[1], 297) / 297))
            self.area_hint.setText(f"Legg arket inntil hjørnemerket på skanneren. Skannerhodet går bare {a[1]} mm"
                                   + (f" – rundt {pct} % kortere enn hele glasset." if pct >= 5 else ".")
                                   + (" Ligger arket på tvers, velg «Snu»." if a[0] > a[1] else ""))

    def _mode_changed(self, _v):
        self._update_hints()
        self._reenhance_current()

    def _split_mode_changed(self, _v):
        self._update_hints()
        self._load_fields()
        before = len(self.documents())
        self._regroup()
        self._refresh_tree()
        after = len(self.documents())
        if before != after:
            self.status.showMessage(f"Delte opp på nytt: {after} dokument(er).", 6000)

    # ================================================================ dokumentmodell
    def _normalize(self):
        if self.pages and self.pages[0].meta is None:
            self.pages[0].meta = DocMeta()
            self.pages[0].meta.fill_from(self.pages[0].sug)

    def documents(self) -> list[tuple[int, int]]:
        """[(start, slutt_eksklusiv), …] – dokumentene som sammenhengende biter av sidelista."""
        self._normalize()
        starts = [i for i, p in enumerate(self.pages) if p.meta is not None]
        return [(s, starts[k + 1] if k + 1 < len(starts) else len(self.pages)) for k, s in enumerate(starts)]

    def _doc_of(self, idx: int) -> tuple[int, int]:
        for s, e in self.documents():
            if s <= idx < e:
                return s, e
        return 0, 0

    def _doc_no(self, idx: int) -> int:
        for k, (s, e) in enumerate(self.documents(), 1):
            if s <= idx < e:
                return k
        return 0

    def _index(self, page_id: int | None) -> int:
        for i, p in enumerate(self.pages):
            if p.id == page_id:
                return i
        return -1

    def current_index(self) -> int:
        return self._index(self.cur_id)

    def current(self) -> Page | None:
        i = self.current_index()
        return self.pages[i] if i >= 0 else None

    def current_meta(self) -> DocMeta | None:
        i = self.current_index()
        return self.pages[self._doc_of(i)[0]].meta if i >= 0 else None

    def _touch(self, idx: int):
        """Noe i dokumentet er endret – det må lagres på nytt."""
        if 0 <= idx < len(self.pages):
            m = self.pages[self._doc_of(idx)[0]].meta
            if m:
                m.bump()

    def _regroup(self, fresh_id: int | None = None):
        """Del sidene i dokumenter ut fra tekstlesingen og valgt oppdeling.
        Grenser brukeren har satt selv (og dokumenter med navn brukeren har skrevet) står fast.
        En tittelside med samme tittel som sangen før, eller med «forts.», er en fortsettelse.
        fresh_id: en side som akkurat er lest for første gang – den har aldri vært med i dokumentet over,
        så blir den et nytt dokument, er dokumentet over uendret (fortsatt lagret)."""
        cur, cur_part = "", ""   # sangen og stemmen vi er inne i
        for i, p in enumerate(self.pages):
            t = p.sug.title if (p.sug is not None and p.sug.is_title_page) else ""
            part = p.sug.part if p.sug is not None else ""
            new_song = bool(t) and not same_song(t, part, cur, cur_part)
            typed = p.meta is not None and p.meta.edited and self.split_mode == "auto"
            if i == 0 or p.manual or typed or p.ocr_state == "pending":
                if p.meta is not None:
                    cur, cur_part = (p.meta.title or t), (p.meta.part or part)
                elif new_song:
                    cur, cur_part = t, part
                continue
            want = self.split_mode == "auto" and new_song
            if new_song:
                cur, cur_part = t, part
            if want and p.meta is None:
                if p.id != fresh_id:
                    self._touch(i - 1)
                if p.hidden_meta is not None:      # samme dokument som før «Én bok» – beholder lagret-status
                    p.meta, p.hidden_meta = p.hidden_meta, None
                else:
                    p.meta = DocMeta()
                    p.meta.fill_from(p.sug)
            elif not want and p.meta is not None:
                if p.meta.edited and p.meta.title and p.section is None:
                    p.section = p.meta.title       # navnet du skrev blir bokmerket i boka
                p.hidden_meta, p.meta = p.meta, None
                self._touch(i)
        self._normalize()

    def _sections(self, s: int, e: int) -> list[tuple[int, str]]:
        """Stykker inni et dokument (titler på side 2 og utover) -> bokmerker i PDF-en."""
        m0 = self.pages[s].meta
        secs, last, last_part = [], m0.title, m0.part
        for k, p in enumerate(self.pages[s:e], s):
            if p.section is not None:      # du har skrevet sangnavnet (eller fjernet det) selv
                if p.section:
                    secs.append((k - s, p.section))
                    last, last_part = p.section, ""
                continue
            t = p.sug.title if (p.sug is not None and p.sug.is_title_page) else ""
            part = p.sug.part if p.sug is not None else ""
            if not t or (k > s and same_song(t, part, last, last_part)) or (k == s and is_continuation(t)):
                continue   # fortsettelse av sangen før – ikke nytt bokmerke
            secs.append((k - s, t + (f" – {part}" if part and k > s and same_title(t, last) else "")))
            last, last_part = t, part
        if not any(i > 0 for i, _ in secs) and not any(p.section for p in self.pages[s:e]):
            return []
        if secs[0][0] != 0:
            secs.insert(0, (0, self.pages[s].meta.title or "Start"))
        return secs

    def _filename(self, m: DocMeta, doc_no: int) -> str:
        t, p, c = m.title.strip(), m.part.strip(), m.composer.strip()
        order = {0: [t, p], 1: [t, c, p], 2: [c, t, p], 3: [p, t]}[self.pattern.currentIndex()]
        name = " - ".join(x for x in order if x)
        if not name:
            name = datetime.now().strftime("Noter %Y-%m-%d") + f" dokument {doc_no}"
        return safe_name(name, datetime.now().strftime("Noter %Y-%m-%d") + f" dokument {doc_no}")

    # ================================================================ lista til venstre
    def _doc_info(self, no: int, s: int, e: int) -> dict:
        m = self.pages[s].meta
        n = e - s
        pending = any(p.ocr_state == "pending" for p in self.pages[s:e])
        if m.title or m.part:
            title = " – ".join(x for x in (m.title, m.part) if x)
        else:
            title = "Leser …" if pending else "Uten navn"
        bits = [f"{n} side{'r' if n != 1 else ''}"]
        secs = self._sections(s, e)
        if secs:
            bits.append(f"{len(secs)} bokmerker")
        if m.saved:
            bits.append("✓ lagret")
            state = "saved"
        else:
            bits.append("endret siden lagring" if m.saved_path else "ikke lagret")
            state = "pending" if pending else "unsaved"
        return {"title": f"{no}. {title}", "sub": " · ".join(bits), "state": state}

    def _refresh_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        select = None
        for no, (s, e) in enumerate(self.documents(), 1):
            d = QTreeWidgetItem([""])
            d.setData(0, ROLE_KEY, ("doc", self.pages[s].id))
            folded = self.pages[s].id in self._folded and not any(p.id == self.cur_id for p in self.pages[s + 1:e])
            d.setData(0, ROLE_INFO, self._doc_info(no, s, e) | {"pages": e - s, "open": not folded})
            self.tree.addTopLevelItem(d)
            secs = dict(self._sections(s, e))
            for k in range(s, e):
                p = self.pages[k]
                c = QTreeWidgetItem([""])
                c.setData(0, ROLE_KEY, ("page", p.id))
                c.setData(0, ROLE_INFO, {"label": f"Side {k - s + 1}", "pixmap": p.thumb(),
                                         "section": secs.get(k - s, ""), "pending": p.ocr_state == "pending",
                                         "error": bool(p.sug is not None and p.sug.engine.startswith("feil"))})
                d.addChild(c)
                if p.id == self.cur_id:
                    select = c
            d.setExpanded(not folded)
            if folded and self.pages[s].id == self.cur_id:
                select = d
        if select is not None:
            self.tree.setCurrentItem(select)
        self.tree.blockSignals(False)
        if select is not None:
            self.tree.scrollToItem(select)
        self.list_stack.setCurrentIndex(1 if self.pages else 0)
        self._show_current()
        self._update_info()
        self._schedule_session()

    def _update_doc_item(self):
        """Oppdater bare dokumentlinja som er valgt (uten å bygge lista på nytt)."""
        i = self.current_index()
        if i < 0:
            return
        for no, (s, e) in enumerate(self.documents(), 1):
            if s <= i < e:
                it = self.tree.topLevelItem(no - 1)
                if it:
                    it.setData(0, ROLE_INFO, self._doc_info(no, s, e))
        self._update_info()

    def _tree_selected(self, item, _prev=None):
        if item is None:
            return
        self.cur_id = item.data(0, ROLE_KEY)[1]
        self._show_current()

    def _set_folded(self, item, folded: bool):
        pid = item.data(0, ROLE_KEY)[1]
        (self._folded.add if folded else self._folded.discard)(pid)
        item.setExpanded(not folded)
        info = dict(item.data(0, ROLE_INFO) or {})
        info["open"] = not folded
        item.setData(0, ROLE_INFO, info)

    def toggle_fold(self, item=None):
        item = item or self.tree.currentItem()
        if item is None:
            return
        if item.parent() is not None:
            item = item.parent()
        self._set_folded(item, item.isExpanded())

    def fold_all(self, folded: bool = True):
        for t in range(self.tree.topLevelItemCount()):
            d = self.tree.topLevelItem(t)
            if folded and d.isExpanded() and any(d.child(c).isSelected() for c in range(d.childCount())):
                self.tree.setCurrentItem(d)     # valgt side gjemmes: velg dokumentlinja i stedet
            self._set_folded(d, folded)

    def _tree_pressed(self, ev) -> bool:
        """Klikk på pila til høyre på en dokumentlinje folder sammen/ut."""
        item = self.tree.itemAt(ev.position().toPoint())
        if item is None or item.parent() is not None:
            return False
        r = self.tree.visualItemRect(item)
        if ev.position().x() >= r.right() - 40:
            self.toggle_fold(item)
            return True
        return False

    def _edit_name(self, item, _col=0):
        """Dobbeltklikk i lista: skriv navnet (sangen på siden i en bok, ellers dokumentets tittel)."""
        kind, _pid = item.data(0, ROLE_KEY)
        target = self.song if (self.split_mode == "book" and kind == "page") else self.title
        target.setFocus()
        target.selectAll()

    def _select_page(self, page_id: int):
        self.cur_id = page_id
        it = self._find_item(page_id)
        if it is not None and it.parent() is not None and not it.parent().isExpanded():
            self._set_folded(it.parent(), False)    # bla inn i et sammenfoldet dokument: fold det ut
        if it is not None:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(it)
            self.tree.blockSignals(False)
            self.tree.scrollToItem(it)
        self._show_current()

    def step_page(self, d: int):
        i = self.current_index()
        if self.pages:
            j = min(max(0, (i if i >= 0 else 0) + d), len(self.pages) - 1)
            self._select_page(self.pages[j].id)

    def _find_item(self, page_id):
        for t in range(self.tree.topLevelItemCount()):
            d = self.tree.topLevelItem(t)
            for c in range(d.childCount()):
                if d.child(c).data(0, ROLE_KEY)[1] == page_id:
                    return d.child(c)
        return None

    def _refresh_thumb(self, page: Page):
        it = self._find_item(page.id)
        if it is not None:
            info = dict(it.data(0, ROLE_INFO) or {})
            info["pixmap"] = page.thumb()
            it.setData(0, ROLE_INFO, info)

    def _show_current(self):
        pg = self.current()
        self.view.set_image(pg.img if pg else None)
        i = self.current_index()
        if pg:
            s, e = self._doc_of(i)
            self.where.setText(f"Dokument {self._doc_no(i)} · side {i - s + 1} av {e - s}")
        else:
            self.where.setText("Forhåndsvisning")
        for a in (self.a_autocrop, self.a_resetcrop, self.a_rescan, self.a_deskew, self.a_rotl, self.a_rotr, self.a_enhance,
                  self.a_dust, self.a_read, self.a_split, self.a_merge, self.a_up, self.a_down, self.a_delete, self.a_prev,
                  self.a_next, self.a_save):
            a.setEnabled(pg is not None)
        self.a_undo.setEnabled(bool(pg and pg.history))
        self._load_fields()

    def _load_fields(self):
        m = self.current_meta()
        self._loading_fields = True
        for name, e in self.edits.items():
            e.setEnabled(m is not None)
            val = getattr(m, name) if m else ""
            if e.text() != val:
                e.setText(val)
            auto = bool(m and val and name not in m.edited)
            if e.property("auto") != auto:
                e.setProperty("auto", auto)
                repolish(e)
            e.setToolTip("Lest fra arket – rett gjerne" if auto else "")
        book = self.split_mode == "book"
        self.field_labels["title"].setText("Boktittel" if book else "Tittel")
        self.song.setVisible(book)
        self.song_label.setVisible(book)
        if book:
            p = self.current()
            i = self.current_index()
            val, auto = "", False
            if p is not None:
                if p.section is not None:
                    val = p.section
                else:
                    s_, e_ = self._doc_of(i)
                    val = dict(self._sections(s_, e_)).get(i - s_, "")
                    auto = bool(val)
            self.song.setEnabled(p is not None)
            if self.song.text() != val:
                self.song.setText(val)
            if self.song.property("auto") != auto:
                self.song.setProperty("auto", auto)
                repolish(self.song)
        self._loading_fields = False
        self.b_save_doc.setEnabled(m is not None and self.save_thread is None)
        self._update_pill()
        self._update_name_preview()

    def _update_pill(self):
        m = self.current_meta()
        i = self.current_index()
        if not m:
            kind, text = "", ""
        elif any(p.ocr_state == "pending" for p in self.pages[slice(*self._doc_of(i))]):
            kind, text = "pill_info", "Leser …"
        elif m.saved:
            kind, text = "pill_ok", "✓ Lagret"
        else:
            kind, text = "pill_warn", "Endret" if m.saved_path else "Ikke lagret"
        self.pill.setText(text)
        self.pill.setVisible(bool(text))
        if self.pill.objectName() != kind:
            self.pill.setObjectName(kind)
            repolish(self.pill)

    def _field_edited(self, name: str):
        if self._loading_fields:
            return
        m = self.current_meta()
        if not m:
            return
        setattr(m, name, self.edits[name].text().strip())
        m.edited.add(name)
        m.bump()
        e = self.edits[name]
        e.setProperty("auto", False)
        repolish(e)
        e.setToolTip("")
        self._update_doc_item()
        self._update_pill()
        self._update_name_preview()
        self._schedule_session()

    def _song_edited(self, text: str):
        if self._loading_fields:
            return
        p = self.current()
        if p is None:
            return
        p.section = text.strip()
        self._touch(self.current_index())
        if self.song.property("auto"):
            self.song.setProperty("auto", False)
            repolish(self.song)
        self._update_section_labels()
        self._schedule_session()

    def _update_section_labels(self):
        """Oppdater bokmerkene i lista for dokumentet som vises, uten å bygge hele lista på nytt."""
        i = self.current_index()
        if i < 0:
            return
        s_, e_ = self._doc_of(i)
        secs = dict(self._sections(s_, e_))
        for k in range(s_, e_):
            it = self._find_item(self.pages[k].id)
            if it is not None:
                info = dict(it.data(0, ROLE_INFO) or {})
                info["section"] = secs.get(k - s_, "")
                it.setData(0, ROLE_INFO, info)
        self._update_doc_item()

    def _update_name_preview(self, *_):
        m = self.current_meta()
        if not m:
            self.name_preview.setText("")
            return
        self.name_preview.setText(f"Filnavn:  {self._filename(m, self._doc_no(self.current_index()))}.pdf")

    def _update_info(self):
        docs = self.documents()
        unsaved = sum(1 for s, _ in docs if not self.pages[s].meta.saved)
        self.count_lbl.setText(f"{len(docs)} dok · {len(self.pages)} sider" if self.pages else "")
        pend = sum(1 for p in self.pages if p.ocr_state == "pending")
        work = self.proc_thread.pending()
        bits = []
        if work:
            bits.append(f"behandler {work} side{'r' if work != 1 else ''}")
        if pend:
            bits.append(f"leser {pend} side{'r' if pend != 1 else ''}")
        self.busy_lbl.setText(("⏳ " + ", ".join(bits)) if bits else "")
        self.b_save_all.setLabel(f"Lagre alle ({unsaved})" if unsaved else "Lagre alle")
        self.b_save_all.setEnabled(bool(unsaved) and self.save_thread is None)
        self.setWindowTitle(f"NoteScanner – {len(docs)} dokument(er), {len(self.pages)} sider"
                            if self.pages else "NoteScanner")

    # ================================================================ nye sider
    def _opts(self) -> dict:
        return {"mode": self.mode, "enhance": self.auto_enhance.isChecked(), "strength": self.strength.value(),
                "deskew": self.auto_deskew.isChecked(), "crop": self.auto_crop.isChecked(),
                "margin": self.margin.value(), "despeckle": self.auto_despeckle.isChecked(),
                "skip_blank": self.skip_blank.isChecked(), "rotate": self.rotate.currentData(),
                "split_songs": self.split_songs.isChecked()}

    def add_page(self, img: np.ndarray, dpi: int, source: str = "", scan_s: float | None = None,
                 replace_id: int | None = None):
        """Legg en ny side i køen for behandling (skjer i bakgrunnen)."""
        o = self._opts()
        o["scan_s"] = scan_s
        o["area_name"] = self.area.currentText().split(" – ")[0] if self.area.currentData() != "full" else ""
        if replace_id is not None:
            o["replace_id"] = replace_id
            o["split_songs"] = False          # erstatter én side – ikke del den
        self.proc_thread.add(o, img=img, dpi=dpi, path=source)
        self._update_info()

    def _page_ready(self, res: dict):
        src = Path(res["source"]).name if res.get("source") else ""
        if res.get("error"):
            self.status.showMessage(res["error"], 8000)
            self._update_info()
            return
        if res.get("skipped"):
            self.status.showMessage(f"Siden {src + ' ' if src else ''}så tom ut og ble hoppet over.", 6000)
            self._update_info()
            return
        if res.get("replace_id") is not None and self._index(res["replace_id"]) >= 0:
            self._replace_page(res)
            return
        page = Page(res["png"], res["dpi"], thumb_arr=res["thumb"], history=res["history"], bw=res["bw"],
                    last_op=res["last_op"], orig=res.get("orig"))
        _cache_put(page.id, page.ver, res["arr"])
        if not self.pages:
            page.meta = DocMeta()
        self.pages.append(page)
        if self.auto_name.isChecked():
            self.ocr_thread.add(page.id, page.ocr_png(), page.dpi)
        else:
            page.ocr_state = "off"
            self._touch(len(self.pages) - 1)   # siden hører til siste dokument
        self._session_dirty.add(page.id)
        self.cur_id = page.id
        self._refresh_tree()
        msg = f"Side {len(self.pages)} lagt til" + (f" fra {src}" if src else "")
        if res.get("scan_s"):
            msg += f" – skannet på {res['scan_s']:.1f} s".replace(".", ",")
            if res.get("area_name"):
                msg += f" ({res['area_name']})"
        if res["notes"]:
            msg += " · " + ", ".join(res["notes"])
        self.status.showMessage(msg, 15000)
        if not src:
            self.raise_to_front()

    def rescan_page(self):
        """Neste skann erstatter siden du står på (samme plass, samme sangnavn) – f.eks. når beskjæringen
        ikke kan rettes fordi hele skannet mangler."""
        pg = self.current()
        if pg is None:
            return
        i = self._index(pg.id)
        s, _e = self._doc_of(i)
        self.status.showMessage(f"Skanner side {i - s + 1} på nytt – den gamle erstattes (Ctrl+Z angrer).", 8000)
        self.scan_page(replace_id=pg.id)

    def _replace_page(self, res: dict):
        pg = self.pages[self._index(res["replace_id"])]
        old = (pg.png, pg.bw, pg.last_op, False, pg.orig)
        raw = [(h[0], h[1], h[2], True, res.get("orig")) for h in res["history"]]   # én Ctrl+Z -> gamle siden
        pg.history = ([old] + raw)[-HISTORY_STEPS:]
        pg.png, pg.last_op, pg.orig = res["png"], res["last_op"], res.get("orig")
        pg.bw = pg.bw or res["bw"]
        pg.ver += 1
        _cache_put(pg.id, pg.ver, res["arr"])
        pg.thumb_arr, pg.thumb_pm = res["thumb"], None
        if pg.section is None and self.auto_name.isChecked():   # navnet du har skrevet selv beholdes
            pg.ocr_state = "pending"
            self.ocr_thread.add(pg.id, pg.ocr_png(), pg.dpi)
        self.cur_id = pg.id
        self._after_image_change(pg)
        self._refresh_tree()
        self.status.showMessage("Siden er erstattet med det nye skannet." + (" · " + ", ".join(res["notes"])
                                if res.get("notes") else ""), 8000)

    def _ocr_result(self, page_id: int, sug):
        i = self._index(page_id)
        if i < 0:
            return
        p = self.pages[i]
        first = p.sug is None              # første lesing av en ny side (ikke «les på nytt»)
        p.sug = sug
        p.ocr_state = "done"
        msg = ""
        if sug.engine.startswith("feil"):
            msg = f"Tekstlesing feilet ({sug.engine[6:90]})"
            logged = self.__dict__.setdefault("_ocr_errors_logged", set())
            if sug.engine not in logged and len(logged) < 20:      # samme feil logges bare én gang per økt
                logged.add(sug.engine)
                try:
                    paths.error_log(f"Tekstlesing feilet: {sug.engine[6:]}\n", __version__)
                except Exception:  # noqa: BLE001
                    pass
        was_start = p.meta is not None
        self._regroup(fresh_id=page_id if first else None)
        if p.meta is None and first:
            self._touch(i)          # ny side ble en del av dokumentet over – det må lagres på nytt
        if p.meta is not None:
            if p.meta.fill_from(sug):
                p.meta.bump()
            if not was_start and i > 0:
                msg = msg or f"Nytt dokument: {p.meta.title or '(uten tittel)'}"
        elif self.split_mode == "book" and sug.is_title_page and sug.title:
            msg = msg or f"Bokmerke: {sug.title}"
        self._refresh_tree()
        if msg:
            self.status.showMessage(msg, 7000)

    def reread_name(self):
        """Les tittelfeltet på siden som vises på nytt og fyll inn dokumentet."""
        p = self.current()
        if not p:
            return
        if not ocr.available_engine():
            QMessageBox.information(self, "Les navn", "Fant ingen tekstlesing. Se LES_MEG.md.")
            return
        self.status.showMessage("Leser tittelfeltet …")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            sug = ocr.read_header(proc.decode(p.ocr_png()), p.dpi)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self.status.showMessage(f"Lesing feilet: {e}", 8000)
            return
        QApplication.restoreOverrideCursor()
        p.sug = sug
        p.ocr_state = "done"
        p.section = None        # bruk det som ble lest
        if sug.engine.startswith("feil"):
            self._refresh_tree()
            self.status.showMessage(f"Tekstlesing feilet: {sug.engine[6:120]}", 10000)
            return
        i = self.current_index()
        s, _ = self._doc_of(i)
        if i == s:              # første side i dokumentet: fyll inn navnet
            m = self.pages[s].meta
            m.edited.clear()    # du ba selv om ny lesing
            m.fill_from(sug)
            m.bump()
        else:                   # side inni et dokument (f.eks. i en bok): bare oppdeling og bokmerker
            self._regroup()
        self._refresh_tree()
        self.status.showMessage(f"Leste: {sug.title or '–'} / {sug.part or '–'} / {sug.composer or '–'}"
                                + ("  – ny tittel" if sug.is_title_page and i != s else ""), 8000)

    def reread_all(self):
        """Les tittelfeltet på alle sidene på nytt (f.eks. etter en oppdatering av tekstlesingen)."""
        if not self.pages or not ocr.available_engine():
            return
        for p in self.pages:
            p.ocr_state = "pending"
            self.ocr_thread.add(p.id, p.ocr_png(), p.dpi)
        self._refresh_tree()
        self.status.showMessage(f"Leser {len(self.pages)} side(r) på nytt …", 6000)

    def raise_to_front(self):
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    # ================================================================ gruppering for hånd
    def split_here(self):
        i = self.current_index()
        if i <= 0:
            return
        p = self.pages[i]
        if p.meta is not None:
            self.status.showMessage("Siden starter allerede et dokument.", 4000)
            return
        self._touch(i)
        p.meta = DocMeta()
        p.meta.fill_from(p.sug)
        p.manual = True
        self._refresh_tree()

    def merge_with_previous(self):
        i = self.current_index()
        if i < 0:
            return
        s, _ = self._doc_of(i)
        if s == 0:
            self.status.showMessage("Dette er det første dokumentet.", 4000)
            return
        prev = self.pages[self._doc_of(s - 1)[0]].meta
        cur = self.pages[s].meta
        for f in FIELDS:
            if not getattr(prev, f) and getattr(cur, f):
                setattr(prev, f, getattr(cur, f))
        prev.bump()
        self.pages[s].meta = None
        self.pages[s].manual = True
        self._refresh_tree()

    def move_page(self, direction: int):
        i = self.current_index()
        j = i + direction
        if i < 0 or not (0 <= j < len(self.pages)):
            return
        P = self.pages
        self._touch(i)
        if direction < 0 and P[i].meta is not None:
            m = P[i].meta          # øverste side i et dokument flyttes opp -> blir siste side i dokumentet over
            P[i].meta = None
            if i + 1 < len(P) and P[i + 1].meta is None:
                P[i + 1].meta = m
                P[i + 1].manual = True
        elif direction > 0 and P[j].meta is not None:
            # nederste side flyttes ned -> blir første side i neste dokument (som beholder navnet sitt).
            # Var det et dokument med bare denne siden, forsvinner det dokumentet.
            P[i].meta, P[j].meta = P[j].meta, None
        else:
            P[i].meta, P[j].meta = P[j].meta, P[i].meta
            P[i], P[j] = P[j], P[i]
        P[i].manual = P[j].manual = True
        self._touch(j)
        self._refresh_tree()

    def delete_page(self):
        i = self.current_index()
        if i < 0:
            return
        p = self.pages[i]
        self._touch(i)
        if p.meta is not None and i + 1 < len(self.pages) and self.pages[i + 1].meta is None:
            self.pages[i + 1].meta = p.meta
            self.pages[i + 1].manual = True     # ellers kan tekstlesingen slå dokumentet sammen med det over
        del self.pages[i]
        _DECODED.pop(p.id, None)
        self.cur_id = self.pages[min(i, len(self.pages) - 1)].id if self.pages else None
        self._refresh_tree()

    def join_with_previous_image(self):
        """Lim denne siden under siden over til ett bilde (når appen har delt én bokside i to ved en feil)."""
        i = self.current_index()
        if i <= 0:
            return
        top, bot = self.pages[i - 1], self.pages[i]

        def stack(a, b):
            if a.ndim != b.ndim:
                a, b = (proc.to_gray(a), proc.to_gray(b)) if a.ndim == 3 or b.ndim == 3 else (a, b)
            w = max(a.shape[1], b.shape[1])
            pad = lambda x: cv2.copyMakeBorder(x, 0, 0, 0, w - x.shape[1], cv2.BORDER_CONSTANT,   # noqa: E731
                                               value=(255, 255, 255) if x.ndim == 3 else 255)
            return np.vstack([pad(a), pad(b)])

        orig = None
        enhanced = top.last_op == "enhance" or top.bw or self.auto_enhance.isChecked()
        if top.orig is not None and bot.orig is not None:
            # lim hele skannene og beskjær/forbedre på nytt – da blir det ingen dobbel marg i skjøten
            full = stack(proc.decode(top.orig), proc.decode(bot.orig))
            orig = proc.encode_jpg(full, 92)
            raw, _bb = proc.auto_crop(full, top.dpi, self.margin.value())
            top.push(raw)
            if enhanced:
                bw = top.bw
                top.push(proc.enhance(raw, top.dpi, "bw" if bw else self.mode, self.strength.value()), join=True)
                top.bw, top.last_op = bw, "enhance"
        else:
            top.push(stack(top.img, bot.img))
            top.last_op = ""
        top.orig = orig
        if top.section is None and bot.section:
            top.section = bot.section
        if bot.meta is not None and i + 1 < len(self.pages) and self.pages[i + 1].meta is None:
            self.pages[i + 1].meta = bot.meta      # resten av dokumentet beholder navnet sitt
            self.pages[i + 1].manual = True
        self._touch(i)
        del self.pages[i]
        _DECODED.pop(bot.id, None)
        self.cur_id = top.id
        self._after_image_change(top)
        self._refresh_tree()
        self.status.showMessage("Sidene er limt sammen til én. Ctrl+Z på siden angrer bildet "
                                "(den nederste siden kommer ikke tilbake).", 8000)

    def new_session(self):
        unsaved = self._unsaved_docs()
        if unsaved and QMessageBox.question(self, "Tøm alt", f"{len(unsaved)} dokument(er) er ikke lagret. "
                                            "Fjerne alt likevel?") != QMessageBox.Yes:
            return
        self.pages.clear()
        _DECODED.clear()
        self.cur_id = None
        self._refresh_tree()

    # ================================================================ bilderedigering
    def _replace_current(self, new_img):
        pg = self.current()
        if pg is None:
            return
        pg.push(new_img)
        self._after_image_change(pg)

    def _after_image_change(self, pg: Page):
        self._touch(self._index(pg.id))
        self._refresh_thumb(pg)
        if pg is self.current():
            self.view.set_image(pg.img)
            self.a_undo.setEnabled(bool(pg.history))
        self._session_dirty.add(pg.id)
        self._update_doc_item()
        self._update_pill()
        self._schedule_session()

    def _crop_page(self, pg: Page, rect, out: np.ndarray | None = None):
        """Beskjær siden. Er den forbedret, beskjæres også bildet før forbedringen, så Svarthet fortsatt
        kan justeres etterpå."""
        if out is None:
            out = proc.crop(pg.img, rect)
        if pg.last_op == "enhance" and pg.history:
            raw = pg.before_enhance()
            if raw.shape[:2] == pg.img.shape[:2]:
                bw = pg.bw
                pg.push(proc.crop(raw, rect))
                pg.bw = bw
                pg.push(out, join=True)
                pg.last_op = "enhance"
                self._after_image_change(pg)
                self._cropped_msg()
                return
        pg.push(out)
        self._after_image_change(pg)
        self._cropped_msg()

    def _cropped_msg(self):
        self.status.showMessage("Beskåret. Ctrl+Z angrer, O henter fram hele skannet.", 5000)

    def do_autocrop(self):
        pg = self.current()
        if not pg:
            return
        img, bb = proc.auto_crop(pg.img, pg.dpi, self.margin.value())
        if bb is None:
            self.status.showMessage("Fant ikke noe innhold å beskjære til.", 5000)
            return
        self._crop_page(pg, bb, img)

    def do_crop_selection(self):
        pg = self.current()
        sel = self.view.selection()
        if pg and sel:
            x0, y0, x1, y1 = sel
            self._crop_page(pg, (x0, y0, x1 + 1, y1 + 1))

    def do_reset_crop(self):
        """Tilbake til hele skannet (rettet opp, men ikke beskåret). Forbedringen gjøres på nytt."""
        pg = self.current()
        if not pg:
            return
        if pg.orig is None:
            QMessageBox.information(
                self, "Tilbakestill beskjæring",
                "Hele skannet finnes ikke for denne siden – den ble skannet med en eldre versjon, så appen har "
                "bare den beskårne siden.\n\nDu kan fortsatt dra med musa for å beskjære den mindre.\n\n"
                "Mangler det noe av siden, legg den på glasset og trykk «Skann siden på nytt» (Shift+mellomrom). "
                "Det nye skannet erstatter siden på samme plass, med samme sangnavn.")
            return
        raw = proc.decode(pg.orig)
        bw = pg.bw
        if raw.ndim == 3 and self.mode != "color" and proc.is_grayish(raw):
            raw = proc.to_gray(raw)
        pg.push(raw)
        if pg.last_op != "" or self.auto_enhance.isChecked() or bw:
            pg.bw = bw
            pg.push(proc.enhance(raw, pg.dpi, "bw" if bw else self.mode, self.strength.value()), join=True)
            pg.bw = bw
            pg.last_op = "enhance"
        self._after_image_change(pg)
        self.status.showMessage("Hele skannet er hentet fram igjen. Dra med musa for å beskjære, eller trykk A.", 6000)

    def do_deskew(self):
        pg = self.current()
        if pg:
            img, ang = proc.deskew(pg.img, pg.dpi)
            self._replace_current(img)
            self.status.showMessage(f"Rettet opp {ang:+.2f}°", 5000)

    def do_rot90(self, cw: bool):
        pg = self.current()
        if pg:
            self._replace_current(proc.rotate90(pg.img, cw))

    def _enhance_page(self, pg: Page):
        mode = self.mode
        out = proc.enhance(pg.before_enhance(), pg.dpi, mode, self.strength.value())
        if pg.last_op == "enhance" and pg.history:
            pg.replace(out)          # allerede forbedret: juster fra bildet før, ikke forbedre to ganger
        else:
            pg.push(out)
            pg.last_op = "enhance"
        pg.bw = mode == "bw"

    def do_enhance(self):
        pg = self.current()
        if pg:
            self._enhance_page(pg)
            self._after_image_change(pg)

    def _reenhance_current(self, *_):
        pg = self.current()
        if pg and pg.last_op == "enhance":
            self.do_enhance()

    def apply_to_all(self):
        if not self.pages:
            return
        dlg = QProgressDialog("Forbedrer sidene …", "Avbryt", 0, len(self.pages), self)
        dlg.setWindowTitle("Bruk på alle sider")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        for k, pg in enumerate(list(self.pages)):
            if dlg.wasCanceled():
                break
            self._enhance_page(pg)
            self._touch(k)
            self._session_dirty.add(pg.id)
            dlg.setValue(k + 1)
            QApplication.processEvents()
        dlg.close()
        self._refresh_tree()

    def do_despeckle(self):
        pg = self.current()
        if pg:
            self._replace_current(proc.despeckle(pg.img, pg.dpi))

    def do_undo(self):
        pg = self.current()
        if pg and pg.undo():
            self._after_image_change(pg)

    # ================================================================ skanning
    def scan_page(self, device_id: str | None = None, replace_id: int | None = None):
        if self._thread is not None:
            self._scan_queue.append(replace_id)   # trykket igjen mens den skanner – ta neste etterpå
            return
        if not scanner.available():
            QMessageBox.information(self, "Skanning", "Direkte skanning fungerer bare på Windows.\n"
                                    "Bruk «Importer …» eller en overvåket mappe.")
            return
        dpi = int(self.dpi.currentData())
        mode = "color" if self.mode == "color" else "gray"   # svart-hvitt skannes i gråtone og gjøres om etterpå
        self.status.showMessage("Skanner …")
        self.b_scan.setLabel("Skanner …")
        SCAN_IDLE.clear()   # bakgrunnsarbeid venter til skanneren er ferdig
        self._thread = QThread(self)
        chosen = self.scanner_box.currentData() or None
        self._worker = ScanWorker(dpi, mode, device_id or self._wia_device or chosen, self._area_mm())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._scan_dpi = dpi
        self._active_replace = replace_id      # hører til akkurat denne skanningen
        self._worker.done.connect(self._scan_finished)
        self._worker.failed.connect(self._scan_failed)
        self._thread.start()
        token = self._thread
        QTimer.singleShot(SCAN_TIMEOUT_S * 1000, lambda: self._scan_timeout(token))

    def _scan_timeout(self, token):
        """Driveren har hengt seg (kan ikke avbrytes). Gi slipp på skanningen så appen kan brukes videre."""
        if self._thread is not token:
            return
        try:
            self._worker.done.disconnect()
            self._worker.failed.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._abandoned.append((self._thread, self._worker))   # holdes i live; appen avsluttes hardt ved lukking
        self._thread = None
        self._active_replace = None
        self._scan_queue.clear()
        SCAN_IDLE.set()
        self.b_scan.setLabel("Skann side")
        self.status.clearMessage()
        QMessageBox.warning(self, "Skanneren svarer ikke",
                            f"Skanneren har ikke svart på {SCAN_TIMEOUT_S // 60} minutter.\n\nSlå skanneren av og på "
                            "(eller trekk ut USB-kabelen og sett den i igjen) og prøv på nytt. Virker det fortsatt "
                            "ikke, start NoteScanner på nytt.")

    def _end_scan_thread(self):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        SCAN_IDLE.set()
        self.b_scan.setLabel("Skann side")
        if self._scan_queue:
            rid = self._scan_queue.pop(0)
            QTimer.singleShot(200, lambda: self.scan_page(replace_id=rid))

    def _scan_finished(self, img, secs: float):
        rid, self._active_replace = self._active_replace, None   # før neste skanning i køen starter
        self._end_scan_thread()
        dpi = self._actual_dpi(img)
        self._log_scan_time(img, secs, dpi)
        if dpi != self._scan_dpi:
            self.status.showMessage(f"Skanneren brukte {dpi} dpi, ikke {self._scan_dpi} – se skannelogg.txt.", 10000)
        self.add_page(img, dpi, scan_s=secs, replace_id=rid)

    def _actual_dpi(self, img) -> int:
        """Oppløsningen skanneren faktisk brukte (noen drivere overstyrer det appen ber om)."""
        t = scanner.last_timing
        ok = lambda v: isinstance(v, int) and 50 <= v <= 2400   # noqa: E731
        rd, fd = t.get("dpi_read"), t.get("dpi_file")
        ew = (t.get("ext_wanted") or (None, None))[0]
        if ew and abs(img.shape[1] - ew) <= 2 and ok(rd):
            return rd
        if ok(fd):
            return fd
        if ok(rd):
            return rd
        return self._scan_dpi

    # LiDE 300 ifølge Canon: millisekunder per linje (uten overføring) – brukes til å sjekke farten
    _SPEC_MS = {("gray", 300): 2.2, ("gray", 600): 1.3, ("gray", 1200): 2.2,
                ("color", 300): 2.2, ("color", 600): 3.8, ("color", 1200): 6.6}

    def _log_scan_time(self, img, secs: float, dpi: int | None = None):
        """Skriv tidsbruken til skannelogg.txt, så vi kan se hva som tar tid (oppkobling, hodet, overføring)."""
        t = dict(scanner.last_timing)
        dpi, mode = dpi or self._scan_dpi, ("color" if img.ndim == 3 else "gray")
        lines = t.get("lines") or img.shape[0]
        mm = lines / dpi * 25.4
        spec = self._SPEC_MS.get((mode, dpi))
        exp = f"{spec * lines / 1000:.1f}" if spec else "?"
        line = (f"{datetime.now():%Y-%m-%d %H:%M:%S}  v{APP_VERSION}  {dpi} dpi {mode:5}  "
                f"{img.shape[1]}x{img.shape[0]} px ({mm:.0f} mm lang)  område={self.area.currentText().split(" – ")[0]}  "
                f"totalt {secs:.1f} s = oppkobling {t.get('connect', 0):.1f} + skann {t.get('transfer', 0):.1f} "
                f"+ kopi {t.get('copy', 0):.1f} + dekoding {t.get('decode', 0):.1f}  "
                f"({t.get('fmt', '?')}, {t.get('bytes', 0) / 1e6:.0f} MB)  "
                f"hodet etter Canons tall: ca. {exp} s  |  dpi: valgt {self._scan_dpi}, skanner sier "
                f"{t.get('dpi_read')}/{t.get('vres_read')}, i fila {t.get('dpi_file')}, lovlige {t.get('res_valid')}, "
                f"maks {t.get('max_raw')}, utsnitt {t.get('ext')} (ønsket {t.get('ext_wanted')})  "
                f"skanner={self._scanner_name}\n")
        paths.append_log("skannelogg.txt", line)

    def _scan_failed(self, msg):
        self._active_replace = None
        self._wia_device = None        # skanneren som startet appen kan være koblet fra – bruk valget i lista
        self._scan_queue.clear()       # ikke fortsett med flere skanninger etter en feil
        self._end_scan_thread()
        self.status.clearMessage()
        QMessageBox.warning(self, "Skanning feilet", msg)

    # ================================================================ import / overvåket mappe
    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Importer skannede bilder", self.watchdir.text(),
                                                "Bilder (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        for fpath in sorted(files):
            self.import_file(fpath)

    def import_file(self, path: str):
        # Oppløsningen som står i fila (Brother iPrint&Scan, IJ Scan Utility), ellers den som er valgt i appen
        dpi = file_dpi(path) or int(self.dpi.currentData())
        self.proc_thread.add(self._opts(), dpi=dpi, path=path)
        self._update_info()

    def _prime_watch_folder(self):
        """Ta et øyeblikksbilde av mappa så bare NYE filer blir importert."""
        self._done_files: set[str] = set()
        self._pending_sizes: dict[str, int] = {}
        d = self.watchdir.text()
        if d and os.path.isdir(d):
            self._done_files = {str(p) for p in Path(d).iterdir()}

    def _poll_watch_folder(self):
        d = self.watchdir.text()
        if not d or not os.path.isdir(d):
            return
        try:
            entries = sorted(Path(d).iterdir())
        except OSError:
            return          # mappa er ikke tilgjengelig akkurat nå (nettverk, rettigheter) – prøv igjen senere
        for p in entries:
            key = str(p)
            if key in self._done_files or p.suffix.lower() not in IMG_EXT:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > 0 and self._pending_sizes.get(key) == size:   # ferdigskrevet
                self._done_files.add(key)
                self._pending_sizes.pop(key, None)
                self.import_file(key)
            else:
                self._pending_sizes[key] = size

    # ================================================================ lagring (i bakgrunnen)
    def _unsaved_docs(self):
        return [(s, e) for s, e in self.documents() if not self.pages[s].meta.saved]

    def _target_path(self, m: DocMeta, doc_no: int, out: Path, taken: set) -> Path:
        base = self._filename(m, doc_no)
        path = out / f"{base}.pdf"
        # Samme dokument lagret før med samme navn -> skriv over. Ellers finn et ledig navn.
        # (men ikke hvis et annet dokument i samme lagring alt har fått navnet)
        if not (m.saved_path and Path(m.saved_path) == path) or str(path).lower() in taken:
            k = 2
            while path.exists() or str(path).lower() in taken:
                path = out / f"{base} ({k}).pdf"
                k += 1
        taken.add(str(path).lower())
        return path

    def _confirm_pending(self, s=0, e=None) -> bool:
        pend = [p for p in self.pages[s:e] if p.ocr_state == "pending"]
        if not pend and not self.proc_thread.pending():
            return True
        return QMessageBox.question(
            self, "Ikke ferdig ennå", f"Appen jobber fortsatt med {len(pend) + self.proc_thread.pending()} side(r), "
            "og oppdelingen kan endre seg.\n\nLagre likevel?") == QMessageBox.Yes

    def save_current_doc(self):
        i = self.current_index()
        if i < 0:
            return
        s, e = self._doc_of(i)
        pid = self.pages[i].id
        if self._confirm_pending(s, e):
            i = self._index(pid)          # oppdelingen kan ha endret seg mens spørsmålet sto åpent
            if i >= 0:
                self._start_save([self._doc_of(i)])

    def save_all(self):
        if not self._unsaved_docs():
            self.status.showMessage("Alt er allerede lagret.", 5000)
            return
        if self._confirm_pending():
            todo = self._unsaved_docs()   # regnes ut på nytt – noe kan ha blitt ferdig i mellomtiden
            if todo:
                self._start_save(todo)

    def _start_save(self, ranges):
        if self.save_thread is not None:
            self.status.showMessage("Lagrer allerede – vent litt.", 4000)
            return
        out = Path(self.outdir.text() or Path.home())
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Lagring", f"Kan ikke lage mappa {out}:\n{e}")
            return
        docs = self.documents()
        taken: set = set()
        jobs = []
        for s, e in ranges:
            m = self.pages[s].meta
            path = self._target_path(m, (docs.index((s, e)) + 1) if (s, e) in docs else 1, out, taken)
            jobs.append({"path": str(path), "name": path.name,
                         "pages": [(p.png, p.dpi, p.bw) for p in self.pages[s:e]],
                         "title": m.title, "author": m.composer, "subject": m.part,
                         "bookmarks": self._sections(s, e), "meta": m, "rev": m.rev})
        self._save_jobs, self._save_errors, self._saved_names = jobs, [], []
        layout = ["a4", "a4fill", "native"][self.pagesize.currentIndex()]
        self.save_thread = SaveThread(jobs, layout, self)
        self.save_thread.progress.connect(self._save_progress)
        self.save_thread.job_done.connect(self._save_job_done)
        self.save_thread.finished.connect(self._save_finished)
        total = sum(len(j["pages"]) for j in jobs)
        self.prog.setRange(0, total)
        self.prog.setValue(0)
        self.prog.show()
        self.prog_lbl.show()
        self.prog_lbl.setText(f"Lagrer {len(jobs)} dokument(er) …")
        self.b_save_doc.setEnabled(False)
        self.b_save_all.setEnabled(False)
        self.save_thread.start(QThread.LowPriority)

    def _save_progress(self, done: int, total: int, name: str):
        self.prog.setValue(done)
        self.prog_lbl.setText(f"Lagrer {name} · {done} av {total} sider")

    def _save_job_done(self, k: int, path: str, err: str):
        j = self._save_jobs[k]
        if path:
            j["meta"].saved_path = path
            j["meta"].saved_rev = j["rev"]   # endret du noe mens den lagret, står dokumentet som ulagret
            self._saved_names.append(Path(path).name)
        else:
            self._save_errors.append(f"{j['name']}: {err}")

    def _save_finished(self):
        self.save_thread = None
        self.prog.hide()
        self.prog_lbl.hide()
        n = len(self._saved_names)
        if n == 1:
            self.status.showMessage(f"Lagret {self._saved_names[0]} i {self.outdir.text()}", 12000)
        elif n:
            self.status.showMessage(f"Lagret {n} dokumenter i {self.outdir.text()}", 12000)
        if self._save_errors:
            QMessageBox.warning(self, "Noe ble ikke lagret", "\n".join(self._save_errors))
        self._save_jobs = []
        if self.clear_after.isChecked():
            self.remove_saved()
        else:
            self._refresh_tree()

    def remove_saved(self):
        """Flytt lagrede dokumenter ut av lista og inn i «Lagret nylig» (på disk, ikke i minnet)."""
        keep, moved = [], 0
        for s, e in self.documents():
            if self.pages[s].meta.saved:
                if self._archive_doc(self.pages[s:e]):
                    moved += 1
                    continue
            keep.extend(self.pages[s:e])
        removed = len(self.pages) - len(keep)
        old_i = max(0, self.current_index())
        self.pages = keep
        if self.current_index() < 0:
            self.cur_id = self.pages[min(old_i, len(self.pages) - 1)].id if self.pages else None
        self._refresh_tree()
        self._refresh_archive()
        if removed:
            self.status.showMessage(f"{moved} lagret(e) dokument(er) flyttet til «Lagret nylig» "
                                    f"({removed} side(r)).", 8000)

    # ================================================================ lagret nylig (arkiv på disk)
    @staticmethod
    def _page_json(p: Page) -> dict:
        return {"id": p.id, "dpi": p.dpi, "bw": p.bw, "manual": p.manual, "ocr_state": p.ocr_state,
                "section": p.section, "meta": p.meta.to_json() if p.meta else None,
                "sug": ({k: getattr(p.sug, k) for k in ("title", "part", "composer", "engine")}
                        | {"header_mm": float(p.sug.header_mm), "title_mm": float(p.sug.title_mm),
                           "staff_mm": float(getattr(p.sug, "staff_mm", 0.0))}) if p.sug is not None else None}

    def _archive_doc(self, pages: list[Page]) -> bool:
        m = pages[0].meta
        d = self.archive_dir / f"{int(time.time() * 1000)}_{pages[0].id}"
        try:
            d.mkdir(parents=True, exist_ok=True)
            for k, p in enumerate(pages):
                (d / f"p{k}.png").write_bytes(p.png)
                if p.orig is not None:
                    (d / f"o{k}.jpg").write_bytes(p.orig)
                if p.thumb_arr is not None:
                    (d / f"t{k}.png").write_bytes(proc.encode_png(p.thumb_arr))
            s = self.pages.index(pages[0])
            info = {"version": 1, "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "title": m.title, "part": m.part, "composer": m.composer, "path": m.saved_path or "",
                    "split_mode": self.split_mode,
                    "sections": [list(x) for x in self._sections(s, s + len(pages))],
                    "pages": [self._page_json(p) for p in pages]}
            (d / "info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            return True
        except (OSError, ValueError) as e:
            shutil.rmtree(d, ignore_errors=True)
            self.status.showMessage(f"Klarte ikke å flytte til «Lagret nylig»: {e}", 10000)
            return False

    def _archive_entries(self) -> list[tuple[Path, dict]]:
        out = []
        if self.archive_dir.is_dir():
            for d in self.archive_dir.iterdir():
                try:
                    out.append((d, json.loads((d / "info.json").read_text(encoding="utf-8"))))
                except (OSError, ValueError):
                    continue
        out.sort(key=lambda x: x[1].get("saved_at", ""), reverse=True)
        return out

    def _purge_archive(self):
        """Slett det som har ligget i «Lagret nylig» lenger enn ARCHIVE_DAYS dager (PDF-ene blir liggende),
        og forkastede/uleselige økter like gamle."""
        now = datetime.now()
        try:
            olds = list((paths.data_dir() / "forkastet").glob("*")) + list(paths.data_dir().glob("session-uleselig-*"))
        except OSError:
            olds = []
        for d in olds:
            try:
                if (now - datetime.fromtimestamp(d.stat().st_mtime)).days >= ARCHIVE_DAYS:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
        if not self.archive_dir.is_dir():
            return
        for d in self.archive_dir.iterdir():
            try:
                info = json.loads((d / "info.json").read_text(encoding="utf-8"))
                age = (now - datetime.fromisoformat(info["saved_at"])).days
            except (OSError, ValueError, KeyError):
                age = (now - datetime.fromtimestamp(d.stat().st_mtime)).days if d.exists() else 0
            if age >= ARCHIVE_DAYS:
                shutil.rmtree(d, ignore_errors=True)

    def _refresh_archive(self):
        entries = self._archive_entries()
        self.arch_list.clear()
        for d, info in entries:
            n = len(info.get("pages") or [])
            try:
                when = datetime.fromisoformat(info["saved_at"])
                when_s = when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%d.%m. %H:%M")
            except (KeyError, ValueError):
                when_s = ""
            title = info.get("title") or Path(info.get("path") or "").stem or "Uten tittel"
            sub = " · ".join(x for x in (f"lagret {when_s}", f"{n} side" + ("r" if n != 1 else ""),
                                         info.get("part", "")) if x)
            it = QListWidgetItem(f"{title}\n{sub}")
            it.setData(Qt.UserRole, str(d))
            it.setToolTip(info.get("path") or "")
            t = d / "t0.png"
            if t.exists():
                try:
                    it.setIcon(QIcon(QPixmap.fromImage(to_qimage(proc.decode(t.read_bytes())))))
                except RuntimeError:
                    pass
            self.arch_list.addItem(it)
        self.arch_head.setText(f"Lagret nylig ({len(entries)})")
        self.arch_head.setIcon(icons.icon("chev_down" if self.arch_body.isVisible() else "chev_right",
                                          C["text"], 14, stroke=2.2))
        self._arch_buttons()

    def _show_archive(self, on: bool):
        self.arch_body.setVisible(on)
        self.arch_head.setIcon(icons.icon("chev_down" if on else "chev_right", C["text"], 14, stroke=2.2))

    def _arch_buttons(self):
        it = self.arch_list.currentItem()
        for b in (self.b_arch_open, self.b_arch_back, self.b_arch_del):
            b.setEnabled(it is not None)

    def _arch_current(self):
        it = self.arch_list.currentItem()
        if it is None:
            return None, None
        d = Path(it.data(Qt.UserRole))
        try:
            return d, json.loads((d / "info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None

    def open_archived(self):
        _d, info = self._arch_current()
        if not info:
            return
        path = info.get("path") or ""
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self.status.showMessage(f"Finner ikke PDF-en {path} – er den flyttet eller slettet?", 8000)

    def restore_archived(self):
        d, info = self._arch_current()
        if not info:
            return
        items = info.get("pages") or []
        new: list[Page] = []
        bad = 0
        for k, it in enumerate(items):
            try:
                png = (d / f"p{k}.png").read_bytes()
                proc.decode(png)                      # sjekk at siden faktisk kan leses før noe slettes
            except (OSError, RuntimeError, ValueError, cv2.error):
                bad += 1
                continue
            o = d / f"o{k}.jpg"
            try:
                orig = o.read_bytes() if o.exists() else None
            except OSError:
                orig = None
            p = Page(png, self._safe_int(it.get("dpi"), 300), bw=bool(it.get("bw")), manual=True,
                     section=it.get("section"), orig=orig)
            t = d / f"t{k}.png"
            if t.exists():
                try:
                    p.thumb_arr = proc.decode(t.read_bytes())
                except (OSError, RuntimeError, ValueError, cv2.error):
                    pass
            try:
                p.meta = DocMeta.from_json(it["meta"]) if it.get("meta") else None
            except (TypeError, ValueError, KeyError):
                p.meta = None
            if it.get("sug"):
                try:
                    p.sug = _suggestion_from_json(it["sug"])
                except (TypeError, ValueError):
                    p.sug = None
            p.ocr_state = "off" if it.get("ocr_state") == "off" else "done"
            self._session_dirty.add(p.id)
            new.append(p)
        if not new:
            QMessageBox.warning(self, "Hent tilbake", "Fant ikke sidene til dette dokumentet. Det blir liggende i "
                                "«Lagret nylig»; PDF-en kan åpnes som før.")
            return
        if bad and QMessageBox.question(
                self, "Hent tilbake", f"{bad} av {len(items)} side(r) kunne ikke leses.\n\nHente tilbake de "
                f"{len(new)} andre? (Dokumentet blir også liggende i «Lagret nylig».)") != QMessageBox.Yes:
            return
        new[0].meta = new[0].meta or DocMeta(title=info.get("title", ""))
        self.pages.extend(new)
        self.cur_id = new[0].id
        self._refresh_tree()
        # slett fra «Lagret nylig» først når sidene ligger trygt i økta
        self._flush_session()
        if not bad and all((self.session_dir / f"p{p.id}.png").exists() for p in new):
            shutil.rmtree(d, ignore_errors=True)
        self._refresh_archive()
        extra = " Det ligger som eget dokument, også i «Én bok»." if self.split_mode == "book" else ""
        self.status.showMessage(f"«{info.get('title') or 'Dokumentet'}» er tilbake i lista. Lagrer du igjen, "
                                "overskrives samme PDF." + extra, 8000)

    @staticmethod
    def _safe_int(v, default: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def delete_archived(self):
        d, info = self._arch_current()
        if d is None:
            return
        shutil.rmtree(d, ignore_errors=True)
        self._refresh_archive()
        self.status.showMessage("Fjernet fra «Lagret nylig». PDF-filen er ikke slettet.", 6000)

    # ================================================================ økt (autolagring)
    def _schedule_session(self):
        self._session_timer.start(800)

    def _flush_session(self):
        if not self._session_ready:
            return
        try:
            d = self.session_dir
            d.mkdir(parents=True, exist_ok=True)
            for p in self.pages:
                f = d / f"p{p.id}.png"
                if p.id in self._session_dirty or not f.exists():
                    _atomic_write(f, p.png)
                    if p.orig is not None:
                        _atomic_write(d / f"o{p.id}.jpg", p.orig)
                    if p.thumb_arr is not None:
                        _atomic_write(d / f"t{p.id}.png", proc.encode_png(p.thumb_arr))
            self._session_dirty.clear()
            data = {"version": 2, "current": self.cur_id, "split_mode": self.split_mode, "pages": [{
                "id": p.id, "dpi": p.dpi, "bw": p.bw, "manual": p.manual, "ocr_state": p.ocr_state,
                "section": p.section,
                "meta": p.meta.to_json() if p.meta else None,
                "sug": ({k: getattr(p.sug, k) for k in ("title", "part", "composer", "engine")}
                        | {"header_mm": float(p.sug.header_mm), "title_mm": float(p.sug.title_mm),
                           "staff_mm": float(getattr(p.sug, "staff_mm", 0.0))})
                if p.sug is not None else None} for p in self.pages]}
            _atomic_write(d / "session.json", json.dumps(data, ensure_ascii=False).encode("utf-8"))
            wanted = {f"p{p.id}.png" for p in self.pages} | {f"t{p.id}.png" for p in self.pages} | \
                {f"o{p.id}.jpg" for p in self.pages if p.orig is not None} | {"session.json"}
            for f in d.iterdir():
                if f.name not in wanted:
                    f.unlink(missing_ok=True)
        except OSError as e:
            self.status.showMessage(f"Klarte ikke å autolagre: {e}", 6000)

    def _discard_session(self):
        """«Nei» til å hente fram forrige økt: flytt den til side (slettes etter ARCHIVE_DAYS) i stedet for å slette."""
        if not self.session_dir.exists() or self.pages:
            return
        try:
            dst = paths.data_dir() / "forkastet" / datetime.now().strftime("%Y%m%d-%H%M%S")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.session_dir), str(dst))
        except OSError:
            pass

    def _offer_restore(self):
        try:
            self._restore()
        except Exception:  # noqa: BLE001
            # Økta kunne ikke leses (f.eks. laget av en nyere versjon). Flytt den til side i stedet for å la
            # autolagringen skrive over den – den kan hentes fram igjen for hånd.
            import traceback
            paths.error_log("Klarte ikke å hente fram forrige økt:\n" + traceback.format_exc(), APP_VERSION)
            try:
                bad = self.session_dir.with_name(f"session-uleselig-{datetime.now():%Y%m%d-%H%M%S}")
                if self.session_dir.exists():
                    self.session_dir.rename(bad)
                QMessageBox.warning(self, "Forrige økt", "Sidene fra forrige gang kunne ikke hentes fram. De er "
                                    f"tatt vare på i mappa\n{bad}")
            except OSError:
                pass
        finally:
            self._session_ready = True
            self._schedule_session()

    def _restore(self):
        f = self.session_dir / "session.json"
        if not f.exists():
            if self.session_dir.exists() and any(self.session_dir.glob("p*.png")):
                raise RuntimeError("session.json mangler, men det ligger sider i økta")   # flyttes til side
            return
        data = json.loads(f.read_text(encoding="utf-8"))     # uleselig -> unntak -> økta flyttes til side
        items = [it for it in (data.get("pages") or []) if isinstance(it, dict) and "id" in it]
        if not items:
            return
        # Les alle sidene inn i minnet FØR spørsmålet vises, så ingenting kan forsvinne mens det står åpent
        files = {}
        for it in items:
            try:
                o = self.session_dir / f"o{it['id']}.jpg"
                files[it["id"]] = ((self.session_dir / f"p{it['id']}.png").read_bytes(),
                                   (self.session_dir / f"t{it['id']}.png").read_bytes()
                                   if (self.session_dir / f"t{it['id']}.png").exists() else None,
                                   o.read_bytes() if o.exists() else None)
            except (OSError, KeyError):
                pass
        if not files:
            return
        n_docs = sum(1 for it in items if it.get("meta"))
        r = QMessageBox.question(self, "Fortsett der du slapp?",
                                 f"Forrige gang lå det {len(files)} side(r) i {max(1, n_docs)} dokument(er) "
                                 "i lista.\n\nHente dem fram igjen?\n\n(Svarer du nei, tas de vare på i "
                                 f"{ARCHIVE_DAYS} dager i mappa «forkastet» i NoteScanner-datamappa.)")
        if r != QMessageBox.Yes:
            self._discard_session()
            return
        id_map = {}
        restored: list[Page] = []
        for it in items:
            if it.get("id") not in files:
                continue
            png, thumb, orig = files[it["id"]]
            keep_id = it["id"] if isinstance(it["id"], int) and self._index(it["id"]) < 0 else None
            p = Page(png, self._safe_int(it.get("dpi"), 300), bw=bool(it.get("bw")), manual=bool(it.get("manual")),
                     section=it.get("section"), orig=orig)
            if keep_id is not None:
                p.id = keep_id          # samme id som filene i økta – ingenting må skrives om
            if thumb:
                try:
                    p.thumb_arr = proc.decode(thumb)
                except RuntimeError:
                    pass
            p.meta = DocMeta.from_json(it["meta"]) if it.get("meta") else None
            if it.get("sug"):
                p.sug = _suggestion_from_json(it["sug"])
            p.ocr_state = "off" if it.get("ocr_state") == "off" else "done"
            if it.get("ocr_state") == "pending" and self.auto_name.isChecked():
                p.ocr_state = "pending"
                self.ocr_thread.add(p.id, p.ocr_png(), p.dpi)
            id_map[it["id"]] = p.id
            restored.append(p)
            if keep_id is None:
                self._session_dirty.add(p.id)   # ny id -> skriv filene på nytt
        if self.pages and restored:      # sider skannet mens spørsmålet sto åpent kommer etter de gamle
            restored[0].meta = restored[0].meta or DocMeta()
        self.pages = restored + self.pages
        if data.get("split_mode") in ("auto", "book"):
            self.split_seg.setValue(data["split_mode"])
            self._update_hints()
        self.cur_id = id_map.get(data.get("current")) or (self.pages[0].id if self.pages else None)
        self._refresh_tree()
        self.status.showMessage(f"Hentet fram {len(restored)} side(r) fra forrige gang.", 8000)

    # ================================================================ meldinger fra skannerknappen
    def handle_args(self, args: list[str]):
        device = None
        event = False
        files = []
        for a in args:
            if a.lower().startswith("/stidevice:"):
                device = a.split(":", 1)[1]
            elif a.lower().startswith("/stievent:") or a == "--scan":
                event = True
            elif os.path.isfile(a):
                files.append(a)
        if device:
            self._wia_device = device
        for fpath in files:
            if Path(fpath).suffix.lower() in IMG_EXT:
                self.import_file(fpath)
        if event:
            self.raise_to_front()
            self.scan_page(device)

    def closeEvent(self, e):
        srv = getattr(self, "_server", None)
        if srv is not None:
            srv.close()                # nye skannerknapp-trykk skal starte en ny instans, ikke gå til denne
        SCAN_IDLE.set()
        if self.save_thread is not None:
            self.status.showMessage("Venter på at lagringen blir ferdig …")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.save_thread.wait()
            QApplication.restoreOverrideCursor()
        self._drain_processing()
        self.proc_thread.stop()
        QApplication.processEvents()   # ta med sider som ble ferdige akkurat nå
        self._session_timer.stop()
        self._flush_session()          # ulagrede dokumenter hentes fram neste gang appen starter
        if not self.pages and self._session_ready:
            shutil.rmtree(self.session_dir, ignore_errors=True)
        self._save_settings()
        self.ocr_thread.stop()
        self._closed = True
        super().closeEvent(e)


    def _drain_processing(self):
        """Gjør ferdig sider som står i kø for behandling, så de kommer med i økta (ellers går de tapt)."""
        n = self.proc_thread.pending()
        if not n:
            return
        dlg = QProgressDialog(f"Gjør ferdig {n} skannet side(r) før appen lukkes …", None, 0, n, self)
        dlg.setWindowTitle("NoteScanner")
        dlg.setMinimumDuration(0)
        dlg.setWindowModality(Qt.ApplicationModal)
        deadline = time.monotonic() + 120
        while self.proc_thread.pending() and time.monotonic() < deadline:
            dlg.setValue(n - self.proc_thread.pending())
            QApplication.processEvents()
            time.sleep(0.05)
        for _ in range(40):            # siste side: vent til resultatet er kommet fram til lista
            QApplication.processEvents()
            time.sleep(0.025)
        dlg.close()

    def emergency_save(self, *_):
        """Windows logger av / slår av: lagre økt og innstillinger med en gang (closeEvent kommer kanskje ikke)."""
        if getattr(self, "_closed", False):
            return
        try:
            self._flush_session()
            self._save_settings()
            self.settings.sync()
        except Exception:  # noqa: BLE001
            pass


    # ================================================================ oppdatering, feilrapport, skannerknapp
    def _run_bg(self, fn, on_result, on_error, with_progress=False, on_progress=None):
        t = FuncThread(fn, self, with_progress)
        t.result.connect(on_result)
        t.error.connect(on_error)
        if on_progress:
            t.progress.connect(on_progress)
        t.finished.connect(lambda: self._update_threads.remove(t) if t in self._update_threads else None)
        self._update_threads.append(t)
        t.start()
        return t

    def check_updates(self, manual: bool = False):
        def done(rel):
            self._release = rel
            if rel and updater.is_newer(rel.version, APP_VERSION):
                how = "Oppdater nå" if updater.can_self_update() else "Last ned"
                self.update_lbl.setText(f"<b>NoteScanner {rel.version}</b> er klar (du har {APP_VERSION}).")
                self.b_update.setText(how)
                self.update_bar.show()
            elif manual:
                QMessageBox.information(self, "Ny versjon", f"Du har siste versjon ({APP_VERSION}).")

        def failed(msg):
            if manual:
                QMessageBox.warning(self, "Ny versjon", f"Fikk ikke kontakt med GitHub:\n{msg}")
        self._run_bg(updater.check, done, failed)

    def _show_release_notes(self):
        rel = self._release
        if not rel:
            return
        box = QMessageBox(self)
        box.setWindowTitle(f"Nytt i NoteScanner {rel.version}")
        box.setTextFormat(Qt.MarkdownText)
        box.setText(rel.notes or "(ingen beskrivelse)")
        box.exec()

    def install_update(self):
        rel = self._release
        if not rel:
            return
        if not updater.can_self_update() or not rel.installer_url:
            QDesktopServices.openUrl(QUrl(rel.page))
            return
        if self.save_thread is not None or self._thread is not None:
            QMessageBox.information(self, "Oppdater", "Vent til skanning og lagring er ferdig, og prøv igjen.")
            return
        dlg = QProgressDialog("Laster ned ny versjon …", "Avbryt", 0, 100, self)
        dlg.setWindowTitle("Oppdaterer NoteScanner")
        dlg.setMinimumDuration(0)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setValue(0)
        state = {"cancel": False}
        dlg.canceled.connect(lambda: state.update(cancel=True))

        def prog(done, total):
            if total:
                dlg.setValue(min(99, int(100 * done / total)))

        def ok(path):
            dlg.close()
            if state["cancel"]:
                return
            # Lagre alt og lukk; installasjonen starter appen igjen når den er ferdig (økta hentes fram).
            self._flush_session()
            self._save_settings()
            try:
                updater.launch_installer(path)
            except OSError as e:
                QMessageBox.warning(self, "Oppdater", f"Klarte ikke å starte installasjonen:\n{e}")
                return
            QApplication.instance().quit()

        def failed(msg):
            dlg.close()
            QMessageBox.warning(self, "Oppdater", f"Nedlastingen feilet:\n{msg}\n\nDu kan også laste ned selv fra "
                                f"{rel.page}")
        self._run_bg(lambda cb: updater.download(rel, cb), ok, failed, with_progress=True, on_progress=prog)

    def report_bug(self):
        info = bugreport.system_info(APP_VERSION, {
            "Skanner": self._scanner_name, "Tekstlesing": ocr.available_engine() or "ingen",
            "Oppløsning": self.dpi.currentData(), "Resultat": self.mode, "Område": self.area.currentData(),
            "Oppdeling": self.split_mode, "Sider i lista": len(self.pages)})
        box = QMessageBox(self)
        box.setWindowTitle("Rapporter feil")
        box.setText("Feilmeldingen åpnes på GitHub, ferdig utfylt med versjon, systeminfo og de siste linjene i "
                    "loggen (personlige mappenavn er fjernet). Du trenger en gratis GitHub-konto.\n\n"
                    "Beskriv hva du gjorde og hva som gikk galt. Legg ikke ved bilder av noter du ikke har rett "
                    "til å dele – saken er offentlig.\n\nUten GitHub-konto: lagre en feilrapport-fil og send den "
                    "til den som ga deg appen.")
        b_web = box.addButton("Åpne på GitHub", QMessageBox.AcceptRole)
        b_zip = box.addButton("Lagre feilrapport-fil", QMessageBox.ActionRole)
        box.addButton("Avbryt", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_web:
            QDesktopServices.openUrl(QUrl(bugreport.issue_url(APP_VERSION, info, bugreport.log_excerpt()),
                                          QUrl.TolerantMode))
        elif box.clickedButton() is b_zip:
            try:
                out = bugreport.save_zip(APP_VERSION, info)
            except OSError as e:
                QMessageBox.warning(self, "Rapporter feil", f"Klarte ikke å lagre: {e}")
                return
            QMessageBox.information(self, "Rapporter feil", f"Lagret:\n{out}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out.parent)))

    def register_button(self):
        from . import button
        if not button.run_elevated("--registrer-knapp"):
            QMessageBox.warning(self, "Skannerknappen", "Fikk ikke administrator-tillatelse.")


# ================================================================ oppstart / én instans

def _instance_name() -> str:
    """Navn på «kjører allerede»-kanalen – per bruker, så to brukere på samme PC ikke snakker med hverandre."""
    import hashlib
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return f"{APP_ID}-{hashlib.sha1((user + str(paths.data_dir())).encode('utf-8')).hexdigest()[:12]}"


def _send_to_running(args: list[str], name: str | None = None) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(name or _instance_name())
    if not sock.waitForConnected(500):
        return False
    # filstier gjøres absolutte her – den andre instansen kan ha en annen arbeidsmappe
    args = [os.path.abspath(a) if not a.startswith(("/", "-")) and os.path.exists(a) else a for a in args]
    sock.write(json.dumps(args).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def _selftest() -> int:
    """`NoteScanner.exe --selftest`: sjekk at alt som trengs er med i den bygde versjonen (brukes ved bygging).
    Rører ikke brukerens økt eller innstillinger. Resultatet skrives til logg/selvtest.txt."""
    import traceback
    lines, ok = [], True

    def step(name, fn):
        nonlocal ok
        try:
            lines.append(f"OK    {name}: {fn() or ''}")
        except Exception:  # noqa: BLE001
            ok = False
            lines.append(f"FEIL  {name}\n{traceback.format_exc()}")

    img = np.full((1200, 900), 235, np.uint8)
    for k in range(5):
        img[300 + k * 14, 80:820] = 30
    cv2.putText(img, "Test", (380, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, 20, 4)
    step("ikoner (QtSvg)", lambda: icons.logo_icon().isNull() and (_ for _ in ()).throw(RuntimeError("tomt ikon")))
    step("bildebehandling", lambda: proc.enhance(proc.deskew(img, 300)[0], 300).shape)
    step("png", lambda: len(proc.encode_png(img)))

    def pdf():
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.pdf")
            save_pdf([(img, 300, True), (img, 300)], out, layout="native", bookmarks=[(0, "Æøå")])
            return os.path.getsize(out)
    step("pdf", pdf)
    step("tekstlesing", lambda: ocr.available_engine() or "ingen (OK på maskiner uten norsk/engelsk OCR)")
    if sys.platform == "win32":
        step("winocr.ps1", lambda: paths.resource("notescanner", "winocr.ps1").is_file() or
             (_ for _ in ()).throw(FileNotFoundError("winocr.ps1 mangler")))
        step("WIA (pywin32)", lambda: __import__("win32com.client") and "importert")
    report = f"NoteScanner {APP_VERSION} selvtest\n" + "\n".join(lines) + "\n"
    try:
        (paths.log_dir() / "selvtest.txt").write_text(report, encoding="utf-8")
    except OSError:
        pass
    try:
        sys.__stdout__.write(report)
    except Exception:  # noqa: BLE001
        pass
    return 0 if ok else 1


def _app_mutex():
    """Navngitt mutex på Windows: installasjonen (Inno Setup AppMutex) ser at appen kjører og ber deg lukke den."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32.CreateMutexW(None, False, "NoteScannerAppMutex")
    except Exception:  # noqa: BLE001
        return None


def _windows_tweaks(win: QMainWindow):
    """Mørk tittellinje (Windows 10/11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(win.winId())
        on = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (ny og gammel verdi)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


def _install_error_log():
    """Uventede feil skrives til feillogg.txt i loggmappa (%LOCALAPPDATA%\\NoteScanner\\logg).
    Krasj i C-kode (Qt, OpenCV) fanges av faulthandler i krasj.txt."""
    import faulthandler
    import traceback
    try:
        fh = open(paths.log_dir() / "krasj.txt", "a", encoding="utf-8")   # holdes åpen så lenge appen kjører
        faulthandler.enable(fh)
        _install_error_log.fh = fh
    except (OSError, RuntimeError):
        pass

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        paths.error_log(text, APP_VERSION)
        try:
            sys.__stderr__.write(text)
        except Exception:  # noqa: BLE001
            pass
        win = QApplication.activeWindow()
        if isinstance(win, MainWindow):
            win.status.showMessage(f"Uventet feil: {value} – du kan melde fra under Flere valg → Rapporter feil",
                                   15000)
    sys.excepthook = hook

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = thread_hook


def setup_app(app: QApplication):
    app.setApplicationName("NoteScanner")
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(font(10))
    app.setWindowIcon(icons.logo_icon())
    try:
        d = paths.ui_dir()
    except OSError:
        d = Path(tempfile.gettempdir())
    files = {"check": ("check", "#ffffff", 2.6), "up": ("up", C["muted"], 2.4), "down": ("down", C["muted"], 2.4)}
    ui = {}
    for key, (name, col, stroke) in files.items():   # små ikoner som stilarket viser (krever filer)
        ui[key] = str(d / f"{key}.png")
        try:
            icons.pixmap(name, col, 14, stroke).save(ui[key])
        except Exception:  # noqa: BLE001
            pass
    app.setStyleSheet(stylesheet(ui["check"], ui["up"], ui["down"]))
    app._nowheel = NoWheel(app)   # holdes i live så lenge appen kjører
    app.installEventFilter(app._nowheel)


def main():
    args = sys.argv[1:]
    if sys.platform == "win32":
        try:   # eget ikon på oppgavelinja i stedet for Python-ikonet
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NoteScanner")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    if "--selftest" in args:
        return _selftest()
    if "--registrer-knapp" in args or "--fjern-knapp" in args:
        from . import button
        setup_app(app)
        try:
            lines = button.register(remove="--fjern-knapp" in args)
            text = "\n".join(lines)
            if "--registrer-knapp" in args:
                text += ("\n\nVelg nå NoteScanner for knappen: Skrivere og skannere → skanneren → "
                         "Skanneregenskaper → fanen Hendelser → Start dette programmet: NoteScanner.")
        except Exception as e:  # noqa: BLE001
            text = f"Feilet: {e}"
        if "--stille" not in args:
            QMessageBox.information(None, "Skannerknappen", text)
        return 0
    name = _instance_name()
    # Én instans per bruker: låsefila holdes så lenge appen kjører (QLockFile rydder etter et krasj selv).
    lock = QLockFile(str(paths.data_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        # En annen instans kjører (eller holder på å starte): send den beskjeden, prøv i noen sekunder.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if _send_to_running(args, name):
                return 0
            if lock.tryLock(250):
                break
        else:
            QMessageBox.information(None, "NoteScanner", "NoteScanner kjører allerede, men svarer ikke.\n"
                                    "Lukk den (eller start PC-en på nytt) og prøv igjen.")
            return 1
    app._ns_lock = lock
    app._ns_mutex = _app_mutex()
    setup_app(app)
    _install_error_log()

    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(name):
        QLocalServer.removeServer(name)
        server.listen(name)

    win = MainWindow()
    win._server = server

    def on_conn():
        s = server.nextPendingConnection()

        def read():
            try:
                win.handle_args(json.loads(bytes(s.readAll()).decode("utf-8")))
            except Exception:  # noqa: BLE001
                pass
        s.readyRead.connect(read)

    server.newConnection.connect(on_conn)
    app.commitDataRequest.connect(win.emergency_save)
    app.aboutToQuit.connect(win.emergency_save)
    win.show()
    _windows_tweaks(win)
    QTimer.singleShot(100, lambda: win.handle_args(args))
    code = app.exec()
    win.emergency_save()
    lock.unlock()
    if sys.platform == "win32":
        # En skanning som henger i driveren kan ikke avbrytes; avslutt hardt i stedet for å vente/krasje
        # (økt og innstillinger er allerede lagret).
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
