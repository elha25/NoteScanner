"""Syntetiske notesider til testene – ingen opphavsrettsbeskyttede noter i repoet.

`make_page()` tegner en side med tittel, stemme, komponist og notesystemer (notelinjer, noter, taktstreker),
og kan legge på det som skjer i virkeligheten: skjev side, bleikt trykk, skygge fra bokryggen, to sanger på
samme side, eller en stripe av nabosiden.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def mm(v: float, dpi: int) -> int:
    return int(round(v / 25.4 * dpi))


def _font(px: int):
    return ImageFont.load_default(size=max(8, px))


def _text(draw: ImageDraw.ImageDraw, xy, text: str, px: int, anchor="la", ink=0):
    draw.text(xy, text, fill=ink, font=_font(px), anchor=anchor)


def _system(draw, x0, x1, y, s, rng, ink=0, line_w=1, notes=True):
    """Ett notesystem: fem linjer med avstand s, noter og taktstreker."""
    for k in range(5):
        draw.line([(x0, y + k * s), (x1, y + k * s)], fill=ink, width=line_w)
    draw.line([(x0, y), (x0, y + 4 * s)], fill=ink, width=line_w + 1)
    draw.line([(x1, y), (x1, y + 4 * s)], fill=ink, width=line_w + 1)
    # «nøkkel»
    draw.ellipse([x0 + s, y + s, x0 + 2.6 * s, y + 3.4 * s], outline=ink, width=max(1, line_w + 1))
    if not notes:
        return
    bars = 4
    bw = (x1 - x0 - 4 * s) / bars
    for b in range(bars):
        bx = x0 + 4 * s + b * bw
        if b:
            draw.line([(bx, y), (bx, y + 4 * s)], fill=ink, width=line_w)
        for n in range(4):
            nx = bx + (n + 0.6) * bw / 4.6
            ny = y + s * rng.integers(-2, 10) / 2
            draw.ellipse([nx - 0.65 * s, ny - 0.5 * s, nx + 0.65 * s, ny + 0.5 * s], fill=ink)
            draw.line([(nx + 0.6 * s, ny), (nx + 0.6 * s, ny - 3.2 * s)], fill=ink, width=line_w)


def make_page(dpi: int = 300, size_mm=(210, 297), title="Bjelleklang", part="2. Kornett i Bb",
              composer="Arr: Kari Nordmann", systems: int = 7, second_song: str | None = None,
              second_systems: int = 3, staff_space_mm: float = 1.75, margin_mm=(18, 16),
              seed: int = 1) -> np.ndarray:
    """Tegn en ren side (gråtone, uint8). second_song: tittel på en ny sang som starter midt på siden."""
    rng = np.random.default_rng(seed)
    w, h = mm(size_mm[0], dpi), mm(size_mm[1], dpi)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    s = staff_space_mm / 25.4 * dpi
    x0, x1 = mm(margin_mm[0], dpi), w - mm(margin_mm[0], dpi)
    lw = max(1, int(round(dpi / 300)))

    def header(y, ttl):
        _text(d, (w / 2, y), ttl, mm(7, dpi), anchor="mt")
        _text(d, (x0, y + mm(11, dpi)), part, mm(3.2, dpi))
        _text(d, (x1, y + mm(11, dpi)), composer, mm(3.0, dpi), anchor="ra")
        return y + mm(20, dpi)

    y = header(mm(margin_mm[1], dpi), title)
    pitch = mm(13, dpi)
    for _ in range(systems):
        _system(d, x0, x1, y, s, rng, line_w=lw)
        y += pitch
    if second_song:
        y += mm(6, dpi)
        y = header(y, second_song)
        for _ in range(second_systems):
            _system(d, x0, x1, y, s, rng, line_w=lw)
            y += pitch
    _text(d, (w / 2, h - mm(10, dpi)), "Testside – laget av tests/synth.py", mm(2.2, dpi), anchor="mb")
    return np.array(img)


# ------------------------------------------------------------------ «skanner-effekter»
def scanned(page: np.ndarray, dpi: int = 300, canvas_mm=None, offset_mm=(8, 8), angle: float = 0.0,
            paper: int = 238, ink_floor: int = 25, noise: float = 3.0, faint: float = 1.0,
            seed: int = 2) -> np.ndarray:
    """Legg siden på et «glass»: papirfarge, blekk som ikke er helt svart, støy, litt skjevhet."""
    rng = np.random.default_rng(seed)
    p = page.astype(np.float32) / 255.0
    ink = ink_floor + (255 - ink_floor) * (1 - faint)          # faint < 1 = blekere trykk
    p = ink + (paper - ink) * p
    if angle:
        hh, ww = p.shape
        m = cv2.getRotationMatrix2D((ww / 2, hh / 2), angle, 1.0)
        p = cv2.warpAffine(p, m, (ww, hh), borderValue=float(paper))
    if canvas_mm:
        cw, ch = mm(canvas_mm[0], dpi), mm(canvas_mm[1], dpi)
        c = np.full((ch, cw), float(paper) - 6, np.float32)    # glasset utenfor arket er litt mørkere
        ox, oy = mm(offset_mm[0], dpi), mm(offset_mm[1], dpi)
        hh, ww = p.shape
        hh2, ww2 = min(hh, ch - oy), min(ww, cw - ox)
        c[oy:oy + hh2, ox:ox + ww2] = p[:hh2, :ww2]
        p = c
    p += rng.normal(0, noise, p.shape).astype(np.float32)
    return np.clip(p, 0, 255).astype(np.uint8)


def add_gutter(img: np.ndarray, pos_px: int, axis: int = 0, width_px: int = 60, dark: int = 90,
               line: bool = True) -> np.ndarray:
    """Skygge (og evt. brettlinje) fra bokryggen på tvers av bildet ved pos_px."""
    out = img.astype(np.float32)
    n = out.shape[axis]
    t = np.arange(n, dtype=np.float32)
    f = 1 - (1 - dark / 255) * np.exp(-((t - pos_px) / (width_px / 2)) ** 2)
    out *= f[:, None] if axis == 0 else f[None, :]
    if line:
        if axis == 0:
            out[pos_px:pos_px + 2, :] *= 0.5
        else:
            out[:, pos_px:pos_px + 2] *= 0.5
    return np.clip(out, 0, 255).astype(np.uint8)
