# NoteScanner – kontekst for Claude Code og andre utviklere

Windows-skrivebordsapp (Python + PySide6) for å skanne musikknoter til PDF. Brukeren bulk-skanner mange ark
(knapp på skanneren eller mellomrom) → hver side rettes opp, beskjæres og forbedres → en OCR-tråd leser
tittelfeltet og deler sidene i dokumenter (tittelside = nytt dokument, ellers fortsettelse) → brukeren sjekker
lista til venstre og lagrer ett eller alle dokumenter som separate PDF-er. UI-tekster og kommentarer er på norsk.

## Struktur
- `NoteScanner.pyw` – oppstart (pythonw, uten konsollvindu). I den installerte versjonen er dette `NoteScanner.exe`.
- `notescanner/__init__.py` – `__version__` (eneste sted versjonen står; release-workflowen sjekker at taggen stemmer).
- `notescanner/paths.py` – alle mapper: data (`%LOCALAPPDATA%\NoteScanner`, eller `NOTESCANNER_HOME` i tester),
  økt, «Lagret nylig», logg (`logg/feillogg.txt`, `skannelogg.txt`, `krasj.txt`), ressurser i exe-en
  (`resource()` bruker `sys._MEIPASS`), og `sanitize()` som fjerner brukernavn fra tekst i feilrapporter.
- `notescanner/app.py` – GUI (mørkt tema), én instans via QLocalServer + QLockFile, mappeovervåking.
  Tråder: ScanWorker (WIA) → ProcessThread (deskew/crop/enhance/komprimering, i rekkefølge) → OcrThread;
  SaveThread lager PDF-ene i bakgrunnen. Hovedtråden gjør bare lette ting.
  - Minne: `Page.png` er komprimert PNG; `Page.img` pakker ut via en LRU-cache på 4 sider. Angrehistorikk
    `(png, bw, last_op, join, orig)`, `HISTORY_STEPS = 8`; råskannet ligger som JPEG q95.
  - Dokumentmodell: `self.pages` er én flat liste; en side med `page.meta` (DocMeta) starter et dokument.
    `DocMeta.rev/saved_rev` = lagret-status. `page.manual` = brukeren har delt/slått sammen/flyttet –
    `_regroup()` endrer ikke grupperingen da. `page.hidden_meta` tar vare på et dokument mens «Én bok» er valgt.
    Sletting/flytting gir dokumentets meta videre til neste side, så dokumenter aldri slås sammen ved et uhell.
  - Oppdeling `auto` (tittelside = nytt dokument) eller `book` (alt i én PDF, titler → bokmerker via `_sections`).
    `Page.section` = sangnavn brukeren har skrevet (None = fra OCR, "" = ikke bokmerke).
  - Økta autolagres atomisk (p{id}.png + t{id}.png + o{id}.jpg + session.json) i `paths.session_dir()`.
    VIKTIG: `_session_ready` er False til `_offer_restore` er ferdig – `_flush_session` gjør ingenting før det.
    En økt som ikke kan leses flyttes til `session-uleselig-<tid>`; «Nei» flytter den til `forkastet/<tid>`.
    Begge slettes etter 14 dager. Side-id-er fortsetter fra høyeste id på disk (`_advance_ids`).
  - «Lagret nylig»: `remove_saved` flytter lagrede dokumenter til `paths.archive_dir()/<ms>_<id>/`;
    `_purge_archive` sletter etter `ARCHIVE_DAYS` (14). `restore_archived` legger sidene tilbake.
  - «Skann siden på nytt» (Shift+Mellomrom): `scan_page(replace_id=…)`, køen `_scan_queue`.
  - Ctrl+J `join_with_previous_image` limer to sider til ett bilde. CropView beskjærer når museknappen slippes.
  - *Flere valg*: skannervalg, «Koble skannerknappen», «Se etter ny versjon», automatisk oppdatering,
    «Rapporter feil». `--selftest` (brukes av release-workflowen på exe-en), `--registrer-knapp`, `--fjern-knapp`.
- `notescanner/updater.py` – GitHub releases/latest → `Release`; laster ned `NoteScanner-Setup-x.y.z.exe`,
  sjekker SHA-256 mot `.exe.sha256` og starter Inno Setup stille etter at appen har lukket seg.
- `notescanner/bugreport.py` – systeminfo + loggutdrag (anonymisert) → forhåndsutfylt GitHub-sak (`feil.yml`),
  URL under 7000 tegn, eller zip i Dokumenter.
- `notescanner/button.py` – registrering av skannerknappen (WIA-hendelser, krever administrator).
- `notescanner/theme.py` / `icons.py` – farger, QSS og SVG-strekikoner (QtSvg).
- `notescanner/scanner.py` – WIA-skanning med pywin32. BMP-overføring hvis driveren støtter det (ellers PNG),
  datatype/bitdybde settes eksplisitt, oppløsningen leses tilbake (noen drivere avviser/nullstiller den) og appen
  bruker faktisk dpi. `explain()` gjør WIA-feilkoder om til norsk tekst. Tider i `last_timing` → `skannelogg.txt`.
  COM-arbeid skjer i indre funksjoner via `_with_com`. `SCAN_IDLE` får bakgrunnstrådene til å vente mens
  skanneren går, så hodet ikke stopper opp.
- `notescanner/processing.py` – bildebehandling:
  - `content_bbox`: blekkmaske med bakgrunnsutjevning, kantfiltre (papirkant/lokkant `_drop_side_lines`, tynne
    streker ≤ 6 mm langs kanten, skygge fra ryggen `_edge_shadow`, brett/gutter), og `_main_page` som fjerner en
    stripe av nabosiden (blokk bak en brett som er mørkere enn papiret på begge sider, eller en liten avskåret
    blokk ute i kanten). Bleke sider prøves på nytt med `FAINT_FLOOR`.
  - `song_splits`/`find_staves` (alltid i 300 dpi): ny sang midt på siden når feltet mellom to systemer er
    ≥ 18 mm og ≥ 1,8 × vanlig mellomrom, har høy tekst og ingen notelinjer (`_has_staff`).
  - `detect_skew` (Hough på notelinjer; under tre linjer = ingen rotasjon), `enhance` (gray/bw/color,
    `_thin_line_depth`, `_color_levels`), `despeckle`. `MIN_SIDE`: bittesmå bilder går uendret gjennom.
- `notescanner/ocr.py` – finner tittelfeltet over første notesystem, kjører OCR, tolker tittel/stemme/komponist.
  - Motorer i rekkefølge: winrt-pakker (valgfritt) → `winocr.ps1` via Windows PowerShell 5.1 (standard) →
    Tesseract. Tomt/ugyldig svar fra PowerShell er en feil (ikke «ingen tekst»), og logges.
  - `parse()`: `_prepare` deler linjer ved stort mellomrom/høydehopp og slår sammen biter; tittel = størst skrift,
    gjerne midtstilt. En linje som starter med komponist-/gradsord («By the Rivers…», «Mr. Sandman») er tittel
    bare når den er klart størst og midtstilt. Tempo-/sjangerord («Largo», «Air») godtas når de er klart
    størst (`Suggestion.title_ok`). `nice_title` lager «Sjanger - Navn» i tittel-stil.
  - `Suggestion.is_title_page`: tittel med skrift ≥ 4,5 mm ELLER ≥ 2,5 × notelinjeavstand, og felt ≥ 10 mm.
- `notescanner/pdfout.py` – img2pdf rett til en midlertidig fil, bokmerker med pikepdf, så `os.replace`.
  A4 faktisk størrelse / fyll / som beskåret. Svart-hvitt som én-stripe CCITT G4.

## Bygg og publisering
- `packaging/NoteScanner.spec` (PyInstaller onedir, img2pdf som .py-fil pga. LGPL), `packaging/installer.iss`
  (Inno Setup, per bruker, `AppMutex=NoteScannerAppMutex`), `make_icon.py`, `make_version_info.py`,
  `release_notes.py` (leser avsnittet for versjonen i `CHANGELOG.md`).
- `.github/workflows/ci.yml` – tester på Windows og Linux. `release.yml` – på tag `v*` eller «Run workflow»: tester, bygger exe,
  kjører `--selftest` på den, lager installasjonsfil + portable zip + sjekksummer og publiserer en GitHub-versjon.
- Ny versjon: oppdater `notescanner/__init__.py` og `CHANGELOG.md`, push til main, og kjør «Ny versjon» under
  Actions (`gh workflow run release.yml`) – eller push en tag `vX.Y.Z`. Jobben lager taggen selv.

## Kommandoer
- Installer avhengigheter: `python -m pip install -r requirements.txt -r requirements-dev.txt`
- Tester: `python -m pytest` (syntetiske noter i `tests/synth.py` – aldri ekte, opphavsrettsbeskyttede noter i repoet)
- Røyktest av alt unntatt skanning: `python selvtest.py`
- Start: `python NoteScanner.pyw` (eller `start.bat`)
