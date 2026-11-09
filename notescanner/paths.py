"""Hvor NoteScanner leser og skriver filer.

Alt brukeren lager (økt, «Lagret nylig», logger) ligger i en datamappe per bruker, aldri ved siden av programmet –
programmappa byttes ut ved hver oppdatering og er ikke skrivbar ved en installasjon for alle brukere.

  Windows:  %LOCALAPPDATA%\\NoteScanner        (samme mappe som tidligere versjoner brukte)
  ellers:   $XDG_DATA_HOME/NoteScanner eller ~/.local/share/NoteScanner
  test:     $NOTESCANNER_HOME (overstyrer alt over)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

APP = "NoteScanner"
LOG_MAX_BYTES = 1_000_000      # hver loggfil roteres ved 1 MB (to gamle beholdes)


def data_dir() -> Path:
    env = os.environ.get("NOTESCANNER_HOME")
    if env:
        d = Path(env)
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        d = Path(os.environ["LOCALAPPDATA"]) / APP
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        d = Path(base) / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_dir() -> Path:
    return data_dir() / "session"


def archive_dir() -> Path:
    return data_dir() / "lagret"


def log_dir() -> Path:
    d = data_dir() / "logg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ui_dir() -> Path:
    """Små bildefiler stilarket trenger (avkrysning, piler). Ikke i %TEMP% – den kan inneholde æøå."""
    d = data_dir() / "ui"
    d.mkdir(parents=True, exist_ok=True)
    return d


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource(*parts: str) -> Path:
    """Fil som følger med programmet (f.eks. notescanner/winocr.ps1), både fra kildekode og i exe-versjonen."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def sanitize(text: str) -> str:
    """Fjern brukernavn og hjemmemappe fra tekst som kan havne i en offentlig feilrapport."""
    home = str(Path.home())
    out = text
    for h in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if len(h) > 3:
            out = out.replace(h, "%USERPROFILE%")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(user) > 2:
        out = out.replace(f"\\{user}\\", "\\%USERNAME%\\").replace(f"/{user}/", "/%USERNAME%/")
    return out


def append_log(name: str, text: str) -> Path:
    """Legg til tekst i en loggfil i loggmappa. Roterer ved LOG_MAX_BYTES (name.1, name.2)."""
    path = log_dir() / name
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            for k in (2, 1):
                src = path.with_name(f"{name}.{k - 1}") if k > 1 else path
                dst = path.with_name(f"{name}.{k}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(sanitize(text))
    except OSError:
        pass
    return path


def error_log(text: str, version: str = "") -> Path:
    stamp = f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S}" + (f" (v{version})" if version else "") + " ---\n"
    return append_log("feillogg.txt", stamp + text.rstrip() + "\n")


def tail(name: str, max_chars: int = 6000) -> str:
    path = log_dir() / name
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-max_chars:]


def documents_dir() -> Path:
    """Brukerens Dokumenter-mappe (også når den er flyttet til OneDrive)."""
    try:
        from PySide6.QtCore import QStandardPaths
        loc = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        if loc:
            return Path(loc)
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / "Documents"
