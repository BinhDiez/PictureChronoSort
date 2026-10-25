
# PictureChronoSort

**Foto· und Video-Organizer für macOS — mit Zeitstempel-, Text· und Metadaten-Korrektur**

PictureChronoSort sortiert deine Foto· und Videosammlung automatisch nach dem Aufnahmedatum in eine übersichtliche Jahr/Monat-Struktur. Darüber hinaus kannst du Text oder Ort nach dem Zeitstempel ergänzen, das Aufnahmedatum korrigieren und vom Programm gesetzte Metadaten gezielt bereinigen.

---

---

## Funktionsumfang

· **Automatische Sortierung** nach Aufnahmedatum in `Jahr/Jahr-Monat/JJJJ-MM-TT_HHMMSS.ext`
· **Duplikat-Erkennung** über SHA-256-Hash mit flexibler Behandlungsstrategie
· **Text/Ort nach Zeitstempel** setzen — z. B. `2013-01-23_150514 Berlin.jpg`
· **Aufnahmedatum korrigieren** — Jahr, Monat, Tag, Stunde, Minute, Sekunde einzeln oder kombiniert; relative Zeitverschiebung für Zeitzonen-Fehler
· **Metadaten bereinigen** — vom Programm gesetzte Felder (`XPTitle`, `ImageDescription`) gezielt löschen
· **Live-Photo-Unterstützung** — HEIC+MOV werden als Paar behandelt
· **AAE-Bearbeitungsdateien** aus Apple Fotos werden mit archiviert
· **GPS-Vorschlag** — ermittelt über Nominatim den Ortsnamen aus den GPS-Daten
· **Dry-Run (Testlauf)** — zeigt alle Aktionen, ohne eine Datei zu verändern
· **Undo-Funktion** — jeder echte Lauf ist rückgängig machbar
· **Ausführliche Logs** — Run-Log, Fehler-Log, Undo-Log und Crash-Log

---

<details>
<summary>⚙️ Installation </summary>



### Voraussetzungen

· macOS (getestet auf Intel und Apple Silicon)
· Python 3.9 oder höher
· exiftool (`brew install exiftool`)
· Optional: send2trash für Papierkorb-Unterstützung (`pip install send2trash`)

### Aus dem Quellcode starten

```bash
git clone https://github.com/BinhDiez/PictureChronoSort.git
cd PictureChronoSort
pip install PyQt5 send2trash
python PictureChronoSort_06.py
```

Fertige App

Lade die aktuelle .app aus den Releases herunter. Die App ist für macOS gebaut und enthält exiftool im Bundle — keine zusätzliche Installation nötig.

</details>

---

## Bedienung

1. Quellordner wählen — dein Import-Ordner (z. B. _Import)
2. Zielordner wählen — dein Archiv (z. B. _Ausgabe)
3. Modus wählen — siehe Die Modi im Detail
4. Dry-Run AN lassen — für den ersten Testlauf
5. Verarbeitung starten — Log prüfen
6. Dry-Run ausschalten — echter Lauf starten


<details>
<summary>⚙️ Tastaturbedienung</summary>


· Tab — wechselt zwischen Eingabefeldern und Buttons
· Shift+Tab — rückwärts
· Pfeiltasten — wechseln zwischen Buttons (OK ↔ Abbrechen)
· Enter — löst den fokussierten Button aus
· Esc — schließt den Dialog ab (Abbrechen)

Der aktuell fokussierte Button wird durch einen weißen Rahmen hervorgehoben.


</details>

---

## Die Modi im Detail


---

<details>
<summary>⚙️ organize — Organisieren (Standard)</summary>


Verschiebt Dateien in die Struktur Jahr/Jahr-Monat/JJJJ-MM-TT_HHMMSS.ext und setzt Erstell· und Änderungsdatum auf das Aufnahmedatum. Der ursprüngliche Dateiname wird in XPTitle gespeichert, damit er später wiederhergestellt oder angezeigt werden kann.

</details>

---

<details>
<summary>⚙️ dedupe_only — Nur Duplikate behandeln</summary>


Sucht Dateien mit identischem Inhalt (SHA-256-Hash) und behandelt sie je nach gewählter Duplikat-Aktion:

· skip — Duplikat bleibt liegen
· move — verschiebt in einen sichtbaren _Duplikate-Ordner (empfohlen)
· trash — in den macOS-Papierkorb (nur auf internen Laufwerken zuverlässig)
· delete — sofort löschen (nicht umkehrbar)

</details>

---

<details>
<summary>⚙️ timestamps_only — Nur Zeitstempel erneuern</summary>


Lässt Dateien am Ort und setzt nur die Finder-Zeitstempel aus EXIF-Daten. Nützlich nach einem NAS-Sync, weil der Erstell-Zeitstempel dabei oft verloren geht.

</details>

---

<details>
<summary>⚙️ fix_capture_date — Aufnahmedatum korrigieren</summary>


Korrigiert das EXIF-Feld DateTimeOriginal. Du kannst:

· Jahr, Monat, Tag in einer Zeile setzen
· Stunde, Minute, Sekunde in einer zweiten Zeile setzen
· Relative Zeitverschiebung angeben (z. B. −2 Stunden bei Zeitzonen-Fehlern)
· Dateinamen an den neuen Zeitstempel anpassen (optional)
· Finder-Zeitstempel mit anpassen (optional)

</details>

---

<details>
<summary>⚙️ add_text — Text/Ort nach Zeitstempel setzen</summary>


Fügt nach dem Zeitstempel einen Text oder Ort ein — z. B. 2013-01-23_150514.jpg → 2013-01-23_150514 Berlin.jpg.

Optionen:

· Vorher gesetzten Text behalten — statt zu ersetzen
· Sinnvollen Originalnamen anzeigen — z. B. Geburtstag.jpg, aber nicht IMG_1234
· GPS-Vorschlag — ermittelt über Nominatim den Ortsnamen aus den GPS-Daten

</details>

---

<details>
<summary>⚙️ clean_metadata — Vom Programm gesetzte Metadaten bereinigen</summary>


Entfernt gezielt Felder, die das Programm zuvor gesetzt hat:

· XPTitle — der ursprüngliche Dateiname
· ImageDescription — der gesetzte Text/Ort

Die Datumsfelder (DateTimeOriginal, CreateDate, ModifyDate) und die Finder-Zeitstempel bleiben unberührt.

</details>

---

<details>
<summary>⚙️ Kombinierter Lauf: „Erst organisieren, dann Text setzen"</summary>


Ein Klick startet beide Schritte hintereinander: zuerst organize (Quelle → Ziel), dann add_text im Zielordner. Ideal für den ersten Import, wenn du schon weißt, wo die Fotos aufgenommen wurden.

</details>

---

<details>
<summary>⚙️ Metadaten-Schema</summary>


PictureChronoSort verwendet zwei Metadaten-Felder, um den Zustand einer Datei nachvollziehbar zu halten:

Feld Inhalt Wann geschrieben
XPTitle Ursprünglicher Dateiname Einmalig beim ersten organize-Lauf
ImageDescription Aktueller Text/Ort Bei jedem add_text-Lauf

Der Originalname wird nur dann im Dateinamen angezeigt, wenn er „sinnvoll" ist — also nicht IMG_1234, DSC_5678 oder ähnliche Kamera-Automatiknamen. Andernfalls bleibt er in den Metadaten gespeichert, wird aber nicht angezeigt.


</details>

---


## Sicherheitsnetze


<details>
<summary>⚙️ Dry-Run (Testlauf)</summary>


Jeder Modus unterstützt einen Testlauf. Im Log siehst du genau, welche Datei wohin verschoben oder wie sie umbenannt würde — ohne dass eine Datei tatsächlich verändert wird.


</details>

---

<details>
<summary>⚙️ Undo Funktion</summary>


Jeder echte Lauf schreibt eine undo_*.jsonl-Datei mit allen Aktionen. Über den Button „Undo…" im Hauptfenster kannst du einen Lauf komplett rückgängig machen. Aktionen, die rückgängig gemacht werden können:

· move — Verschiebungen
· dup_move — Duplikat-Verschiebungen
· rename_date — Umbenennungen
· add_text — Umbenennungen
· fix_date — Datum-Korrekturen
· restore_meta — Metadaten-Bereinigungen

Nicht rückgängig machbar sind trash und delete.


</details>

---

<details>
<summary>⚙️ Sicherheitskopie vor Datum-Korrekturn</summary>


Vor einer Datum-Korrektur kannst du eine Sicherheitskopie aller aktuellen EXIF-Datumswerte anlegen lassen. Die Datei landet als exif_backup_*.txt im logs/-Ordner.


</details>

---

<details>
<summary>⚙️ Bestätigung bei echtem Lauf</summary>


Vor jedem echten Lauf in einem destruktiven Modus erscheint eine Bestätigung mit der Anzahl betroffener Dateien. So kannst du nicht versehentlich einen Testlauf mit einem echten Lauf verwechseln.


</details>

---

<details>
<summary>⚙️ Logdateien</summary>


Alle Logs liegen im Ordner logs/:

Datei Inhalt
run_*.txt Vollständiges Protokoll eines Laufs
failed_*.txt Alle problematischen Dateien mit Details
undo_*.jsonl Alle Aktionen des Laufs (für Undo)
crash.log Nicht abgefangene Exceptions (Abstürze)
exif_backup_*.txt Sicherheitskopie der EXIF-Datumswerte

Log-Aufbewahrung: Beim Programmstart werden automatisch alte Logs gelöscht. Die letzten 10 Dateien bleiben erhalten, zusätzlich wird alles gelöscht, was älter als 7 Tage ist.


</details>

---

<details>
<summary>⚙️ Tastaturbedienung</summary>


· Tab — wechselt zwischen Eingabefeldern und Buttons
· Shift+Tab — rückwärts
· Pfeiltasten — wechseln zwischen Buttons (OK ↔ Abbrechen)
· Enter — löst den fokussierten Button aus
· Esc — schließt den Dialog ab (Abbrechen)

Der aktuell fokussierte Button wird durch einen weißen Rahmen hervorgehoben.


</details>

---

<details>
<summary>⚙️ Systemanforderungen</summary>

Systemanforderungen

· macOS 10.15 (Catalina) oder höher
· exiftool (im App-Bundle enthalten; für den Quellcode-Start separat installieren)
· Python 3.9+ (nur für den Quellcode-Start)


</details>

---


<details>
<summary>📄 Lizenz</summary>

Dieses Projekt ist unter der **MIT-Lizenz** lizenziert.


MIT License

Copyright (c) 2026 BinhDiez

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

1. PyQt5
This application uses PyQt5, which is licensed under the GNU General Public License v3 (GPLv3).
Copyright (c) Riverbank Computing Limited.
Full license text: https://www.gnu.org/licenses/gpl-3.0.html

2. ExifTool
   ExifTool ist unter den Bedingungen der Perl Artistic License oder
   GPL veröffentlicht. Die Weitergabe im Bundle ist gestattet, solange
   der Copyright-Hinweis und die Lizenz beibehalten werden.

3. send2trash
   Steht unter der BSD-3-Clause-Lizenz. Der Copyright-Hinweis und der
   Haftungsausschluss müssen in der Dokumentation beibehalten werden.

4. Nominatim / OpenStreetMap
   Die per Reverse-Geocoding ermittelten Ortsdaten stammen aus
   OpenStreetMap und stehen unter der ODbL. Bei Anzeige oder
   Speicherung der Daten ist eine Quellenangabe
   ("© OpenStreetMap-Mitwirkende") erforderlich.

5. Python
   Python selbst steht unter der PSF-Lizenz. Die Lizenztexte sind
   im App-Bundle enthalten und müssen beibehalten werden.


</details>

---

<details>
<summary>🔄 Versionsverlauf / Change Log / Lịch sử phiên bản</summary>

## Erste Veröffentlichung. Alle Funktionen sind neu.


## PictureChronoSort v6.0

**Erste stabile Version mit vollständigem Funktionsumfang.**

### Was ist neu

· **Sechs Modi** für unterschiedliche Aufgaben:
  · `organize` — Organisieren nach Aufnahmedatum
  · `dedupe_only` — Duplikate erkennen und behandeln
  · `timestamps_only` — Zeitstempel erneuern (für NAS)
  · `fix_capture_date` — Aufnahmedatum korrigieren
  · `add_text` — Text/Ort nach Zeitstempel setzen
  · `clean_metadata` — Vom Programm gesetzte Metadaten bereinigen

· **Kombinierter Lauf** — „Erst organisieren, dann Text setzen" mit einem Klick

· **GPS-Vorschlag** — ermittelt über Nominatim den Ortsnamen aus den GPS-Daten (asynchron, ohne die Oberfläche zu blockieren)

· **Metadaten-Schema** — `XPTitle` (Originalname) und `ImageDescription` (Text/Ort) werden gezielt gesetzt und können bereinigt werden

· **Undo-Funktion** — jeder echte Lauf ist rückgängig machbar, außer `trash` und `delete`

· **Dry-Run** — jeder Modus unterstützt einen Testlauf

· **Ausführliche Logs** — Run-Log, Fehler-Log, Undo-Log und Crash-Log, automatisch aufgeräumt (10 Dateien / 7 Tage)

· **Barrierefreie Bedienung** — weißer Fokusrahmen auf allen Buttons, Tab· und Pfeiltasten-Navigation, Enter löst den fokussierten Button aus

· **Deutsche Benutzerführung** — alle Dialoge mit deutschen Buttons und verständlichen Texten

· **Info-Dialog** mit Versionsanzeige und GitHub-Update-Prüfung

</details>

---

## 👤 Autor

**BinhDiez**

· GitHub: [@BinhDiez](https://github.com/BinhDiez)
· Projekt: [PictureChronoSort](https://github.com/BinhDiez/PictureChronoSort)

---

## 🙏 Danksagungen

Diese App wäre ohne die folgenden Open-Source-Projekte nicht möglich:

· **Qt / PyQt5** — GUI-Framework
· **exiftool** — Metadaten-Extraktion und -Manipulation
· **send2trash** — Papierkorb-Unterstützung
· **Nominatim / OpenStreetMap** — Reverse-Geocoding für GPS-Vorschläge

---

<details>
<summary>🤝 Beitragen</summary>

Beiträge sind willkommen! Bitte:

1. **Fork** das Repository
2. **Branch** erstellen (`git checkout -b feature/neues-feature`)
3. **Commit** (`git commit -am 'Füge neues Feature hinzu'`)
4. **Push** (`git push origin feature/neues-feature`)
5. **Pull Request** öffnen

### Bugs & Wünsche

Bitte als [Issue](https://github.com/BinhDiez/GitPulseX/issues) melden.

---

## ⭐ Unterstützung

Wenn PictureChronoSort dir hilft, freue ich mich über einen **Star** auf GitHub!

---

**Viel Erfolg beim Sortieren deiner Bilder! 🚀**
</details>

---

<details>
<summary>🔒 macOS Gatekeeper Info</summary>


PictureChronoSort is currently not signed with an Apple Developer certificate.

When starting the app for the first time, macOS Gatekeeper may block the app from running.

1. Open the app once.
2. Close the warning.
3. **System Settings → Privacy & Security**
4. Scroll all the way down to the warning "PictureChronoSort was blocked..." 
5. Select **“Open Anyway”**
6. If you are warned again: select **“Open Anyway”** again and confirm with your password.

### Alternatively, remove the quarantine attribute:

🍎 macOS Terminal

xattr -d com.apple.quarantine '/Users/username/Downloads/PictureChronoSort.app'

> Please adjust the file path accordingly.

</details>

---

<details>
<summary>🖥️ Download-Info</summary>


### Download-Versione

| Suffix | Betriebssystem |
|--------|-----------------|
| `_macOS_arm64` | 🍎 Apple Silicon (M1–M4) |
| `_macOS_x86_64` | 🍎 Intel Macs |


### Extract 7z-Archive

| Betriebssystem | Empfohlene App |
|---------------|----------------|
| 🍎 macOS | **Keka** – <https://www.keka.io/> |
| 🪟 Windows | **7-Zip** – <https://www.7-zip.org/> |

</details>

---

<details>
<summary>🔑 7z Password</summary>

**BinhDiez**

</details>
