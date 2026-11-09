"""Se etter ny versjon på GitHub og installer den (bare i den installerte exe-versjonen).

Flyt: `check()` henter siste versjon fra GitHub (offentlig repo, ingen innlogging). Finnes en nyere, viser appen
en stripe. `download()` henter installasjonsfila og sjekksummen, kontrollerer SHA-256, og `launch_installer()`
starter den stille (Inno Setup) – appen lukker seg selv først, og installasjonen starter appen igjen.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "elha25/NoteScanner"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 15


@dataclass
class Release:
    version: str
    notes: str
    page: str
    installer_url: str | None
    checksum_url: str | None
    size: int = 0


def parse_version(v: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", v)[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "NoteScanner-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def check() -> Release | None:
    """Siste publiserte versjon, eller None (ingen nett, ingen versjon, GitHub svarer ikke)."""
    data = json.loads(_get(API).decode("utf-8"))
    if data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name", "")).lstrip("vV")
    inst = chk = None
    size = 0
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.lower().endswith(".exe") and "setup" in name.lower():
            inst, size = a.get("browser_download_url"), int(a.get("size") or 0)
        elif name.lower().endswith(".exe.sha256"):
            chk = a.get("browser_download_url")
    return Release(version, str(data.get("body") or ""), str(data.get("html_url") or RELEASES_PAGE), inst, chk, size)


def can_self_update() -> bool:
    """Bare den installerte Windows-versjonen kan oppdatere seg selv (fra kildekode: last ned selv)."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def download(rel: Release, progress=None) -> Path:
    """Last ned installasjonsfila til en midlertidig mappe og kontroller sjekksummen. Kaster ved feil."""
    if not rel.installer_url or not rel.checksum_url:
        raise RuntimeError("Versjonen mangler installasjonsfil eller sjekksum.")
    expected = _get(rel.checksum_url, "application/octet-stream").decode("ascii", "replace").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("Ugyldig sjekksum fra GitHub.")
    target = Path(tempfile.mkdtemp(prefix="notescanner-update-")) / f"NoteScanner-Setup-{rel.version}.exe"
    req = urllib.request.Request(rel.installer_url, headers={"User-Agent": "NoteScanner-updater"})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(target, "wb") as fh:
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError("Nedlastingen ble ødelagt (sjekksummen stemmer ikke). Prøv igjen.")
    return target


def launch_installer(path: Path):
    """Start installasjonen uten spørsmål, et par sekunder etter at appen har lukket seg (den holder ellers filene
    låst). Installasjonen starter appen igjen når den er ferdig, og økta hentes fram."""
    args = f'"{path}" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS'
    if sys.platform == "win32":
        cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        flags = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(f'"{cmd}" /c ping -n 4 127.0.0.1 >nul & start "" {args}', creationflags=flags,
                         close_fds=True, cwd=os.path.dirname(path))
    else:
        subprocess.Popen([str(path)], close_fds=True)
