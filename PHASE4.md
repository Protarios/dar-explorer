# Phase 4 – LTFS-Grundintegration

**Aktueller Stand:** unabhängiges LTFS-PTY, Mount/Unmount, Bandstatus, Auswurf,
Gerätekonfiguration, verstellbare Terminalhöhe und manuell bestätigte
LTFS-Formatierung (siehe Ausbaustufe 4). Hardwaretests stehen aus.

Erste Umsetzung (2026-10-08):

- DAR-Terminal bleibt unverändert; neues LTFS-Terminal mit separatem VTE/PTY und eigener Shell.
- Reiterwechsel unterbricht keine laufende Shell/Operation.
- LTFS-Gerät und Mountpoint einstellbar; Mount/Unmount nur über LTFS-Shell.
- Mountstatus aus `/proc/self/mountinfo`, ohne Tape-Zugriff.
- Freigabe nach Unmount wird ausdrücklich bestätigt; kein Force-/Lazy-Unmount oder automatisches Eject.
- DAR-Erstellung, Slicing und bestehende DAR-Operationen bleiben unverändert.

Noch **nicht** integriert: automatische Bandwechselerkennung, LTFS-Eject-/Laufwerkssteuerung,
LTFS-Formatierung, automatische Geräteauswahl, Hardwareintegration und realer Bandlaufwerkstest.

## Manuelle Bedienung – Ausbaustufe 2 (2026-10-08)

- LTFS-Laufwerke im zweiten Terminal per `ltfs -o device_list` anzeigen.
- Getrennte Eingaben für LTFS-Gerät (`/dev/sgX` oder Seriennummer) und
  `mt`-Gerät (`/dev/nstX` oder `/dev/tape/by-id/...`). Die Zuordnung ist
  durch den Benutzer zu überprüfen.
- Mountpoint per Schaltfläche anlegen (ohne `sudo`, vorhandene Dateisysteme
  werden nicht verändert).
- Laufwerksstatus über `mt -f DEVICE status` prüfen, solange LTFS nicht aktiv ist.
- Band optional manuell über `mt -f DEVICE offline` auswerfen, erst nach
  bestätigtem vollständigem LTFS-Unmount und zusätzlichem Sicherheitsdialog.
- LTFS-Einstellungen in `~/.config/dar-explorer/config.json` speichern;
  bestehende DAR-Einstellungen bleiben erhalten.
- Laufwerks-/Mountpoint-Felder während Mount/Unmount sperren;
  Schließen des Anwendungsfensters verhindern, solange LTFS aktiv sein kann.
- Keine automatische Fortsetzung von DAR, kein automatischer Bandwechsel,
  kein `mkltfs`, `unltfs`, `ltfsck` mit schreibenden Optionen.

**Wichtig:** Terminal-Abschluss und Laufwerkfreigabe können nicht allein anhand
von `/proc/self/mountinfo` verifiziert werden. Die abschließende Bestätigung
liegt weiterhin beim Benutzer.
- Nach `mt offline` bleibt der nächste Mount gesperrt, bis der Benutzer den
  vollständig abgeschlossenen Auswurf ebenfalls über „Status / Abschluss
  bestätigen“ bestätigt.

## Bedienoberfläche – höhenverstellbarer Terminalbereich (2026-10-08)

- Der bisher starre vertikale Aufbau wurde durch ein `Gtk.Paned(VERTICAL)` ersetzt.
- Oberer Bereich: Archivbaum und Dateitabelle mit vorhandener horizontaler Trennleiste.
- Unterer Bereich: DAR-/LTFS-Terminal-Reiter und DAR-Log mit vorhandener horizontaler Trennleiste.
- Die neue Trennleiste zwischen oberem und unterem Bereich lässt sich mit der Maus verschieben.
- Unabhängige DAR- und LTFS-Prozesse bleiben unverändert; keine Prozessänderungen.
- Initiale Trennposition: 425 Pixel; derzeit keine dauerhafte Speicherung der Fensteraufteilung.

## Ausbaustufe 4 – sichere manuelle LTFS-Formatierung (2026-10-08)

- Ein separater, standardmäßig eingeklappter Bereich **„Neues Band vorbereiten / LTFS-Formatierung“** verhindert, dass Formatierung als alltägliche Bandwechselaktion erscheint.
- Der Befehl `mkltfs -d LAUFWERK` (optional `-n VOLUMENAME`) wird **nur im LTFS-Terminal** ausgeführt. **Nie** `-f`, `--force`, `-w`, `--wipe`, `--long-wipe` oder `sudo` durch die GUI.
- Sperre, solange der gewählte Mountpoint oder irgendein LTFS-Dateisystem laut `/proc/self/mountinfo` eingehängt ist, oder während eines ausstehenden LTFS-Mounts, Unmounts, Ejects bzw. Formatierungsschritts. Ohne lesbare Mount-Tabelle bleibt Formatieren gesperrt.
- Der Dialog fordert eine Bandkennung vom **physischen Etikett**, drei separate Sicherheitsbestätigungen und die exakte Eingabe `FORMATIEREN BANDKENNUNG`.
- Der Benutzer bestätigt ausdrücklich, dass das Band nicht mehr benötigte Daten enthält und dass DAR nicht auf das Band schreibt. Die Software kann diese Sachverhalte **nicht selbst beweisen**.
- Nach dem Formatierungsstart bleiben Gerätewechsel, Mount und Eject gesperrt. Der Benutzer muss nach `mkltfs`-Ende den Exit-Code im LTFS-Terminal kontrollieren und den Abschluss manuell freigeben. Die GUI behauptet keinen automatisch bestätigten Formatiererfolg.
- Noch **kein** Abgleich mit dem zukünftigen Medienkatalog und keine verlässliche automatische Erkennung der Identität/Belegung des eingelegten Bandes. **Unbekannt heißt niemals leer.**
- Keine automatische DAR-Fortsetzung und keine Änderungen an der DAR-Slice-Logik.

Hardwaretest der GTK4-Anzeige, der Freigabefolge und der `mkltfs`-Befehlsvariante für den verwendeten LTFS-Treiber stehen aus.
