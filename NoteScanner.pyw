"""Start NoteScanner (fra kildekode: dobbeltklikk start.bat; i den installerte versjonen er dette NoteScanner.exe)."""
import os
import sys
import traceback

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _log_path():
    try:
        from notescanner import paths
        return str(paths.log_dir() / "feillogg.txt")
    except Exception:  # noqa: BLE001
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "NoteScanner-feillogg.txt")


def _fail(text):
    log = _log_path()
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write("\n--- oppstart ---\n" + text)
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, text[-1500:] + f"\n\nHele feilen er lagret i:\n{log}", "NoteScanner – feil ved oppstart", 0x10)
    else:
        print(text, file=sys.stderr)


def _import_main():
    try:
        from notescanner.app import main as m   # statisk import, så PyInstaller tar med hele pakken
        return m
    except ModuleNotFoundError as e:
        if getattr(sys, "frozen", False) or e.name != "notescanner":
            raise
        # Kildekode pakket ut med annen skrivemåte på mappa (f.eks. «NoteScanner») – Python krever samme case
        import importlib
        here = os.path.dirname(os.path.abspath(__file__))
        for name in os.listdir(here):
            if name.lower() == "notescanner" and os.path.isfile(os.path.join(here, name, "app.py")):
                return importlib.import_module(f"{name}.app").main
        raise


try:
    main = _import_main()
except ImportError as e:
    hint = ("Installasjonen er ufullstendig – installer NoteScanner på nytt." if getattr(sys, "frozen", False)
            else "Kjør start.bat (den installerer det som mangler).")
    _fail(f"Mangler en del av programmet: {e}\n\n{hint}\n\n" + traceback.format_exc())
    sys.exit(1)
except Exception:  # noqa: BLE001
    _fail(traceback.format_exc())
    sys.exit(1)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        _fail(traceback.format_exc())
        sys.exit(1)
