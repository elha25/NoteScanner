# NoteScanner

Skann mange noter i ett strekk med en flatskanner (f.eks. Canon CanoScan LiDE 300 eller Brother DCP-L2627DW). Appen retter opp og rydder hver side, leser tittel, stemme og komponist, og deler sidene opp i dokumenter. Du sjekker lista og lagrer alt som PDF-er med ett trykk.

## Installasjon (Windows 10/11)

1. Skannerdriveren må være installert (for LiDE 300 følger den med IJ Scan Utility, for Brother med iPrint&Scan / Full Driver).
2. Last ned **NoteScanner-Setup-x.y.z.exe** fra [Releases](https://github.com/elha25/NoteScanner/releases/latest) og dobbeltklikk den. Den installeres for din bruker, uten administrator-tilgang.
3. Windows kan vise «Windows beskyttet PC-en» første gang (programmet er ikke kodesignert): trykk **Mer info → Kjør likevel**.

**Oppdatering:** NoteScanner ser etter ny versjon når den starter. Finnes det en, kommer en stripe øverst: *Hva er nytt?* viser endringene, og *Oppdater nå* laster ned, sjekker og installerer den – appen lukker seg, oppdateres og starter igjen med sidene dine. Du kan også trykke *Flere valg → Se etter ny versjon*, eller slå av den automatiske sjekken der.

**Fra kildekoden** (for utviklere): installer Python 3.11–3.13 og dobbeltklikk `start.bat`. Den installerer pakkene første gang.

**Avinstallere:** *Innstillinger → Apper → NoteScanner → Avinstaller*. Sidene dine i «Lagret nylig» og innstillingene ligger igjen i `%LOCALAPPDATA%\NoteScanner` til du sletter mappa selv. PDF-ene dine røres aldri.

## Velge skanner

Er flere skannere koblet til (f.eks. både Canon og Brother), velg den under *Skanning → Skanner*. *Automatisk* bruker den første som svarer. Får du en feilmelding («Skanneren er opptatt», «Fant ingen skanner» …), sjekk at skanneren er slått på og at ikke et annet skanneprogram holder den.

## Koble skannerknappen til NoteScanner

Windows bestemmer hvilket program som starter når du trykker en knapp på skanneren. Canon-programmet tar normalt alle knappene, så du må bytte for den knappen du vil bruke:

1. I NoteScanner: *Flere valg → Koble skannerknappen til NoteScanner* (sier ja til administrator-spørsmålet). Da står NoteScanner i lista over programmer skanneren kan starte.
2. Åpne **Kontrollpanel → Enheter og skrivere** (eller søk etter «Skannere og kameraer»).
3. Høyreklikk skanneren (f.eks. **CanoScan LiDE 300**) **→ Skanneregenskaper → fanen Hendelser**.
4. Velg knappen i lista (f.eks. COPY), velg **Start dette programmet: NoteScanner**, OK.

Nå: trykk COPY → NoteScanner starter og skanner siden. Trykk COPY igjen → neste side legges til i lista (appen som allerede er åpen tar imot). Mellomromstasten i appen gjør det samme.

Lar ikke knappen seg flytte (noen Canon-driverversjoner låser COPY til utskrift), er det to alternativer:
- Bruk en av de andre knappene (AUTO SCAN, PDF, SEND) til NoteScanner.
- Behold IJ Scan Utility på knappen, la den lagre bildene i en mappe, og sett samme mappe under **Overvåk** i NoteScanner. Hver ny fil i mappa blir automatisk en ny side.

## Skanneknappen på Brother DCP-L2627DW

Brother sender Scan-knappen til sitt eget program (Brother iPrint&Scan), ikke til NoteScanner. Løsningen er å la Brother lagre bildet i en mappe som NoteScanner overvåker:

1. Lag en tom mappe, f.eks. `C:\Skann\Noter`.
2. Åpne **Brother iPrint&Scan → Maskinskanneinnstillinger** (Machine Scan Settings) og velg DCP-L2627DW.
3. Under **Fil** (Scan to File): filtype **JPEG** (eller TIFF), oppløsning **600 dpi**, farge **Grå**, og lagringsmappe = mappa fra punkt 1. Slå av at mappa/bildet åpnes etter skanning hvis det står på.
4. I NoteScanner: *Flere valg → Overvåk mappe → Velg …* og velg samme mappe.
5. På skanneren: **Scan → til PC → Fil** og velg PC-en.

Brother iPrint&Scan må kjøre i bakgrunnen (den starter vanligvis med Windows). Hver gang du trykker, havner siden i lista i NoteScanner. NoteScanner leser oppløsningen fra bildefila, så den trenger ikke stemme med dpi-valget i appen. Mellomromstasten i appen virker fortsatt.

## Vinduet

- **Øverst:** *Skann side* (Mellomrom) og *Importer …* for bilder du allerede har.
- **Til venstre:** dokumentene med miniatyrer av hver side. Gul prikk betyr ikke lagret, grønn betyr lagret, og blå betyr at appen fortsatt leser. Knappene under lista deler, slår sammen, flytter og sletter.
- **I midten:** siden du har valgt, med verktøy for beskjæring, retting, rotering, forbedring og angre. Pil opp og ned (eller PgUp/PgDn) blar side for side.
- **Til høyre:** dokumentets navn, *Skanning* (resultat og oppdeling), *Lagres i*, og *Flere valg* nederst. *Lagre alle* ligger alltid synlig nederst.
- **Nederst:** status for skanner og tekstlesing, og fremdrift mens noe lagres.

## Skanning: gråtone, svart-hvitt eller farge

Velg under *Skanning → Resultat*:
- **Gråtone** (standard): beholder blyantmerknader og gråtoner. Rundt 1 MB per side i PDF-en.
- **Svart-hvitt:** ren svart/hvitt i 600 dpi i PDF-en. Skarpest utskrift og minst fil, rundt 100 kB per side. Lurt for store bøker. Skanneren skanner i gråtone, og appen gjør om til svart/hvitt etterpå, fordi det blir skarpere enn skannerens egen svart/hvitt-modus.
- **Farge:** for noter med farger. Største filer.

Bytter du resultat, endres siden du ser på med en gang. *Flere valg → Bruk resultat og svarthet på alle sider* gjør det samme for alle sidene i lista.

## Raskere skanning: begrens skanneområdet

Skannerhodet går normalt hele lengden av glasset (297 mm), også når arket er mindre. Under *Skanning → Område* kan du velge hvor stort område som skannes, målt fra hjørnemerket på skanneren:
- **A5 liggende (210 × 148 mm):** raskest, hodet går bare halve veien. Legg A5-boka på tvers inntil hjørnemerket, og velg *Snu* så sidene kommer rett vei.
- **A5 stående, B5 stående og A4:** for andre formater.
- **Egendefinert:** skriv inn bredde og lengde i mm. Det er lengden som bestemmer tiden.

Statuslinja viser hvor lang tid hver skanning tok, for eksempel «skannet på 9,8 s (A5 liggende)», så du kan sammenligne.

Mens skanneren går, venter behandlingen og tekstlesingen av forrige side til skanningen er ferdig. Skanneren sender bildet mens hodet går, og hvis PC-en er opptatt med annet, kan hodet stoppe opp og skanningen ta lengre tid.

## Store bøker (100+ sider)

Velg **Én bok** under *Skanning → Oppdeling*. Da havner alle sidene i én PDF. Titlene appen finner inne i boka (en side med tittel over første notesystem) blir **bokmerker** i PDF-en, så du kan hoppe rett til et stykke i PDF-leseren. Bokmerkene vises med blått ikon i lista.

**Navn på sangene:** I bokmodus har *Dokument*-kortet et felt **Sang her** for siden du ser på. Står det et navn der (gult = lest av appen), blir det bokmerke. Skriv inn eller rett navnet selv, eller tøm feltet hvis siden ikke starter en ny sang. Dobbeltklikk på en side i lista for å gå rett til feltet. *Boktittel* øverst er navnet på hele boka (og PDF-filen).

Appen ser på hvor stor tittelen er i forhold til avstanden mellom notelinjene, så den finner titler også i små A5-bøker. Tempo- og foredragsord («Allegro con spirito», «Trio») regnes ikke som titler.

Titler skrives i samme stil overalt: «MARCH—BEYOND THE BLUE» blir **March - Beyond the Blue**, og «SONG ARRANGEMENT—YES, JESUS LOVES ME» blir **Song Arrangement - Yes, Jesus Loves Me**. Sidenummeret i boka («No. 47») tas bort, og typiske lesefeil i sjangerordet («Arrangenient») rettes. Komponisten høyre for tittelen («Captain DEAN GOFFIN») blir ikke med i tittelen.

**To sanger på samme bokside:** Står en kort sang og starten på neste sang på samme side, deler appen siden i to – én side i lista per sang, hver med sitt bokmerke. Du trenger ikke skanne siden to ganger. Kan slås av under *Flere valg → Del sider med flere sanger*.

**Nabosiden i boka:** Kommer en stripe av forrige eller neste side med i skannet, tar auto-crop den bort. Appen ser etter bretten mellom sidene (en mørk linje eller skygge), eller noter som er kuttet av kanten av skannet med et bredt tomrom imellom.

Tips:
- Er boka A5 eller mindre, velg et mindre **Område** (se over). Det sparer mye tid over 100 sider.
- Rullehjulet ruller bare panelet. Det endrer ikke nedtrekkslister og tallfelt du tilfeldigvis står over.
- Velg **Svart-hvitt**. En bok på 120 sider blir da rundt 11 MB, mot rundt 100 MB i gråtone.
- Trykk boka godt ned mot glasset. Skyggen fra bokryggen fjernes automatisk når den ligger i margen. Ligger skyggen inne over notene, kan det bli en grå stripe igjen. Da kan du beskjære for hånd (dra med musa og trykk Enter).
- Du kan skanne videre mens boka lagres. Fremdriften står nederst i vinduet.
- Lukker du appen midt i, hentes sidene fram neste gang.
- Har tekstlesingen bommet på noen sider, bruk *Flere valg → Les tittel og stemme på alle sider på nytt*. Navn du har skrevet selv beholdes. På en enkelt side trykker du T. Inni en bok endrer det bare bokmerkene, ikke bokas tittel.
- Sider der tekstlesingen feilet, er merket med «Tekstlesing feilet» i lista.

## Slik jobber du

1. **Skann alt i ett strekk.** Legg ark på glasset og trykk knappen (eller mellomrom), om igjen og om igjen. Du trenger ikke lagre mellom hvert ark.
2. **Appen deler opp selv.** Hver side leses mens du skanner videre. En side med stor tittel over første notesystem starter et nytt dokument. Andre sider, som side 2 og 3 av samme stykke, legges til dokumentet før. Mens en side leses, står det ⏳ ved den.
3. **Sjekk lista til venstre.** Alle dokumentene ligger der med miniatyrer av hver side. Klikk deg gjennom og se at sidene ser bra ut og havner i riktig dokument. Tittel, stemme og komponist for dokumentet du har valgt står til høyre. Gule felt er forslag fra appen, og du kan rette dem.
4. **Lagre.** *Lagre* (Ctrl+S) lagrer dokumentet du har valgt. *Lagre alle* (Ctrl+Shift+S) lagrer alle dokumenter som ikke er lagret, hvert i sin egen PDF. Lagrede dokumenter får ✓ og grønn tekst. *Fjern lagrede* rydder dem bort fra lista.

**Har appen delt feil?**
- **Del her** (N): starter et nytt dokument fra siden du står på.
- **Slå sammen** (M): legger dokumentet til det forrige, f.eks. når side 2 ble lest som ny tittel.
- **▲ / ▼** (Ctrl+pil opp/ned): flytter siden. Flytter du øverste side i et dokument opp, havner den i dokumentet før.

Det du har rettet selv, endrer appen aldri.

**Sanger over flere sider** holdes samlet: en side uten stor tittel legges til sangen før. Det samme gjelder en side der tittelen gjentas – med «(forts.)» eller med samme tittel og samme stemme. Har siden samme tittel men en *annen* stemme (f.eks. Kornett 2 etter Kornett 1), blir den et nytt dokument. I «Én bok» blir det bare ett bokmerke per sang.

**Lagret nylig.** Når et dokument er lagret, flyttes det ut av lista og ned i *Lagret nylig* nederst til venstre. Der ligger det i 14 dager: dobbeltklikk (eller *Åpne PDF*) for å åpne PDF-en, *Hent tilbake* for å endre noe (lagrer du igjen, overskrives samme fil), eller *Fjern*. PDF-filene blir aldri slettet. Vil du heller beholde lagrede dokumenter i lista, slå av *Flytt lagrede dokumenter til «Lagret nylig»* under *Lagring*.

**Fold sammen stykker.** Klikk pila til høyre på en dokumentlinje for å skjule sidene, så hvert stykke bare tar én linje. Knappene under lista folder sammen eller ut alle (Ctrl+← / Ctrl+→), og F folder dokumentet du står i. Blar du inn i et sammenfoldet stykke, foldes det ut.

**Ingenting går tapt.** Lista lagres automatisk mens du jobber. Lukker du appen, eller den krasjer, spør den neste gang om du vil hente fram sidene igjen.

## Taster

| Tast | Funksjon |
|---|---|
| Mellomrom | Skann ny side |
| A | Auto-crop (finner noter og tekst, legger på marg) |
| Dra med musa | Beskjær manuelt: marker området du vil beholde – siden beskjæres når du slipper knappen |
| O | Tilbakestill beskjæring: hele skannet kommer fram igjen (rettet opp og forbedret) |
| Shift+mellomrom | Skann siden på nytt: det nye skannet erstatter siden du står på (samme plass og sangnavn) |
| D | Rett opp skjev side (bruker notelinjene) |
| L / R | Roter 90° |
| E | Forbedre: hvit bakgrunn, svart blekk |
| S | Fjern støvprikker |
| T | Les tittel/stemme/komponist på nytt fra siden som vises |
| PgUp / PgDn | Forrige / neste side |
| N / M | Del her / slå sammen med forrige |
| F | Fold sammen / ut dokumentet du står i |
| Ctrl+J | Lim siden under siden over til ett bilde (hvis appen har delt én bokside i to ved en feil) |
| Ctrl+← / Ctrl+→ | Fold sammen / ut alle dokumenter |
| Ctrl+↑ / Ctrl+↓ | Flytt side |
| Ctrl+Z | Angre (per side) |
| Delete | Slett side |
| Ctrl+S / Ctrl+Shift+S | Lagre valgt dokument / lagre alle |
| Ctrl+N | Tøm hele lista |

## Navn og oppdeling

Appen leser toppen av hvert ark (alt over første notesystem):
- **Tittel**: teksten med størst skrift, helst midtstilt. STORE BOKSTAVER blir gjort om til vanlig skrift.
- **Stemme**: linja med et instrumentnavn (kornett, althorn, Bb Cornet, Klarinett 1, Tuba, Slagverk, Partitur …), gjerne oppe til venstre.
- **Komponist**: «Musikk: …», «Music by …» eller et kort navn ute til høyre. Arrangøren («Arr. …») brukes bare hvis ingen komponist står der.

Tempo, dynamikk («mf», «solo», «rall» …), forlag og copyright-linjer regnes ikke som tittel.

**Filnavn**-menyen bestemmer rekkefølgen: «Tittel - Stemme», «Tittel - Komponist - Stemme» osv. Forhåndsvisningen står under feltene. Mangler tittel, blir navnet «Noter <dato> dokument N». Finnes filnavnet fra før, legges (2), (3) … til. Lagrer du et dokument på nytt etter å ha endret det, overskrives filen det ble lagret til forrige gang. Tittel, komponist og stemme legges også inn i PDF-egenskapene.

Slår du av «Les tittel, stemme og komponist», havner alle sidene i ett dokument, og du deler opp selv med N.

**OCR-motoren** er Windows' innebygde tekstgjenkjenning, kalt via PowerShell. Den trenger ingen ekstra pakker og virker med alle Python-versjoner. For best resultat med æ, ø og å bør norsk være lagt til som språk i Windows (*Innstillinger → Tid og språk → Språk og område → Legg til språk → Norsk bokmål*). Har du Python 3.14 eller eldre, kan `pip install -r requirements-winrt.txt` gjøre lesingen litt raskere. Har du Tesseract installert, brukes den som reserve.


**PDF-side:**
- *A4-ark, originalstørrelse*: noten havner på A4 i samme størrelse som originalen. Riktig for utskrift av A4-noter, men mindre bøker får bred hvit marg.
- *A4-ark, forstørret til hele arket*: forstørrer små bøker til A4. Fint for utskrift i større format.
- *Som beskåret (minst marg)* – standard: PDF-siden blir akkurat som i appen. Best på nettbrett og skjerm.

Bytter du PDF-side etter at du har lagret, blir dokumentene merket som ulagret. Trykk *Lagre alle* for å lagre dem på nytt – de gamle filene blir erstattet.

**Flere valg → Gjør automatisk på nye sider:** rett opp, auto-crop, forbedre, fjern støv, hopp over tomme sider og del sider med flere sanger. Alt kan skrus av, og Ctrl+Z går tilbake til originalskannet.

**Tynne streker** (notelinjer, halser, buer) blir aldri like mørke som notehodene i skannet. Forbedringen finner dem og gjør dem svarte og sammenhengende, uten at notehoder og bjelker blir tykkere.

**Svarthet (0–10)** bestemmer hvor svart blekket blir. Standard er 4, som gir samme strektykkelse som Canon-programmet. Endrer du tallet, oppdateres siden som vises med en gang.

300 dpi er et godt utgangspunkt for noter. 600 dpi er for veldig små noter.

**Skannetid:** Hver skanning skrives til `skannelogg.txt` i loggmappa (se under): total tid, oppkobling, selve skannet (hodet går), overføring og hvor lang tid hodet skal bruke etter Canons egne tall. Ifølge Canon bruker LiDE 300 1,3 ms per linje i gråtone ved 600 dpi, 2,2 ms ved 300 dpi, men 3,8 ms i farge ved 600 dpi. Farge i 600 dpi er altså rundt tre ganger så tregt som gråtone.

## Fant du en feil?

*Flere valg → Rapporter feil …* samler versjon, Windows-versjon, skanner og de siste linjene fra loggen (uten
brukernavnet ditt og personlige mappenavn) og åpner en ferdig utfylt sak på
[GitHub](https://github.com/elha25/NoteScanner/issues). Du må ha en (gratis) GitHub-konto. Uten konto kan du velge
*Lagre feilrapport-fil* og sende fila på e-post i stedet.

Sakene på GitHub er offentlige: ikke legg ved bilder av noter du ikke har rett til å dele.

**Loggene** ligger i `%LOCALAPPDATA%\NoteScanner\logg` (lim inn i adressefeltet i Utforsker):
- `feillogg.txt` – uventede feil, og tekstlesing som feilet
- `skannelogg.txt` – tider og innstillinger for hver skanning
- `krasj.txt` – hvis appen krasjet helt
- `selvtest.txt` – resultatet av `NoteScanner.exe --selftest`

Der ligger også økta (`session`) og «Lagret nylig» (`lagret`).
