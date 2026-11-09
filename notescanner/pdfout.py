"""Lag flersiders PDF av sidene, med riktig fysisk størrelse ut fra dpi, og eventuelt bokmerker."""
from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor

import cv2
import img2pdf
import numpy as np
from PIL import Image

A4 = (img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297))


def _encode(img, dpi: int, bw: bool = False) -> bytes:
    if isinstance(img, (bytes, bytearray)):   # sidene ligger komprimert i minnet
        from .processing import decode
        img = decode(img)
    if bw:
        from .processing import binarize
        img, dpi = binarize(img, dpi)
    if img.ndim == 3:
        b, g, r = cv2.split(img)
        if np.abs(b.astype(int) - g).max() < 8 and np.abs(g.astype(int) - r).max() < 8:
            img = g  # "farge" som egentlig er grå -> mindre fil
    buf = io.BytesIO()
    if img.ndim == 2:
        vals = np.unique(img[::4, ::4])
        if len(vals) <= 2 and set(vals.tolist()) <= {0, 255}:
            # Ren svart/hvitt: CCITT G4 (som faks og dokumentskannere) – liten fil, rask å lage.
            # Én stripe gjør at img2pdf kan legge dataene rett inn i PDF-en uten å pakke dem opp igjen.
            im = Image.fromarray(img).convert("1")
            im.save(buf, "TIFF", compression="group4", dpi=(dpi, dpi), tiffinfo={278: im.height})
        else:
            Image.fromarray(img).save(buf, "JPEG", quality=90, dpi=(dpi, dpi), optimize=True)
    else:
        Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(buf, "JPEG", quality=90, dpi=(dpi, dpi))
    return buf.getvalue()


def save_pdf(pages, path: str, layout: str = "a4", title: str | None = None,
             author: str | None = None, subject: str | None = None,
             bookmarks: list[tuple[int, str]] | None = None, progress=None):
    """pages: liste av (bilde eller PNG-bytes, dpi) eller (…, dpi, svart_hvitt).

    layout:
      "a4"     – A4-ark, noten i faktisk størrelse, sentrert (krympes bare hvis den er større)
      "a4fill" – A4-ark, noten forstørres/forminskes til å fylle arket
      "native" – sidestørrelse = beskåret bilde
    bookmarks: [(sideindeks fra 0, tittel), …] – blir innholdsfortegnelse i PDF-leseren.
    progress:  kalles som progress(ferdige_sider) etter hver side.
    """
    imgs = []
    n = len(pages)
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(workers) as ex:   # flere sider om gangen – bildebibliotekene slipper GIL-en
        futs = [ex.submit(_encode, *p) for p in pages]
        for k, fut in enumerate(futs):
            imgs.append(fut.result())
            if progress and k + 1 < n:
                progress(k + 1)            # siste steg meldes først når fila er skrevet
    kw = {}
    if layout == "a4":
        kw["layout_fun"] = img2pdf.get_layout_fun(A4, fit=img2pdf.FitMode.shrink, auto_orient=True)
    elif layout == "a4fill":
        kw["layout_fun"] = img2pdf.get_layout_fun(A4, fit=img2pdf.FitMode.into, auto_orient=True)
    if title:
        kw["title"] = title
    if author:
        kw["author"] = author
    if subject:
        kw["subject"] = subject
    # Skriv rett til en midlertidig fil (ikke hele PDF-en i minnet flere ganger), og bytt den inn til slutt,
    # så en halvferdig fil aldri blir liggende og en gammel versjon ikke ødelegges hvis noe går galt.
    tmp = path + ".lagres"
    raw = path + ".lagres1"
    try:
        with open(raw if bookmarks else tmp, "wb") as f:
            img2pdf.convert(imgs, outputstream=f, **kw)
        imgs.clear()
        if bookmarks:
            _add_bookmarks(raw, tmp, bookmarks)
        os.replace(tmp, path)
    except PermissionError as e:
        raise RuntimeError(f"Fikk ikke skrive «{os.path.basename(path)}». Er den åpen i PDF-leseren? "
                           f"Lukk den og prøv igjen. ({e.strerror or e})") from e
    except OSError as e:
        if getattr(e, "errno", None) == 28:
            raise RuntimeError("Disken er full – PDF-en ble ikke lagret.") from e
        raise
    finally:
        for f in (raw, tmp):
            try:
                os.remove(f)
            except OSError:
                pass
    if progress:
        progress(n)


def _add_bookmarks(src: str, dst: str, bookmarks):
    """Kopier src til dst med bokmerker. Uten pikepdf blir PDF-en lagret uten bokmerker."""
    try:
        import pikepdf
    except ImportError:
        import shutil
        shutil.copyfile(src, dst)
        return
    with pikepdf.open(src) as pdf:
        with pdf.open_outline() as outline:
            for idx, text in bookmarks:
                if 0 <= idx < len(pdf.pages):
                    outline.root.append(pikepdf.OutlineItem(text, idx))
        pdf.Root.PageMode = pikepdf.Name.UseOutlines   # vis bokmerkene når PDF-en åpnes
        pdf.save(dst)
