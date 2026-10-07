# DAR Explorer V2

GTK4-GUI für DAR mit Mehrfachauswahl und Extraktion.

## Installation – CachyOS

```bash
sudo pacman -S python-gobject gtk4 vte4
```

DAR selbst muss bereits installiert sein:

```bash
dar -V
```

## Start

```bash
chmod +x run.sh
./run.sh ~/dar-test.1.dar
```

oder:

```bash
./run.sh
```

Dann **Archiv öffnen** wählen.

## V2-Funktionen

### 1. Hierarchische Archivansicht

Links befindet sich der Verzeichnisbaum. Rechts werden die Einträge des ausgewählten Verzeichnisses angezeigt.

### 2. Mehrfachauswahl

Im rechten Bereich können mehrere Dateien und Verzeichnisse gleichzeitig ausgewählt werden.

GTK4 unterstützt dabei die üblichen Auswahlmechanismen:

- `Ctrl` + Klick: einzelne weitere Einträge
- `Shift` + Klick: Bereich auswählen
- `Ctrl+A`: je nach Fokus alle sichtbaren Einträge

### 3. Auswahl extrahieren

Nach der Auswahl:

**Auswahl extrahieren**

Dann ein Zielverzeichnis auswählen.

Der Prototyp ruft DAR sinngemäß auf:

```text
dar -x ARCHIV -R ZIEL -g DATEI1 -g VERZEICHNIS2 ...
```

Die eigentliche Extraktion übernimmt vollständig DAR.

### 4. Fortschritt

Während DAR läuft, bleibt die GUI bedienbar. Der Fortschrittsbalken arbeitet in V2 bewusst als indeterminierter Fortschrittsindikator.

Der Grund: Die normale DAR-Konsolenausgabe ist kein stabil spezifiziertes Maschinenformat. Eine echte Prozentanzeige wird deshalb in einer späteren Version über eine gezielte DAR-Ausgabe-/Parserstrategie ergänzt.

### 5. DAR-Ausgabe

Unter **DAR-Ausgabe anzeigen** kann das vollständige laufende DAR-Protokoll eingeblendet werden.

Das ist besonders hilfreich bei:

- beschädigten Archiven
- fehlenden Slices
- Berechtigungsproblemen
- Passwort-/Verschlüsselungsproblemen
- sonstigen DAR-Fehlern

## Architektur

V2 verwendet weiterhin bewusst:

```text
GTK4 / Python
       │
       ▼
   dar CLI
       │
       ▼
   DAR-Archiv
```

und nicht:

```text
GTK4
  │
  ▼
libdar C++ API
```

Damit bleibt die GUI unabhängig von der aktuell problematischen API-Kompatibilität zwischen GDAR und deiner aktuellen libdar-Version.

## Wichtiger Hinweis zur Extraktion

Die Extraktion ist in V2 absichtlich auf eine einzelne DAR-Operation mit mehreren `-g`-Argumenten konzentriert.

Die ausgewählten Archivpfade werden **nicht** selbst kopiert oder entpackt. DAR bleibt für:

- Dekompression
- Entschlüsselung
- Integritätsprüfung
- Slice-Verwaltung
- Dateiattribute

zuständig.

## Projektstruktur

```text
dar-explorer/
├── dar_explorer.py
├── dar_parser.py
├── run.sh
├── README.md
└── tests/
    ├── test_parser.py
    └── run-test.sh
```

## Nächste sinnvolle V3

Für wirklich große Archive würde ich danach nicht einfach weitere Buttons hinzufügen, sondern die Architektur gezielt erweitern:

1. echte Fortschrittsanzeige
2. Kontextmenü im Dateibereich
3. Extraktion per Doppelklick
4. Auswahl ganzer Verzeichnisbäume
5. Sortierung nach Name/Größe/Datum
6. Filter nach Datei/Verzeichnis
7. Archivintegritätsprüfung
8. Slice-Übersicht
9. Detaildialog mit DAR-Metadaten
10. optionaler Hintergrundindex für sehr große Archive


## Phase 3
See PHASE3.md.
