"""Registrer NoteScanner for knappene på skanneren (kjører fra kildekode). Kjør som administrator:

    python registrer_skannerknapp.py          (registrer)
    python registrer_skannerknapp.py --fjern  (fjern)

Den installerte appen gjør det samme via Flere valg → «Koble skannerknappen til NoteScanner».
"""
import sys

from notescanner import button

if __name__ == "__main__":
    for line in button.register(remove="--fjern" in sys.argv):
        print(line)
    print("\nVelg NoteScanner for knappen under Skanneregenskaper > Hendelser.")
    input("\nTrykk Enter for å lukke …")
