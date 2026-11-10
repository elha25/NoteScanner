# Endringer

## 2.3.1

- Bilder fra en overvåket mappe eller *Importer* bruker oppløsningen som står i bildefila (f.eks. 600 dpi fra
  Brother iPrint&Scan), ikke dpi-valget i appen. Før ble siden feil størrelse i PDF-en og beskjæringen dårligere
  når de to ikke stemte.

## 2.3.0

Første versjon på GitHub, med installasjonsfil.

- **Installasjonsfil for Windows** – ingen Python å installere. Installeres for din bruker, uten administrator.
- **Oppdatering i appen:** NoteScanner ser etter ny versjon ved oppstart (kan slås av under *Flere valg*), viser hva
  som er nytt og oppdaterer seg med ett klikk. Økta tas vare på.
- **Rapporter feil** under *Flere valg* åpner en ferdig utfylt sak på GitHub med versjon og logg (uten
  personlige mappenavn), eller lagrer en zip du kan sende.
- **Velg skanner** når flere er koblet til. Tydeligere feilmeldinger fra skanneren (opptatt, lokk åpent, frakoblet).
- **Koble skannerknappen** til NoteScanner fra *Flere valg*.
- Tittel-lesing: titler som «By the Rivers of Babylon», «Mr. Sandman», «Largo», «Air», «76 Trombones» og
  «Mass in B minor» leses riktig, og «Tekst og musikk:», «Melodi:» o.l. fjernes fra komponistnavnet.
- Sikrere økt: en ødelagt økt-fil slettes aldri (den flyttes til side), sidene beholder riktig «hele skann»
  etter omstart, og «Nei» til å hente fram økta flytter den til side i 14 dager i stedet for å slette den.
- Lagring: tydelig melding når PDF-en er åpen i PDF-leseren, ingen halvferdige filer, ugyldige filnavn
  (CON, for lange navn) rettes, og store dokumenter bruker mye mindre minne.
- Mange rettelser i dokumentlista: slett, flytt, lim sammen og skann på nytt holder dokumentene og titlene
  dine på plass; et lagret dokument står fortsatt som lagret når en ny side blir eget dokument.
- Loggene ligger nå i `%LOCALAPPDATA%\NoteScanner\logg`.

## 2.2.1

- To korte sanger ble delt feil midt på siden – oppdelingen krever nå at feltet mellom sangene ikke har
  notelinjer.

## 2.2.0

- Brother DCP-L2627DW: riktig oppløsning (600 dpi) og overvåket mappe for Scan-knappen.
- Ny `start.bat` som finner Python også når den ikke ligger på PATH.

## 2.1.9

- «Skann siden på nytt» (Shift+Mellomrom) bytter ut en side som ikke lot seg beskjære.

## 2.1.8

- Angre beskjæring og beskjær på nytt, flere titler leses riktig.

## 2.1.7

- Lagrede stykker flyttes til «Lagret nylig» i 14 dager. Stykker med flere ark kan foldes sammen.

## 2.1.6

- Raskere skanning, «som beskåret» som standard PDF-format, tilbakestill beskjæring, beskjær når museknappen slippes.

## 2.1.5

- Beskjæringen tar ikke lenger med noter fra neste sang, og tittelen leses bedre.
