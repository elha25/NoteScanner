"""Hent avsnittet for én versjon fra CHANGELOG.md (brukes som tekst på GitHub-versjonen og i appen)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def notes(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[?{re.escape(version)}\]?.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    body = m.group(1).strip() if m else ""
    return body or f"NoteScanner {version}"


if __name__ == "__main__":
    out = notes(sys.argv[1])
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(out + "\n\nLast ned **NoteScanner-Setup-" + sys.argv[1] + ".exe** og kjør den. "
                                     "Windows kan vise «Windows beskyttet PC-en» første gang – trykk "
                                     "*Mer info → Kjør likevel*.\n", encoding="utf-8")
    else:
        print(out)
