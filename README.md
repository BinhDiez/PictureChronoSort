# PictureChronoSort

## Foto- und Video-Organizer für macOS – mit Zeitstempel-, Text- und Metadaten-Korrektur

PictureChronoSort sortiert deine Foto- und Videosammlung automatisch nach dem Aufnahmedatum in eine übersichtliche Jahr/Monat-Struktur. Darüber hinaus kannst du Text oder Ort nach dem Zeitstempel ergänzen, das Aufnahmedatum korrigieren und vom Programm gesetzte Metadaten gezielt bereinigen.

---

## ✨ Funktionsumfang

* Automatische Sortierung nach Aufnahmedatum in Jahr/Jahr-Monat/JJJJ-MM-TT_HHMMSS.ext
* Duplikat-Erkennung über SHA-256-Hash mit flexibler Behandlungsstrategie
* Text/Ort nach Zeitstempel setzen – z. B. 2013-01-23_150514 Berlin.jpg
* Aufnahmedatum korrigieren – Jahr, Monat, Tag, Stunde, Minute und Sekunde einzeln oder kombiniert; relative Zeitverschiebung für Zeitzonenfehler
* Metadaten bereinigen – vom Programm gesetzte Felder (XPTitle, ImageDescription) gezielt löschen
* Live-Photo-Unterstützung – HEIC- und MOV-Dateien werden als Paar behandelt
* AAE-Bearbeitungsdateien aus Apple Fotos werden mit archiviert
* GPS-Vorschlag – ermittelt über Nominatim den Ortsnamen aus den GPS-Daten
* Dry-Run (Testlauf) – zeigt alle Aktionen, ohne eine Datei zu verändern
* Undo-Funktion – jeder echte Lauf ist rückgängig machbar
* Ausführliche Logs – Run-Log, Fehler-Log, Undo-Log und Crash-Log

---

<details>
<summary>📦 Installation</summary>

Voraussetzungen

* macOS (getestet auf Intel und Apple Silicon)
* Python 3.9 oder höher
* ExifTool (brew install exiftool)
* Optional: send2trash für Papierkorb-Unterstützung (pip install send2trash)

Aus dem Quellcode starten

git clone https://github.com/BinhDiez/PictureChronoSort.git
cd PictureChronoSort
pip install PyQt5 send2trash
python PictureChronoSort_06.py

Fertige App

Lade die aktuelle .app aus den Releases herunter. Die App ist für macOS gebaut und enthält ExifTool im Bundle – keine zusätzliche Installation ist erforderlich.

</details>

---

## 🚀 Bedienung

1. Quellordner wählen – dein Import-Ordner (z. B. _Import)
2. Zielordner wählen – dein Archiv (z. B. _Ausgabe)
3. Modus wählen – siehe Die Modi im Detail⁠￼
4. Dry-Run eingeschaltet lassen – für den ersten Testlauf
5. Verarbeitung starten – Log prüfen
6. Dry-Run ausschalten – echten Lauf starten


<details>
<summary>⌨️ Tastaturbedienung</summary>

* Tab – wechselt zwischen Eingabefeldern und Buttons
* Shift + Tab – rückwärts navigieren
* Pfeiltasten – wechseln zwischen Buttons (OK ↔ Abbrechen)
* Enter – löst den fokussierten Button aus
* Esc – schließt den Dialog ab (Abbrechen)

Der aktuell fokussierte Button wird durch einen weißen Rahmen hervorgehoben.

</details>

---

## 🛠️ Die Modi im Detail

<details>
<summary>📁 organize – Organisieren (Standard)</summary>

Verschiebt Dateien in die Struktur Jahr/Jahr-Monat/JJJJ-MM-TT_HHMMSS.ext und setzt Erstellungs- und Änderungsdatum auf das Aufnahmedatum.

Der ursprüngliche Dateiname wird in XPTitle gespeichert, damit er später wiederhergestellt oder angezeigt werden kann.

</details>

---

<details>
<summary>🔍 dedupe_only – Nur Duplikate behandeln</summary>

Sucht Dateien mit identischem Inhalt (SHA-256-Hash) und behandelt sie je nach gewählter Duplikat-Aktion:

* skip – Duplikat bleibt liegen
* move – verschiebt die Datei in einen sichtbaren _Duplikate-Ordner (empfohlen)
* trash – verschiebt die Datei in den macOS-Papierkorb (nur auf internen Laufwerken zuverlässig)
* delete – löscht die Datei sofort (nicht umkehrbar)

</details>

---

<details>
<summary>🕒 timestamps_only – Nur Zeitstempel erneuern</summary>

Lässt Dateien am ursprünglichen Speicherort und setzt nur die Finder-Zeitstempel anhand der EXIF-Daten.

Nützlich nach einem NAS-Sync, weil der Erstellungszeitstempel dabei oft verloren geht.

</details>

---

<details>
<summary>📅 fix_capture_date – Aufnahmedatum korrigieren</summary>

Korrigiert das EXIF-Feld DateTimeOriginal.

Du kannst:

* Jahr, Monat und Tag in einer Zeile setzen
* Stunde, Minute und Sekunde in einer zweiten Zeile setzen
* Eine relative Zeitverschiebung angeben (z. B. −2 Stunden bei Zeitzonenfehlern)
* Dateinamen an den neuen Zeitstempel anpassen (optional)
* Finder-Zeitstempel mit anpassen (optional)

</details>

---

<details>
<summary>📍 add_text – Text/Ort nach Zeitstempel setzen</summary>

Fügt nach dem Zeitstempel einen Text oder Ort ein.

Beispiel:

Vorher: 2013-01-23_150514.jpg
Nachher: 2013-01-23_150514 Berlin.jpg

Optionen

* Vorher gesetzten Text behalten – statt ihn zu ersetzen
* Sinnvollen Originalnamen anzeigen – z. B. Geburtstag.jpg, aber nicht IMG_1234
* GPS-Vorschlag – ermittelt über Nominatim den Ortsnamen aus den GPS-Daten

</details>

---

<details>
<summary>🧹 clean_metadata – Vom Programm gesetzte Metadaten bereinigen</summary>

Entfernt gezielt Felder, die das Programm zuvor gesetzt hat:

* XPTitle – der ursprüngliche Dateiname
* ImageDescription – der gesetzte Text oder Ort

Die Datumsfelder (DateTimeOriginal, CreateDate, ModifyDate) und die Finder-Zeitstempel bleiben unberührt.

</details>

---

<details>
<summary>🔄 Kombinierter Lauf: „Erst organisieren, dann Text setzen“</summary>

Ein Klick startet beide Schritte hintereinander:

1. organize – verarbeitet die Dateien von der Quelle zum Ziel.
2. add_text – ergänzt den Text im Zielordner.

Ideal für den ersten Import, wenn du bereits weißt, wo die Fotos aufgenommen wurden.

</details>

---

<details>
<summary>🏷️ Metadaten-Schema</summary>

PictureChronoSort verwendet zwei Metadatenfelder, um den Zustand einer Datei nachvollziehbar zu halten:

Feld	Inhalt	Wann geschrieben
XPTitle	Ursprünglicher Dateiname	Einmalig beim ersten organize-Lauf
ImageDescription	Aktueller Text/Ort	Bei jedem add_text-Lauf

Der Originalname wird nur dann im Dateinamen angezeigt, wenn er sinnvoll ist – also nicht IMG_1234, DSC_5678 oder ähnliche Kamera-Automatiknamen.

Andernfalls bleibt er in den Metadaten gespeichert, wird aber nicht angezeigt.

</details>

---

## 🛡️ Sicherheitsnetze

<details>
<summary>🧪 Dry-Run (Testlauf)</summary>

Jeder Modus unterstützt einen Testlauf.

Im Log siehst du genau, welche Datei wohin verschoben oder wie sie umbenannt würde – ohne dass eine Datei tatsächlich verändert wird.

</details>

---

<details>
<summary>↩️ Undo-Funktion</summary>

Jeder echte Lauf schreibt eine undo_*.jsonl-Datei mit allen Aktionen.

Über den Button „Undo…“ im Hauptfenster kannst du einen Lauf komplett rückgängig machen.

Rückgängig machbare Aktionen

* move – Verschiebungen
* dup_move – Duplikat-Verschiebungen
* rename_date – Umbenennungen
* add_text – Umbenennungen durch Text-/Ort-Ergänzungen
* fix_date – Datumskorrekturen
* restore_meta – Metadaten-Bereinigungen

Nicht rückgängig machbar: trash und delete.

</details>

---

<details>
<summary>💾 Sicherheitskopie vor Datumskorrekturen</summary>

Vor einer Datumskorrektur kannst du eine Sicherheitskopie aller aktuellen EXIF-Datumswerte anlegen lassen.

Die Datei wird als exif_backup_*.txt im Ordner logs/ gespeichert.

</details>

---

<details>
<summary>⚠️ Bestätigung bei echtem Lauf</summary>

Vor jedem echten Lauf in einem destruktiven Modus erscheint eine Bestätigung mit der Anzahl betroffener Dateien.

So kannst du nicht versehentlich einen Testlauf mit einem echten Lauf verwechseln.

</details>

---

<details>
<summary>📋 Logdateien</summary>

Alle Logs liegen im Ordner logs/.

Datei	Inhalt
run_*.txt	Vollständiges Protokoll eines Laufs
failed_*.txt	Alle problematischen Dateien mit Details
undo_*.jsonl	Alle Aktionen des Laufs (für Undo)
crash.log	Nicht abgefangene Exceptions (Abstürze)
exif_backup_*.txt	Sicherheitskopie der EXIF-Datumswerte

Log-Aufbewahrung

Beim Programmstart werden automatisch alte Logs gelöscht.

* Die letzten 10 Dateien bleiben erhalten.
* Zusätzlich werden Dateien gelöscht, die älter als 7 Tage sind.

</details>

---

<details>
<summary>💻 Systemanforderungen</summary>

* macOS 10.15 (Catalina) oder höher
* ExifTool (im App-Bundle enthalten; für den Start aus dem Quellcode separat installieren)
* Python 3.9 oder höher (nur für den Start aus dem Quellcode)

</details>

---

<details>
<summary>📄 Lizenz und Drittanbieter</summary>

Lizenz von PictureChronoSort


MIT License

Copyright (c) 2026 BinhDiez

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

Hinweis zu Drittanbieter-Komponenten:

Dieses Programm nutzt verschiedene Open-Source-Bibliotheken und -Werkzeuge,
die unter eigenen Lizenzen stehen. Die vollständigen Lizenztexte und
Copyright-Hinweise der Drittanbieter sind in der Datei
THIRD_PARTY_LICENSES.txt enthalten, die mit diesem Programm ausgeliefert wird.

Die MIT-Lizenz gilt ausschließlich für den eigenen Quellcode dieses Projekts.
Für die Drittanbieter-Komponenten gelten die Bedingungen der jeweils
genannten Lizenzen.

---

2. THIRD_PARTY_LICENSES

PictureChronoSort

Dieses Programm nutzt die folgenden Open-Source-Komponenten. Die jeweiligen
Copyright-Hinweise und Lizenzbedingungen gelten zusätzlich zur MIT-Lizenz
dieses Projekts.

1. PySide6 / Qt

PySide6 wird von der Qt Company unter der GNU Lesser General Public License
(LGPL) Version 3 vertrieben. Qt selbst steht unter LGPLv3 bzw. GPLv3,
je nach Komponente.

Copyright (C) 2016-2026 The Qt Company Ltd.
Copyright (C) 2015-2026 Qt Contributors.

Lizenztext (LGPL v3):
https://www.gnu.org/licenses/lgpl-3.0.html

Weitere Informationen:
https://www.qt.io/qt-licensing

Hinweis: Der Quellcode von PySide6 und Qt ist öffentlich verfügbar.
Bei einer Weitergabe der App als Binärpaket sind die LGPL-Pflichten
(Quellcode-Verfügbarkeit, Änderbarkeit der Bibliothek) zu beachten.

2. ExifTool

ExifTool wird von Phil Harvey unter der Perl Artistic License bzw. der
GNU General Public License (GPL) vertrieben. Dieses Programm nutzt die
Möglichkeit, ExifTool im Rahmen der Artistic License zu verwenden.

Copyright (C) 2003-2026 Phil Harvey.

Lizenztext (Artistic License 2.0):
https://opensource.org/licenses/Artistic-2.0

Weitere Informationen:
https://exiftool.org/

3. send2trash

send2trash wird unter der BSD-3-Clause-Lizenz vertrieben.

Copyright (C) 2013-2026 Send2Trash-Autoren (Andrew Senetar, Virgil Dupras
und weitere Mitwirkende).

Lizenztext (BSD-3-Clause):
https://opensource.org/licenses/BSD-3-Clause

Weitere Informationen:
https://github.com/arsenetar/send2trash

4. Nominatim / OpenStreetMap

Die GPS-Vorschlagsfunktion nutzt den Nominatim-Dienst, der auf Daten von
OpenStreetMap basiert. Die Daten stehen unter der Open Database License
(ODbL).

Copyright (C) 2004-2026 OpenStreetMap-Mitwirkende.

Lizenztext (ODbL):
https://opendatacommons.org/licenses/odbl/

Nutzungsbedingungen von Nominatim:
https://operations.osmfoundation.org/policies/nominatim/

Copyright- und Lizenzhinweis für OpenStreetMap-Daten:
https://www.openstreetmap.org/copyright

Hinweis: Bei der Anzeige oder Speicherung von Ortsdaten, die aus
OpenStreetMap stammen, ist die Quellenangabe
"© OpenStreetMap-Mitwirkende" erforderlich.

5. Python

Python wird unter der Python Software Foundation License (PSF) vertrieben.

Copyright (C) 2001-2026 Python Software Foundation.

Lizenztext (PSF License):
https://docs.python.org/3/license.html

6. Sonstige Abhängigkeiten

Darüber hinaus können weitere transitive Abhängigkeiten (z. B. shiboken6,
Qt-Plugins) mit ausgeliefert werden. Deren Lizenztexte sind in den
jeweiligen Paketen enthalten und gelten zusätzlich.

Ende der Drittanbieter-Lizenzhinweise

</details>

---

<details>
<summary>📜 Versionsverlauf / Change Log</summary>

Erste Veröffentlichung

Alle Funktionen sind neu.

PictureChronoSort v6.0

Erste stabile Version mit vollständigem Funktionsumfang.

Was ist neu?

* Sechs Modi für unterschiedliche Aufgaben:
    * organize – Organisieren nach Aufnahmedatum
    * dedupe_only – Duplikate erkennen und behandeln
    * timestamps_only – Zeitstempel erneuern (für NAS)
    * fix_capture_date – Aufnahmedatum korrigieren
    * add_text – Text/Ort nach Zeitstempel setzen
    * clean_metadata – Vom Programm gesetzte Metadaten bereinigen
* Kombinierter Lauf – „Erst organisieren, dann Text setzen“ mit einem Klick
* GPS-Vorschlag – ermittelt über Nominatim den Ortsnamen aus den GPS-Daten (asynchron, ohne die Oberfläche zu blockieren)
* Metadaten-Schema – XPTitle (Originalname) und ImageDescription (Text/Ort) werden gezielt gesetzt und können bereinigt werden
* Undo-Funktion – jeder echte Lauf ist rückgängig machbar, außer trash und delete
* Dry-Run – jeder Modus unterstützt einen Testlauf
* Ausführliche Logs – Run-Log, Fehler-Log, Undo-Log und Crash-Log, automatisch aufgeräumt (10 Dateien / 7 Tage)
* Barrierefreie Bedienung – weißer Fokusrahmen auf allen Buttons, Tab- und Pfeiltasten-Navigation; Enter löst den fokussierten Button aus
* Deutsche Benutzerführung – alle Dialoge mit deutschen Buttons und verständlichen Texten
* Info-Dialog mit Versionsanzeige und GitHub-Update-Prüfung

</details>

---

👤 Autor

BinhDiez

* GitHub: @BinhDiez⁠￼
* Projekt: PictureChronoSort⁠￼

---

🙏 Danksagungen

Diese App wäre ohne die folgenden Open-Source-Projekte nicht möglich:

* Qt / PyQt5 – GUI-Framework
* ExifTool – Metadaten-Extraktion und -Manipulation
* send2trash – Papierkorb-Unterstützung
* Nominatim / OpenStreetMap – Reverse-Geocoding für GPS-Vorschläge

---

<details>
<summary>🤝 Beitragen</summary>

Beiträge sind willkommen!

1. Fork erstellen
2. Branch erstellen (git checkout -b feature/neues-feature)
3. Commit erstellen (git commit -am 'Füge neues Feature hinzu')
4. Push ausführen (git push origin feature/neues-feature)
5. Pull Request öffnen

Bugs und Wünsche

Bitte als Issue⁠￼ melden.

</details>

---

⭐ Unterstützung

Wenn PictureChronoSort dir hilft, freue ich mich über einen Star auf GitHub!

Viel Erfolg beim Sortieren deiner Bilder!

---

<details>
<summary>🔒 macOS Gatekeeper – Informationen</summary>

PictureChronoSort ist derzeit nicht mit einem Apple-Developer-Zertifikat signiert.

Beim ersten Start kann macOS Gatekeeper die Ausführung der App blockieren.

App manuell freigeben

1. Öffne die App einmal.
2. Schließe die Warnmeldung.
3. Öffne Systemeinstellungen → Datenschutz & Sicherheit.
4. Scrolle ganz nach unten zur Warnung „PictureChronoSort wurde blockiert …“.
5. Wähle „Dennoch öffnen“ (Open Anyway).
6. Falls erneut eine Warnung erscheint, wähle noch einmal „Dennoch öffnen“ und bestätige gegebenenfalls mit deinem Passwort.

Alternative: Quarantäne-Attribut entfernen

Öffne das macOS-Terminal und führe folgenden Befehl aus:

xattr -d com.apple.quarantine '/Users/username/Downloads/PictureChronoSort.app'

Passe den Dateipfad entsprechend deinem Speicherort an.

</details>

---

<details>
<summary>🖥️ Download-Informationen</summary>

Download-Versionen

Suffix	Betriebssystem
_macOS_arm64	Apple Silicon (M1–M4)
_macOS_x86_64	Intel-Macs

7z-Archive entpacken

Betriebssystem	Empfohlene App
macOS	Keka – https://www.keka.io/
Windows	7-Zip – https://www.7-zip.org/

</details>

---

<details>
<summary>🔑 7z-Passwort</summary>
BinhDiez
</details>
