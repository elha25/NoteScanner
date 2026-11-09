"""Koble knappene på skanneren til NoteScanner (Windows WIA-hendelser). Krever administrator.

Fra den installerte appen:  NoteScanner.exe --registrer-knapp   (eller knappen under Flere valg)
Fjerne igjen:               NoteScanner.exe --fjern-knapp
Etterpå velger du NoteScanner for knappen under Skanneregenskaper → Hendelser.
"""
from __future__ import annotations

import sys
from pathlib import Path

NAME = "NoteScanner"
DESC = "Skann noter til PDF"


def command() -> tuple[str, str]:
    """(kommandolinje som Windows kjører ved knappetrykk, ikon)."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return f'"{exe}" /StiDevice:%1 /StiEvent:%2', f"{exe},0"
    root = Path(__file__).resolve().parent.parent
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pyw}" "{root / "NoteScanner.pyw"}" /StiDevice:%1 /StiEvent:%2', f"{sys.executable},0"


def register(remove: bool = False) -> list[str]:
    """Registrer (eller fjern) for alle knappene på alle skannere. Returnerer linjer til visning."""
    import pythoncom
    import win32com.client
    out = []
    pythoncom.CoInitialize()
    try:
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        cmd, icon = command()
        found = False
        for i in range(1, dm.DeviceInfos.Count + 1):
            info = dm.DeviceInfos.Item(i)
            if info.Type != 1:              # bare skannere
                continue
            found = True
            out.append(f"Skanner: {info.Properties.Item('Name').Value}")
            try:
                dev = info.Connect()
            except Exception as e:  # noqa: BLE001
                out.append(f"  kunne ikke koble til ({e})")
                continue
            for j in range(1, dev.Events.Count + 1):
                ev = dev.Events.Item(j)
                if not ev.Type & 2:         # bare knapper («action»), ikke tilkobling o.l.
                    continue
                try:
                    if remove:
                        dm.UnregisterPersistentEvent(cmd, NAME, DESC, icon, ev.EventID, info.DeviceID)
                        out.append(f"  fjernet: {ev.Name}")
                    else:
                        dm.RegisterPersistentEvent(cmd, NAME, DESC, icon, ev.EventID, info.DeviceID)
                        out.append(f"  registrert for: {ev.Name}")
                except Exception as e:  # noqa: BLE001
                    out.append(f"  {ev.Name}: feilet ({e})")
        if not found:
            out.append("Fant ingen skanner som er koblet til.")
    finally:
        pythoncom.CoUninitialize()
    return out


def run_elevated(arg: str) -> bool:
    """Start denne appen på nytt som administrator med `arg` (Windows spør om tillatelse)."""
    import ctypes
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, arg
    else:
        root = Path(__file__).resolve().parent.parent
        exe = str(Path(sys.executable).with_name("python.exe"))
        params = f'"{root / "NoteScanner.pyw"}" {arg}'
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return r > 32
