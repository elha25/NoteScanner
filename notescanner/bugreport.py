"""«Rapporter feil»: samle versjon, systeminfo og de siste loggene (uten personlige stier) og åpne en ferdig
utfylt GitHub-sak. Bilder av noter tas aldri med automatisk – sakene er offentlige, og notene er beskyttet."""
from __future__ import annotations

import platform
import sys
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths

REPO_URL = "https://github.com/elha25/NoteScanner"
MAX_URL = 7000          # GitHub/nettlesere godtar ikke vilkårlig lange adresser


def system_info(version: str, extra: dict | None = None) -> str:
    lines = [
        f"NoteScanner {version} ({'installert' if paths.frozen() else 'fra kildekode'})",
        f"Windows/OS: {platform.platform()}",
        f"Python {platform.python_version()} ({platform.architecture()[0]})",
    ]
    try:
        import cv2
        lines.append(f"OpenCV {cv2.__version__}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from PySide6 import __version__ as pyside
        lines.append(f"PySide6 {pyside}")
    except Exception:  # noqa: BLE001
        pass
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    return paths.sanitize("\n".join(lines))


def log_excerpt(max_chars: int = 2500) -> str:
    err = paths.tail("feillogg.txt", max_chars)
    scan = paths.tail("skannelogg.txt", 1200)
    parts = []
    if err.strip():
        parts.append("== feillogg ==\n" + err.strip())
    if scan.strip():
        parts.append("== skannelogg (siste) ==\n" + scan.strip())
    return paths.sanitize("\n\n".join(parts)) or "(ingen logg)"


def issue_url(version: str, info: str, logs: str) -> str:
    def build(log_text: str) -> str:
        q = urllib.parse.urlencode({"template": "feil.yml", "versjon": version, "system": info, "logg": log_text},
                                   quote_via=urllib.parse.quote)
        return f"{REPO_URL}/issues/new?{q}"
    url = build(logs)
    while len(url) > MAX_URL and len(logs) > 200:      # kort ned loggen (eldste først) til adressen passer
        logs = "…" + logs[len(logs) // 4:]
        url = build(logs)
    return url


def save_zip(version: str, info: str, folder: Path | None = None) -> Path:
    """Feilrapport som fil (for e-post eller for å dra inn i GitHub-saken). Ingen bilder."""
    folder = folder or paths.documents_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"NoteScanner-feilrapport-{datetime.now():%Y%m%d-%H%M}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", info)
        for name in ("feillogg.txt", "feillogg.txt.1", "skannelogg.txt"):
            p = paths.log_dir() / name
            if p.exists():
                z.writestr(name, paths.sanitize(p.read_text(encoding="utf-8", errors="replace")))
    return out


def python_info() -> str:
    return sys.version
