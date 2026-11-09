"""Skanning via Windows WIA (fungerer med Canon CanoScan LiDE 300 og de fleste flatbed-skannere)."""
from __future__ import annotations

import sys
import time

import cv2
import numpy as np

WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"   # ukomprimert: ingen PNG-koding i Windows etter skannet
WIA_DEVICE_SCANNER = 1

# WIA-egenskaper
P_INTENT = 6146        # 1 = farge, 2 = gråtone, 4 = tekst/svart-hvitt
P_HRES = 6147
P_VRES = 6148
P_XPOS = 6149
P_YPOS = 6150
P_XEXT = 6151          # bredde i piksler (på tvers av skannerhodet)
P_YEXT = 6152          # lengde i piksler (så langt hodet går) – det er denne som bestemmer tiden
P_DATATYPE = 4103     # 0 = svart-hvitt, 2 = gråtone, 3 = farge
P_DEPTH = 4104        # bit per piksel
P_BRIGHT = 6154
P_CONTRAST = 6155

INTENT = {"color": 1, "gray": 2, "bw": 4}
DATATYPE = {"color": (3, 24), "gray": (2, 8), "bw": (2, 8)}

# Tider for siste skanning (vises i skannelogg.txt): oppkobling, selve skannet, dekoding
last_timing: dict = {}


class ScannerError(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "win32"


def _prop(props, pid: int):
    for i in range(1, props.Count + 1):
        p = props.Item(i)
        if p.PropertyID == pid:
            return p
    return None


def _set(props, pid: int, value) -> bool:
    p = _prop(props, pid)
    if p is None:
        return False
    try:
        p.Value = value
        return True
    except Exception:  # noqa: BLE001
        return False


def _max(props, pid: int) -> int | None:
    p = _prop(props, pid)
    try:
        return int(p.SubTypeMax) if p is not None else None
    except Exception:  # noqa: BLE001
        return None


def _value(props, pid: int):
    p = _prop(props, pid)
    try:
        return int(p.Value) if p is not None else None
    except Exception:  # noqa: BLE001
        return None


def _valid(props, pid: int) -> str:
    """Lovlige verdier for en egenskap (til skanneloggen)."""
    p = _prop(props, pid)
    if p is None:
        return "-"
    try:
        if p.SubType == 2:                       # liste
            return ",".join(str(v) for v in p.SubTypeValues)
        if p.SubType == 1:                       # område
            return f"{p.SubTypeMin}-{p.SubTypeMax}"
    except Exception:  # noqa: BLE001
        pass
    return "?"


def configure(props, dpi: int, mode: str, area_mm: tuple[float, float] | None = None) -> tuple[int, int]:
    """Still inn modus, oppløsning og utsnitt. Returnerer (bredde, lengde) i piksler.

    area_mm = (bredde, lengde) målt fra hjørnemerket på glasset. None = hele glassplata.
    Rekkefølgen betyr noe: modus -> oppløsning -> posisjon -> størrelse. Oppløsningen leses tilbake etterpå:
    noen drivere (f.eks. Brother) godtar ikke alle verdier eller nullstiller den, og da må utsnittet regnes
    ut fra det skanneren faktisk bruker.
    """
    _set(props, P_INTENT, INTENT.get(mode, 2))
    # Si eksplisitt fra om 8-bit gråtone, så driveren ikke skanner i farge (eller 16 bit) og regner om –
    # LiDE 300 bruker 1,3 ms per linje i gråtone ved 600 dpi, men 3,8 ms i farge.
    dt, depth = DATATYPE.get(mode, (2, 8))
    _set(props, P_DATATYPE, dt)
    _set(props, P_DEPTH, depth)
    _set(props, P_HRES, dpi)
    _set(props, P_VRES, dpi)
    hres, vres = _value(props, P_HRES), _value(props, P_VRES)
    if hres != dpi or vres != dpi:              # prøv igjen i motsatt rekkefølge
        _set(props, P_VRES, dpi)
        _set(props, P_HRES, dpi)
        hres, vres = _value(props, P_HRES), _value(props, P_VRES)
    actual = hres or dpi
    _set(props, P_XPOS, 0)
    _set(props, P_YPOS, 0)
    mx, my = _max(props, P_XEXT), _max(props, P_YEXT)
    raw_max = (mx, my)
    # Største bredde oppgitt for lav oppløsning (ingen flatbed er smalere enn ~200 mm): skaler opp
    if mx and actual > 300 and mx / actual * 25.4 < 160:
        f = actual / 300
        mx, my = round(mx * f), (round(my * f) if my else my)
    w, h = mx, my
    if area_mm:
        if mx:
            w = min(mx, max(1, round(area_mm[0] / 25.4 * actual)))
        if my:
            h = min(my, max(1, round(area_mm[1] / 25.4 * actual)))
    if w:
        _set(props, P_XEXT, w)
    if h:
        _set(props, P_YEXT, h)
    last_timing.update(dpi_set=dpi, dpi_read=hres, vres_read=vres, res_valid=_valid(props, P_HRES),
                       max_raw=raw_max, ext=(_value(props, P_XEXT), _value(props, P_YEXT)), ext_wanted=(w, h))
    return w or 0, h or 0


# Vanlige WIA-feilkoder -> forklaring på norsk (HRESULT 0x8021xxxx)
WIA_ERRORS = {
    0x80210001: "Skanneren rapporterte en generell feil. Slå den av og på og prøv igjen.",
    0x80210002: "Papiret har satt seg fast.",
    0x80210003: "Det ligger ikke noe papir i materen.",
    0x80210004: "Skanneren har et problem med papiret.",
    0x80210005: "Skanneren er ikke tilgjengelig (frakoblet, slått av eller i dvale). Sjekk kabel/nett og prøv igjen.",
    0x80210006: "Skanneren er opptatt med noe annet (f.eks. et annet skanneprogram). Lukk det og prøv igjen.",
    0x80210007: "Skanneren varmer opp. Vent litt og prøv igjen.",
    0x80210008: "Skanneren trenger at noen gjør noe med den (se displayet på maskinen).",
    0x80210009: "Skanneren ble koblet fra under skanningen.",
    0x8021000A: "Skanneren svarte ikke på en innstilling.",
    0x8021000B: "Skanneren mistet forbindelsen. Koble til på nytt.",
    0x8021000C: "Lokket på skanneren er åpent.",
    0x8021000D: "Skannerlampen er av.",
    0x80210015: "Fant ingen skanner.",
}


def explain(e: Exception) -> str:
    """Lesbar feilmelding for en COM-feil fra skanneren."""
    code = None
    args = getattr(e, "args", ())
    if args and isinstance(args[0], int):
        code = args[0] & 0xFFFFFFFF
    exc = args[2] if len(args) > 2 and isinstance(args[2], tuple) else None
    if exc and len(exc) > 5 and isinstance(exc[5], int):      # feilkoden fra selve driveren ligger her
        inner = exc[5] & 0xFFFFFFFF
        if inner in WIA_ERRORS:
            code = inner
    if code in WIA_ERRORS:
        return WIA_ERRORS[code]
    detail = (exc[2] if exc and len(exc) > 2 and exc[2] else None) or (args[1] if len(args) > 1 else str(e))
    return f"Skanneren svarte med feil ({hex(code) if code else '?'}): {detail}"


def _connect(device_id: str | None):
    """Koble til valgt skanner, eller den første som svarer (en avslått skanner skal ikke stoppe en annen)."""
    import win32com.client
    dm = win32com.client.Dispatch("WIA.DeviceManager")
    candidates = []
    for i in range(1, dm.DeviceInfos.Count + 1):
        d = dm.DeviceInfos.Item(i)
        if d.Type != WIA_DEVICE_SCANNER:
            continue
        if device_id is None or d.DeviceID == device_id or device_id in d.DeviceID:
            candidates.append(d)
    if not candidates:
        if device_id:
            raise ScannerError("Fant ikke den valgte skanneren. Er den slått på og koblet til? "
                               "(Du kan velge skanner under Skanning.)")
        raise ScannerError("Fant ingen skanner. Er skanneren koblet til og slått på, og er driveren fra "
                           "produsenten installert?")
    last = None
    for d in candidates:
        try:
            return d.Connect()
        except Exception as e:  # noqa: BLE001
            last = e
    raise ScannerError(explain(last))


def _formats(item) -> set[str]:
    try:
        return {str(f).upper() for f in item.Formats}
    except Exception:  # noqa: BLE001
        return set()


def _with_com(fn):
    """Kjør fn med COM startet i denne tråden. fn skal returnere vanlige Python-data, så alle
    COM-objekter er sluppet før COM avsluttes."""
    import pythoncom
    pythoncom.CoInitialize()
    try:
        return fn()
    finally:
        pythoncom.CoUninitialize()


def list_scanners():
    """Returnerer [(device_id, navn), ...]."""
    if not available():
        return []

    def work():
        import win32com.client
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        out = []
        for i in range(1, dm.DeviceInfos.Count + 1):
            info = dm.DeviceInfos.Item(i)
            if info.Type == WIA_DEVICE_SCANNER:
                out.append((str(info.DeviceID), str(info.Properties.Item("Name").Value)))
        return out
    return _with_com(work)


def glass_size_mm(device_id: str | None = None) -> tuple[float, float] | None:
    """Største område skanneren kan ta, i mm (bredde, lengde)."""
    if not available():
        return None

    def work():
        props = _connect(device_id).Items.Item(1).Properties
        _set(props, P_HRES, 300)
        _set(props, P_VRES, 300)
        mx, my = _max(props, P_XEXT), _max(props, P_YEXT)
        return (mx / 300 * 25.4, my / 300 * 25.4) if mx and my else None
    return _with_com(work)


def scan(dpi: int = 300, mode: str = "gray", device_id: str | None = None,
         area_mm: tuple[float, float] | None = None) -> np.ndarray:
    """Skann (hele glassplata eller et utsnitt fra hjørnemerket) og returner bildet (BGR eller gråtone)."""
    if not available():
        raise ScannerError("Skanning via WIA fungerer bare på Windows.")
    import pywintypes

    def work():
        last_timing.clear()
        t0 = time.perf_counter()
        item = _connect(device_id).Items.Item(1)
        w, h = configure(item.Properties, dpi, mode, area_mm)
        t1 = time.perf_counter()
        fmts = _formats(item)
        # BMP er raskest (ingen PNG-koding etter skannet), men bare hvis driveren sier den kan det –
        # et mislykket forsøk kan ellers bety at siden skannes to ganger
        fmt = "bmp" if (not fmts or WIA_FORMAT_BMP in fmts) else "png"
        image = item.Transfer(WIA_FORMAT_BMP if fmt == "bmp" else WIA_FORMAT_PNG)
        t2 = time.perf_counter()
        data = bytes(image.FileData.BinaryData)
        t3 = time.perf_counter()
        bmp_dpi = None
        if data[:2] == b"BM" and len(data) > 46:      # oppløsningen skanneren selv skrev i bildet
            ppm = int.from_bytes(data[38:42], "little")
            bmp_dpi = round(ppm * 0.0254) if ppm else None
        last_timing.update(connect=t1 - t0, transfer=t2 - t1, copy=t3 - t2, fmt=fmt, w=w, h=h,
                           bytes=len(data), dpi_file=bmp_dpi)
        return data
    try:
        data = _with_com(work)
    except pywintypes.com_error as e:
        raise ScannerError(explain(e)) from e

    t = time.perf_counter()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ScannerError("Klarte ikke å lese bildet fra skanneren.")
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    if mode != "color" and arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)   # BMP i gråtone kan komme som 24 bit
    last_timing["decode"] = time.perf_counter() - t
    last_timing["lines"] = arr.shape[0]
    return arr
