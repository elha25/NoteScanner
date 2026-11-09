"""Enkle strekikoner (24×24, tegnet for NoteScanner) og app-logoen, gjort om til QIcon via QtSvg."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "scan": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 '
            '1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M3 12h18"/>',
    "import": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.8"/>'
              '<path d="m21 16-5-5-9 9"/>',
    "autocrop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "select": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 '
              '1-1-1v-4"/><path d="M9 9h6v6H9z"/>',
    "deskew": '<path d="M3 20h18"/><path d="m7.5 16 1.6-11 8 1.2-1.6 11z"/>',
    "rotl": '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4 3.5V8h4.5"/>',
    "rotr": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M20 3.5V8h-4.5"/>',
    "enhance": '<path d="M11 3.5 12.8 8l4.7 1.8-4.7 1.8L11 16l-1.8-4.4L4.5 9.8 9.2 8z"/>'
               '<path d="m18 14 .9 2.1L21 17l-2.1.9L18 20l-.9-2.1L15 17l2.1-.9z"/>',
    "dust": '<path d="M8 20h12"/><path d="m5.6 14.4 8.5-8.5a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L12 18H9.2'
            'a2 2 0 0 1-1.4-.6l-2.2-2.2a.9.9 0 0 1 0-.8z"/><path d="m10 10 5 5"/>',
    "text": '<path d="M4 7V5h11v2"/><path d="M9.5 5v13"/><path d="M7.5 18h4"/><path d="M15 12h5M15 16h5"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "split": '<path d="M6 3h12v5H6zM6 16h12v5H6z"/><path d="M3 12h3M10 12h4M18 12h3"/>',
    "merge": '<path d="M7 3v4.5l5 4.5 5-4.5V3"/><path d="M12 12v9"/><path d="m8.5 17.5 3.5 3.5 3.5-3.5"/>',
    "up": '<path d="m6 15 6-6 6 6"/>',
    "fold": '<path d="m7 20 5-5 5 5"/><path d="m7 4 5 5 5-5"/>',
    "unfold": '<path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/>',
    "fit": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><path d="M4 4l6 6M20 4l-6 6M20 20l-6-6M4 20l6-6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="m6 7 1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3"/>'
            '<path d="M7 21v-7h10v7"/>',
    "saveall": '<path d="M12 3v11"/><path d="m7 9 5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "gray": '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
    "bw": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h6z" '
          'fill="currentColor"/>',
    "color": '<circle cx="9" cy="9.5" r="4.5"/><circle cx="15" cy="9.5" r="4.5"/><circle cx="12" cy="15" r="4.5"/>',
    "docs": '<path d="M9 3h7l4 4v10"/><path d="M4 7h8l4 4v10H4z"/>',
    "book": '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5V5.5"/>'
            '<path d="M8 7.5h8"/>',
    "bookmark": '<path d="M6 3h12v18l-6-4.5L6 21z"/>',
    "chev_right": '<path d="m9 6 6 6-6 6"/>',
    "chev_down": '<path d="m6 9 6 6 6-6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "page": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    "apply": '<path d="M4 6h16M4 12h16M4 18h10"/><path d="m16 17 2 2 4-4"/>',
    "open": '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 '
            '1 2-2h4"/>',
}

LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#6cb6ff"/><stop offset="1" stop-color="#2f7fe0"/></linearGradient></defs>'
        '<rect x="2" y="2" width="44" height="44" rx="11" fill="url(#g)"/>'
        '<g fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M11 17v-4a2 2 0 0 1 2-2h4M31 11h4a2 2 0 0 1 2 2v4M37 31v4a2 2 0 0 1-2 2h-4M17 37h-4a2 2 0 0 1-2-2v-4"/>'
        '<path d="M21.5 29.5V17.5l9-2.2v11.8"/></g>'
        '<g fill="#fff"><ellipse cx="19" cy="29.8" rx="3.2" ry="2.5" transform="rotate(-20 19 29.8)"/>'
        '<ellipse cx="28" cy="27.4" rx="3.2" ry="2.5" transform="rotate(-20 28 27.4)"/></g></svg>')


def _svg(name: str, color: str, stroke: float) -> bytes:
    body = PATHS[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def _render(svg: bytes, size: int, ratio: float = 2.0) -> QPixmap:
    px = max(1, int(size * ratio))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg)).render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#d9dbe0", size: int = 18, on_color: str | None = None,
         disabled: str = "#5b5e66", stroke: float = 1.8) -> QIcon:
    ic = QIcon()
    ic.addPixmap(_render(_svg(name, color, stroke), size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(_render(_svg(name, disabled, stroke), size), QIcon.Disabled, QIcon.Off)
    if on_color:
        ic.addPixmap(_render(_svg(name, on_color, stroke), size), QIcon.Normal, QIcon.On)
    return ic


def pixmap(name: str, color: str, size: int, stroke: float = 1.8) -> QPixmap:
    return _render(_svg(name, color, stroke), size)


def logo_pixmap(size: int) -> QPixmap:
    return _render(LOGO.encode(), size)


def logo_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(_render(LOGO.encode(), s, 1.0))
    return ic
