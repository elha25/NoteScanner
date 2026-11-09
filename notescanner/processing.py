"""Bildebehandling for skannede noter: auto-crop, rett opp (deskew), forbedring."""
from __future__ import annotations

import cv2
import numpy as np


def mm_to_px(mm: float, dpi: int) -> int:
    return max(1, int(round(mm / 25.4 * dpi)))


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


MIN_SIDE = 32   # px – mindre bilder (f.eks. en feilklikket beskjæring) behandles ikke, de kommer ut uendret


def _tiny(img: np.ndarray) -> bool:
    return img is None or img.ndim < 2 or min(img.shape[:2]) < MIN_SIDE


def _ink_mask(gray: np.ndarray, dpi: int, floor: int = 200) -> np.ndarray:
    """Binær maske (255 = blekk) med bakgrunnsutjevning, robust mot skygge fra lokket."""
    k = mm_to_px(15, dpi) | 1
    bg = cv2.medianBlur(cv2.resize(gray, None, fx=0.25, fy=0.25), max(3, (k // 4) | 1))
    bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    norm = cv2.divide(gray, np.maximum(bg, 1), scale=255)
    _, mask = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Otsu kan bli for "snill" på nesten tomme sider -> krev tydelig mørkt blekk
    mask[norm > floor] = 0
    return mask


FAINT_FLOOR = 225   # bleik kopi / blyant: blekket er bare litt mørkere enn papiret


def content_bbox(img: np.ndarray, dpi: int = 300, edge_ignore_mm: float = 3.0,
                 min_blob_mm: float = 1.2, floor: int = 200):
    """Finn rektangel (x0, y0, x1, y1) rundt alt innhold på siden (noter, tekst, titler).

    Ignorerer støv/prikker og skygger/papirkant helt ute ved kanten av glassplata.
    Returnerer None hvis siden ser tom ut.
    """
    if _tiny(img):
        return None
    gray = to_gray(img)
    h, w = gray.shape
    mask = _ink_mask(gray, dpi, floor)

    e = mm_to_px(edge_ignore_mm, dpi)
    mask[:e, :] = 0
    mask[-e:, :] = 0
    mask[:, :e] = 0
    mask[:, -e:] = 0
    # Skygge fra bokryggen / mørk kant utenfor arket: brede mørke bånd som starter ved kanten
    l, r, t, b = _edge_shadow(mask, dpi, e)
    mask[:, :l] = 0
    mask[:, w - r:] = 0
    mask[:t, :] = 0
    mask[h - b:, :] = 0

    _drop_side_lines(mask, dpi)

    # Fjern enkeltpiksel-støv, slå sammen nærliggende symboler (staccato-prikker, tekst)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    d = mm_to_px(2.5, dpi)
    merged = cv2.dilate(mask, np.ones((d, d), np.uint8))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    ink = np.bincount(labels[mask > 0].ravel(), minlength=n)   # ekte blekkpiksler per komponent
    min_side = mm_to_px(min_blob_mm, dpi) + d
    band = mm_to_px(8, dpi) + e   # "berører kanten"
    thin = mm_to_px(6, dpi)       # papirkant: én strek (+ utvidelsen). Tittellinjer nær kanten er tykkere
    line = mm_to_px(4.5, dpi)     # ett notesystem er minst ~4,4 mm + utvidelsen, en papirkant er tynnere
    side = mm_to_px(30, dpi)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < min_side and bh < min_side:
            continue  # støvkorn
        # Lange, tynne streker langs kanten = papirkant/skygge, ikke innhold
        vert_edge = (x < band or x + bw > w - band) and bw <= thin and bh > h * 0.3
        horiz_edge = (y < band or y + bh > h - band) and bh <= thin and bw > w * 0.3
        # Rest av skygge fra bokryggen: smal flekk ute ved sidekanten som går over nesten hele høyden
        side_shadow = (x < side or x + bw > w - side) and bw < side and bh > h * 0.75
        # Papirkant/bretten mellom to boksider midt i bildet: én lang strek (ikke fem som i et notesystem)
        crease = (bh <= line and bw > w * 0.3) or (bw <= line and bh > h * 0.3)
        # Skyggen i bretten: langt, smalt og nesten helt mørkt bånd (notesystemer er mest hvite)
        long_, short = max(bw, bh), min(bw, bh)
        # (fet tittelskrift kan også bli et langt, mørkt bånd – men aldri så tett som en skygge)
        dim = w if bw >= bh else h
        fill = ink[i] / max(1, bw * bh)
        gutter = long_ >= 6 * short and long_ > 0.3 * dim and (fill > 0.8 or (long_ > 0.85 * dim and fill > 0.4))
        if vert_edge or horiz_edge or side_shadow or crease or gutter:
            continue
        boxes.append((x, y, x + bw, y + bh, int(ink[i])))
    if not boxes:
        return None
    lims = (max(e, l), max(e, t), w - max(e, r), h - max(e, b))
    boxes = _main_page(boxes, gray, dpi, lims)
    x0 = min(b[0] for b in boxes) + d // 2
    y0 = min(b[1] for b in boxes) + d // 2
    x1 = max(b[2] for b in boxes) - d // 2
    y1 = max(b[3] for b in boxes) - d // 2
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def _drop_side_lines(mask: np.ndarray, dpi: int):
    """Fjern en loddrett strek ute på siden – papirkanten eller kanten av lokket – også når den er brutt opp.
    Streken må gå over mer enn en tredjedel av høyden, ligge i den ytterste femdelen og ha minst 4 mm helt
    tomt mellom seg og notene (en akkolade foran systemene har ikke det)."""
    h, w = mask.shape
    cols = (mask > 0).mean(axis=0)
    empty = mm_to_px(4, dpi)
    maxw = mm_to_px(3, dpi)
    for side in (0, 1):
        rng = range(0, int(w * 0.2)) if side == 0 else range(w - 1, int(w * 0.8), -1)
        idx = list(rng)
        k = 0
        while k < len(idx):
            if cols[idx[k]] > 0.33:
                start = k
                while k < len(idx) and cols[idx[k]] > 0.1:
                    k += 1
                run = idx[start:k]
                if len(run) <= maxw:
                    tail = mm_to_px(2, dpi)            # skyggen ved siden av streken
                    inner = idx[k + tail:k + tail + empty]
                    if len(inner) == empty and float(cols[inner].max()) < 0.02:
                        band = idx[max(0, start - tail):k + tail]
                        lo, hi = min(band), max(band)
                        mask[:, max(0, lo):hi + 1] = 0
                continue
            k += 1


def _clusters(boxes, axis: int, gap: int):
    """Del boksene i grupper langs en akse (0 = x, 1 = y) der det er minst `gap` piksler tomt imellom."""
    order = sorted(boxes, key=lambda b: b[axis])
    groups, end = [], None
    for b in order:
        if end is None or b[axis] - end > gap:
            groups.append([b])
            end = b[axis + 2]
        else:
            groups[-1].append(b)
            end = max(end, b[axis + 2])
    return groups


def _main_page(boxes, gray: np.ndarray, dpi: int, lims):
    """Fjern innhold fra nabosiden i boka (neste/forrige sang som er kommet med i kanten av skannet).

    Innholdet deles i blokker der det er et bredt tomt bånd imellom. Blokken med mest blekk er siden vi
    skanner. En blokk lenger ut fjernes hvis det er en brett/papirkant (mørk linje eller skygge) i båndet
    mellom, eller hvis den er kuttet av kanten av skannet og båndet er så bredt at det ikke kan være
    avstand innenfor én side.
    """
    split = mm_to_px(6, dpi)        # i utvidede bokser ≈ 8,5 mm tomt på papiret
    touch = mm_to_px(1.5, dpi)      # så nær kanten av skannet = kuttet av (fortsetter utenfor skannet)
    wide = mm_to_px(20, dpi)

    def _is_cut(g, axis, direction):
        """Går blekket rett ut i kanten over en god bredde (notelinjer/noter som er skåret av)?"""
        if axis == 1:
            edge = lims[3] - 1 if direction > 0 else lims[1]
            row = small[max(0, edge // 4 - 1):edge // 4 + 1, o0 // 4:o1 // 4]
        else:
            edge = lims[2] - 1 if direction > 0 else lims[0]
            row = small[o0 // 4:o1 // 4, max(0, edge // 4 - 1):edge // 4 + 1].T
        return row.size > 0 and float((row < paper - 60).mean()) > 0.01
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA).astype(np.float32)
    paper = float(np.percentile(small, 90))

    def fold_between(axis, a, b, other0, other1):
        """Er det en mørk linje/skygge på tvers i båndet a..b (langs aksen)?

        Hver kolonne på tvers sammenlignes med papiret rett på begge sider av båndet i samme kolonne: en brett
        eller kant er mørkere enn papiret på begge sider, over det meste av bredden. Kremfarget papir på hvitt
        lokk, en jevn skygge fra ryggen og de hvite hjørnene fra opprettingen er ikke det."""
        a4, b4 = a // 4, max(a // 4 + 1, b // 4)
        o0, o1 = other0 // 4, max(other0 // 4 + 1, other1 // 4)
        im = small if axis == 1 else small.T          # rader = posisjon langs aksen, kolonner = på tvers
        reg = im[a4:b4, o0:o1]
        if reg.size == 0 or reg.shape[1] < 4:
            return False
        reg = cv2.blur(reg, (5, 1))                   # én støvprikk er ingen brett
        m = max(2, mm_to_px(10, dpi) // 4)
        sides = [np.percentile(r, 90, axis=0) for r in (im[max(0, a4 - m):a4, o0:o1], im[b4:b4 + m, o0:o1])
                 if r.shape[0] > 0]
        side = np.minimum.reduce(sides) if sides else np.full(reg.shape[1], paper, np.float32)
        side = np.minimum(cv2.blur(side.reshape(1, -1).astype(np.float32), (9, 1)).ravel(), paper)
        dark = side - reg.min(axis=0)
        return float((dark > 22).mean()) > 0.5

    for _ in range(2):
        for axis in (1, 0):
            groups = _clusters(boxes, axis, split)
            if len(groups) < 2:
                continue
            mass = [sum(b[4] for b in g) for g in groups]
            k = int(np.argmax(mass))
            keep = [k]
            o0 = min(b[1 - axis] for b in boxes)
            o1 = max(b[3 - axis] for b in boxes)
            for direction in (-1, 1):
                prev = k
                i = k + direction
                while 0 <= i < len(groups):
                    g, gp = groups[i], groups[prev]
                    if direction > 0:
                        a, bnd = max(b[axis + 2] for b in gp), min(b[axis] for b in g)
                        at_edge = max(b[axis + 2] for b in g) >= lims[axis + 2] - touch
                    else:
                        a, bnd = max(b[axis + 2] for b in g), min(b[axis] for b in gp)
                        at_edge = min(b[axis] for b in g) <= lims[axis] + touch
                    # Uten brett kuttes bare en liten bit helt ute i kanten (avskåret) med bredt tomrom imellom –
                    # bunntekst og overskrifter på samme side skal være med.
                    cut = fold_between(axis, a, bnd, o0, o1) or \
                        (at_edge and bnd - a > wide and mass[i] < 0.25 * mass[k] and _is_cut(g, axis, direction))
                    if cut:
                        break          # alt herfra og utover hører til nabosiden
                    keep.append(i)
                    prev = i
                    i += direction
            boxes = [b for i in keep for b in groups[i]]
    return boxes


def _edge_shadow(mask: np.ndarray, dpi: int, start: int) -> tuple[int, int, int, int]:
    """Hvor langt inn fra hver kant (venstre, høyre, topp, bunn) det ligger en sammenhengende skygge.

    En skygge fra bokryggen eller lokket gir kolonner/rader der en stor del er «blekk». Noter gjør aldri det:
    taktstreker og notelinjer er tynne, og selv der systemene starter er under en tredjedel mørkt.
    """
    h, w = mask.shape
    lim_x, lim_y = mm_to_px(60, dpi), mm_to_px(40, dpi)   # lenger inn enn dette er det innhold, ikke skygge
    min_run = mm_to_px(3, dpi)
    gap = mm_to_px(1.5, dpi)
    cols = (mask > 0).mean(axis=0)
    rows = (mask > 0).mean(axis=1)

    def run(profile, idxs, need):
        last, misses = 0, 0
        for k, i in enumerate(idxs):
            if profile[i] > need:
                last, misses = k + 1, 0
            else:
                misses += 1
                if misses > gap:
                    break
        return last

    def side(profile, n, lim, rev):
        idxs = range(n - 1 - start, max(n - 1 - lim, -1), -1) if rev else range(start, min(lim, n))
        k = run(profile, idxs, 0.35)
        if k < min_run:
            return 0
        # ta med den lysere delen av skyggen også (der under en tredjedel er mørkt, men mer enn vanlig papir)
        k2 = run(profile, list(idxs)[k:], 0.12)
        return start + k + k2 + mm_to_px(1, dpi)

    return side(cols, w, lim_x, False), side(cols, w, lim_x, True), side(rows, h, lim_y, False), \
        side(rows, h, lim_y, True)


def auto_crop_inner(img: np.ndarray, dpi: int = 300, margin_mm: float = 5.0):
    """Beskjær til innholdet + marg. Returnerer (bilde, rect, inner) der inner er innholdet
    (+1,5 mm) i det beskårne bildets koordinater – eller (img, None, None) hvis siden er tom."""
    bbox = content_bbox(img, dpi)
    if bbox is None:
        bbox = content_bbox(img, dpi, floor=FAINT_FLOOR)
    if bbox is None:
        return img, None, None
    h, w = img.shape[:2]
    m = mm_to_px(margin_mm, dpi)
    bx0, by0, bx1, by1 = bbox
    x0, y0 = max(0, bx0 - m), max(0, by0 - m)
    x1, y1 = min(w, bx1 + m), min(h, by1 + m)
    out = crop(img, (x0, y0, x1, y1))
    pad = mm_to_px(1.5, dpi)
    inner = (max(0, bx0 - x0 - pad), max(0, by0 - y0 - pad),
             min(out.shape[1], bx1 - x0 + pad), min(out.shape[0], by1 - y0 + pad))
    return out, (x0, y0, x1, y1), inner


def clean_outside(img: np.ndarray, inner, value=None) -> np.ndarray:
    """Fyll alt utenfor innholdet med papirfarge (eller value), så rester av skygge, papirkanter
    og støv i margen forsvinner."""
    ix0, iy0, ix1, iy1 = [int(v) for v in inner]
    if ix1 <= ix0 or iy1 <= iy0:
        return img
    keep = img[iy0:iy1, ix0:ix1].copy()
    if value is None:
        value = np.median(keep.reshape(-1, keep.shape[2]), axis=0) if keep.ndim == 3 else np.median(keep)
    out = np.empty_like(img)
    out[:] = np.asarray(value).astype(np.uint8)
    out[iy0:iy1, ix0:ix1] = keep
    return out


def find_staves(img: np.ndarray, dpi: int = 300):
    """Finn notesystemene. Returnerer ([(topp, bunn), ...] i piksler, linjeavstand i piksler)."""
    if _tiny(img):
        return [], 0
    gray = to_gray(img)
    f = 300 / dpi if dpi > 0 else 1.0
    if abs(f - 1.0) > 0.01:     # konstantene under er i 300-dpi-piksler: skaler ned (og opp fra 150/200 dpi)
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
    else:
        f = 1.0
    mask = _ink_mask(gray, 300)
    # Korte biter (6 mm) og litt utglatting på tvers: tåler bleke, brutte og litt buede linjer (bokrygg)
    lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, mm_to_px(6, 300)), np.uint8))
    prof = (lines > 0).sum(axis=1).astype(np.float32)
    prof = np.convolve(prof, np.ones(3, np.float32), mode="same")
    if prof.max() < mm_to_px(25, 300):
        return [], 0
    rows = np.where(prof > max(mm_to_px(15, 300), 0.18 * prof.max()))[0]
    if len(rows) == 0:
        return [], 0
    # sammenhengende rader = én linje
    ys, start = [], rows[0]
    for a, b in zip(rows[:-1], rows[1:]):
        if b - a > 2:
            ys.append((start + a) / 2)
            start = b
    ys.append((start + rows[-1]) / 2)
    gaps = np.diff(ys)
    small = gaps[gaps < mm_to_px(3.5, 300)]
    if len(small) < 4:
        return [], 0
    s = float(np.median(small))
    staves, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= s * 1.6:
            cur.append(y)
        else:
            if len(cur) >= 4:
                staves.append((cur[0], cur[-1]))
            cur = [y]
    if len(cur) >= 4:
        staves.append((cur[0], cur[-1]))
    return [(int(a / f), int(b / f)) for a, b in staves], s / f


def _has_staff(lines: np.ndarray, dpi: int, s: float) -> bool:
    """Ligger det et notesystem (minst fire vannrette streker over ≥ 2,5 linjeavstander) i utsnittet?
    Brukes for å ikke dele en side ved et system med så bleke/brutte linjer at find_staves ikke så det."""
    if lines.size == 0:
        return False
    rows = (lines > 0).sum(axis=1) > mm_to_px(15, dpi)
    ys = np.where(rows)[0]
    if len(ys) == 0:
        return False
    centers, start = [], ys[0]
    for p, q in zip(ys[:-1], ys[1:]):
        if q - p > 2:
            centers.append((start + p) / 2)
            start = q
    centers.append((start + ys[-1]) / 2)
    # se etter fire streker med jevn, notelinje-aktig avstand
    for i in range(len(centers) - 3):
        grp = centers[i:i + 4]
        d = np.diff(grp)
        if d.min() > 0.6 * s and d.max() < 1.6 * s:
            return True
    return False


def song_splits(img: np.ndarray, dpi: int = 300) -> list[int]:
    """Rader der en ny sang begynner midt på siden (to korte sanger på samme bokside).

    Et vanlig mellomrom mellom to notesystemer er under ~1 cm. Mellom to sanger står nummer, tittel,
    stemme og komponist – et bredt felt (≥ 18 mm) med tekst som er mye høyere enn linjeavstanden.
    Svaret er radene det skal deles på (midt i det tomme båndet over den nye sangens overskrift).
    """
    if _tiny(img):
        return []
    staves, s = find_staves(img, dpi)
    if len(staves) < 2 or s <= 0:
        return []
    gray = to_gray(img)
    mask = _ink_mask(gray, dpi)
    # ta bort notelinjene, så bare tekst og noter står igjen
    lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, mm_to_px(10, dpi)), np.uint8))
    lines6 = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, mm_to_px(6, dpi)), np.uint8))
    ink = cv2.subtract(mask, lines)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    min_gap = mm_to_px(18, dpi)
    blank = max(2, mm_to_px(1.2, dpi))
    gaps = [b0 - a1 for (_, a1), (b0, _) in zip(staves[:-1], staves[1:])]
    normal = float(np.median(gaps)) if len(gaps) >= 2 else 0.0
    out = []
    for (_, a1), (b0, _) in zip(staves[:-1], staves[1:]):
        # mye større mellomrom enn ellers på siden (overskriften til en ny sang), ikke bare luftig sats
        if b0 - a1 < min_gap or b0 - a1 < 1.8 * normal or (not normal and b0 - a1 < mm_to_px(25, dpi)):
            continue
        m = int(round(s))
        if _has_staff(lines6[a1 + m:b0 - m], dpi, s):
            continue   # notelinjer i mellomrommet: et system vi ikke fant (bleke/brutte linjer), ikke en ny sang
        prof = (ink[a1:b0] > 0).sum(axis=1) > max(3, mm_to_px(0.6, dpi))
        # tekst-/notelinjer i båndet mellom systemene
        runs, i, n = [], 0, len(prof)
        while i < n:
            if prof[i]:
                j = i
                while j < n and prof[j]:
                    j += 1
                runs.append([i, j])
                i = j
            else:
                i += 1
        # slå sammen runs med bare små hull (prikker over i, aksenter)
        merged = []
        for r in runs:
            if merged and r[0] - merged[-1][1] < blank:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        hang = merged[0][1] if merged and merged[0][0] <= mm_to_px(1, dpi) else 0
        title = [r for r in merged if r[0] > hang and 2.8 * s <= r[1] - r[0] <= 12 * s
                 and not _has_staff(lines6[a1 + r[0]:a1 + r[1]], dpi, s)]   # ikke et notesystem vi ikke fant
        if not title:
            continue
        # del i det største tomme båndet over overskriften (dynamikk o.l. under systemet over blir med opp)
        edges = [0] + [v for r in merged if r[1] <= title[0][0] for v in r] + [title[0][0]]
        best, cut = 0, None
        for k in range(0, len(edges) - 1, 2):
            g0, g1 = edges[k], edges[k + 1]
            if g1 - g0 > best:
                best, cut = g1 - g0, (g0 + g1) // 2
        if cut is None or best < blank:
            continue
        out.append(int(a1 + cut))
    return out


def auto_crop(img: np.ndarray, dpi: int = 300, margin_mm: float = 5.0, clean: bool = True):
    """Beskjær til innholdet + marg. Returnerer (bilde, rect) eller (img, None) hvis tom."""
    out, rect, inner = auto_crop_inner(img, dpi, margin_mm)
    if rect is None:
        return img, None
    if clean and margin_mm > 0:
        out = clean_outside(out, inner)
    return out, rect


def crop(img: np.ndarray, rect) -> np.ndarray:
    x0, y0, x1, y1 = [int(v) for v in rect]
    return img[y0:y1, x0:x1].copy()


def detect_skew(img: np.ndarray, dpi: int = 300, max_angle: float = 8.0) -> float:
    """Finn skjevhet i grader ut fra notelinjer / tekstlinjer (vannrette linjer)."""
    if _tiny(img):
        return 0.0
    gray = to_gray(img)
    scale = 150 / dpi if dpi > 150 else 1.0
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    mask = _ink_mask(small, int(dpi * scale))
    w = small.shape[1]
    if np.count_nonzero(mask) > 0.3 * mask.size:
        return 0.0
    # Bare tynne streker: tette flater (svart banner, notehoder, tykk skrift) gir «linjer» i alle vinkler
    k = max(3, mm_to_px(1.2, int(dpi * scale)))
    mask = cv2.subtract(mask, cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, 1), np.uint8)))
    lines = cv2.HoughLinesP(mask, 1, np.pi / 1800, threshold=100,
                            minLineLength=int(w * 0.25), maxLineGap=int(w * 0.02))
    angles, weights = [], []
    if lines is not None:
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
            a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(a) <= max_angle:
                angles.append(a)
                weights.append(np.hypot(x2 - x1, y2 - y1))
    if len(angles) >= 3:
        order = np.argsort(angles)
        a = np.array(angles)[order]
        cw = np.cumsum(np.array(weights)[order])
        return float(a[np.searchsorted(cw, cw[-1] / 2)])  # vektet median
    # For få linjer (omslag, tittelside med lite tekst): ikke gjett – en rett side skal ikke vris
    return 0.0


def rotate(img: np.ndarray, angle_deg: float) -> np.ndarray:
    """Roter fritt (positiv = mot klokka i bildekoordinater) med hvit fyll."""
    if abs(angle_deg) < 0.15:
        return img  # så lite at det ikke synes – unngå unødvendig omsampling (gjør bildet mykere)
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    border = 255 if img.ndim == 2 else (255, 255, 255)
    return cv2.warpAffine(img, M, (nw, nh), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def deskew(img: np.ndarray, dpi: int = 300):
    angle = detect_skew(img, dpi)
    return rotate(img, angle), angle


def rotate90(img: np.ndarray, clockwise: bool = True) -> np.ndarray:
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE if clockwise else cv2.ROTATE_90_COUNTERCLOCKWISE)


def _shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Flytt bildet dx, dy piksler (kantene gjentas)."""
    m = max(abs(dx), abs(dy))
    if m == 0:
        return a
    p = cv2.copyMakeBorder(a, m, m, m, m, cv2.BORDER_REPLICATE)
    h, w = a.shape[:2]
    return p[m - dy:m - dy + h, m - dx:m - dx + w]


def _thin_line_depth(f: np.ndarray, dpi: int) -> np.ndarray:
    """Hvor mye mørkere hver piksel er enn begge sider på tvers av en tynn strek (notelinjer, halser, buer,
    tynn tekst). Kanter på notehoder og tykke streker har mørkt på den ene siden og gir 0 – de blir ikke tykkere.
    Det glattes langs streken først, så korn i papiret ikke regnes som streker."""
    if dpi > 330:   # regn ut i 300 dpi og skaler opp – raskere, og strekene er uansett myke
        small = cv2.resize(f, None, fx=300 / dpi, fy=300 / dpi, interpolation=cv2.INTER_AREA)
        d = _thin_line_depth(small, 300)
        return cv2.resize(d, (f.shape[1], f.shape[0]), interpolation=cv2.INTER_LINEAR)
    n = 9
    diag = np.eye(n, dtype=np.float32) / n
    smoothed = (  # (glattet langs strekretningen, steg på tvers)
        (cv2.blur(f, (n, 1)), (0, 1)),                                   # vannrett
        (cv2.blur(f, (1, n)), (1, 0)),                                   # loddrett
        (cv2.filter2D(f, -1, diag, borderType=cv2.BORDER_REPLICATE), (1, -1)),          # skrå ↘
        (cv2.filter2D(f, -1, diag[:, ::-1].copy(), borderType=cv2.BORDER_REPLICATE), (1, 1)),  # skrå ↗
    )
    best = np.zeros_like(f)
    for a, (px, py) in smoothed:
        for dist in (2, 3):
            dx, dy = px * dist, py * dist
            if px and py:   # skrå: samme avstand som rett
                dx, dy = int(round(px * dist / 1.414)) or px, int(round(py * dist / 1.414)) or py
            np.maximum(best, np.minimum(_shift(a, dx, dy), _shift(a, -dx, -dy)) - a, out=best)
    return best


def enhance(img: np.ndarray, dpi: int = 300, mode: str = "gray", strength: int = 5) -> np.ndarray:
    """Hvit bakgrunn og kraftig svart blekk.

    mode = "gray": gråtoner med hvit bakgrunn (beholder blyantnotater/merknader)
    mode = "bw":   ren svart/hvitt (minst fil, skarpest utskrift)
    mode = "color": som gråtone, men beholder fargene (hvitt papir, mettede farger)
    strength 0–10: hvor mye mørkere blekket gjøres (5 = standard)

    Resultatet er alltid et gråtonebilde med myke (antialiaserte) kanter, så strekene beholder riktig
    tykkelse. «bw» gir en bratt kurve (nesten ren svart/hvitt); selve omgjøringen til 1-bit skjer først
    i PDF-en, i dobbel oppløsning (se pdfout.py), så kantene ikke blir taggete.
    """
    if _tiny(img):
        return to_gray(img).copy() if not (mode == "color" and img is not None and img.ndim == 3) else img.copy()
    gray = to_gray(img)
    norm = _flatten(gray, dpi)  # bakgrunn ≈ 255 overalt (fjerner skygge og gulnet papir)

    # Hvor mørk er kjernen i blekket? Skanneren gir ofte bare mellomgrått (100–140).
    _, inkmask = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    inkmask = cv2.erode(inkmask, np.ones((3, 3), np.uint8))  # bare innsiden av strekene, ikke kantene
    ink = norm[inkmask > 0]
    ink_level = float(np.median(ink)) if ink.size > 200 else 80.0
    ink_level = min(ink_level, 200.0)
    s = max(0, min(10, strength)) / 10.0
    x = np.arange(256, dtype=np.float32)
    if mode != "color":
        # Tynne streker blir aldri like mørke som notehodene i skannet. Gjør dem mørkere og jevne,
        # ellers blir de grå og prikkete («kornete») etter forbedringen.
        f = norm.astype(np.float32)
        depth = _thin_line_depth(f, dpi)
        f -= (1.2 + 0.8 * s) * np.maximum(0.0, depth - 12.0)
        norm = np.clip(f, 0, 255).astype(np.uint8)

    if mode == "bw":
        # Kanten på en strek ligger midt mellom blekk og papir. Styrke flytter terskelen litt
        # mot papiret (tykkere/svartere) eller mot blekket (tynnere).
        mid = (ink_level + 255.0) / 2
        c = mid + (s - 0.5) * 0.6 * (255.0 - mid)
        half = 12.0
        t = np.clip((x - (c - half)) / (2 * half), 0, 1)
    else:
        # Lineær kurve: blekkets kjerne -> svart, papir -> hvitt. Ingen gamma, så strekene ikke blir fetere.
        black = ink_level + (s - 0.3) * 0.5 * (255.0 - ink_level)
        black = max(0.0, black)
        white = 248.0 - 8.0 * s
        t = np.clip((x - black) / max(1.0, white - black), 0, 1)
    lut = (255 * t).astype(np.uint8)
    if mode == "color" and img.ndim == 3:
        return _color_levels(img, s)
    return cv2.LUT(norm, lut)


def _color_levels(img: np.ndarray, s: float) -> np.ndarray:
    """Farge: bare lys og kontrast for hele siden – samme justering i alle kanaler, så fargene beholdes.
    (Utjevningen som gjør papiret hvitt ville bleket et farget omslag, fordi den tror omslagsfargen er papir.)"""
    small = img[::4, ::4]
    mins = small.min(axis=2)
    lo = float(np.percentile(mins, 0.5 + 1.5 * s))                 # svartpunkt
    hi = float(np.percentile(small.max(axis=2), 99.7))              # hvitpunkt: det lyseste på siden
    # Lite blekk på siden (et par notelinjer, et nesten tomt omslag): da havner prosentilen på papiret,
    # og hele siden ville blitt mørk. Svartpunktet får aldri ligge mer enn en tredjedel opp fra det mørkeste.
    darkest = float(np.percentile(mins, 0.05))
    lo = min(lo, darkest + 0.35 * max(0.0, hi - darkest))
    hi = max(hi, lo + 40.0)
    x = np.arange(256, dtype=np.float32)
    lut = (255 * np.clip((x - lo) / (hi - lo), 0, 1)).astype(np.uint8)
    return cv2.LUT(img, lut)


def _flatten(gray: np.ndarray, dpi: int) -> np.ndarray:
    """Del på et estimat av papirfargen, så papiret blir jevnt hvitt (også i skyggen mot bokryggen)."""
    k = mm_to_px(15, dpi)
    small = cv2.resize(gray, None, fx=0.25, fy=0.25)
    bg = cv2.medianBlur(small, max(3, (k // 4) | 1))
    # NB: en helt svart flate større enn ~8 mm (svart banner, omslag) blir også lysnet – det er prisen for at dyp
    # skygge mot bokryggen blir hvit. Omslag skannes best med «Farge», som ikke utjevner papiret.
    bg = cv2.resize(bg, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(gray, np.maximum(bg, 1), scale=255)


def binarize(img: np.ndarray, dpi: int):
    """Ren svart/hvitt i dobbel oppløsning med glatte kanter. Returnerer (bilde, ny_dpi)."""
    gray = to_gray(img)
    if dpi >= 600:   # allerede høy oppløsning – ingen grunn til å doble
        _, bw = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
        return bw, dpi
    up = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(up, 127, 255, cv2.THRESH_BINARY)
    return bw, dpi * 2


def is_blank(img: np.ndarray, dpi: int = 300) -> bool:
    return content_bbox(img, dpi) is None and content_bbox(img, dpi, floor=FAINT_FLOOR) is None


def despeckle(img: np.ndarray, dpi: int = 300, max_mm: float = 0.45) -> np.ndarray:
    """Fjern små isolerte prikker (støv på glasset). Notehoder og staccato er større."""
    if _tiny(img):
        return img.copy()
    gray = to_gray(img)
    out = img.copy()
    mask = _ink_mask(gray, dpi)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    lim = mm_to_px(max_mm, dpi)
    small = np.zeros(n, bool)
    small[1:] = (stats[1:, cv2.CC_STAT_WIDTH] <= lim) & (stats[1:, cv2.CC_STAT_HEIGHT] <= lim)
    kill = small[labels]
    if not kill.any():
        return out
    kill = cv2.dilate(kill.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    out[kill] = 255
    return out


# ---------------------------------------------------------------- komprimert lagring i minnet

def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        raise RuntimeError("Klarte ikke å komprimere bildet")
    return buf.tobytes()


def encode_jpg(img: np.ndarray, quality: int = 95) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Klarte ikke å komprimere bildet")
    return buf.tobytes()


def decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError("Klarte ikke å lese bildet")
    return img


def thumbnail(img: np.ndarray, w: int = 120, h: int = 166) -> np.ndarray:
    ih, iw = img.shape[:2]
    s = min(w / iw, h / ih)
    return cv2.resize(img, (max(1, int(iw * s)), max(1, int(ih * s))), interpolation=cv2.INTER_AREA)


def is_grayish(img: np.ndarray, tol: int = 10) -> bool:
    """Er et fargebilde egentlig grått? (Skanneren gir ofte 3 kanaler også i gråtonemodus.)"""
    if img.ndim == 2:
        return True
    small = img[::8, ::8].astype(np.int16)
    return int(np.abs(small[..., 0] - small[..., 1]).max()) < tol and \
        int(np.abs(small[..., 1] - small[..., 2]).max()) < tol
