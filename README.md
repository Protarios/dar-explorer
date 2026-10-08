# DAR Explorer

Grafische Oberfläche für **Disk ARchive (DAR)** unter Linux.

DAR Explorer ist eine GTK-basierte grafische Benutzeroberfläche für das Kommandozeilenprogramm `dar`. Ziel ist es, die Verwaltung auch komplexer DAR-Archive komfortabel über eine grafische Oberfläche zu ermöglichen, ohne die Möglichkeiten von DAR auf einfache Standardfälle zu beschränken.

Ein besonderer Schwerpunkt liegt auf dem Umgang mit **geslicten, unvollständigen und isolierten DAR-Katalogen**. Dadurch können beispielsweise auch Archive untersucht und teilweise extrahiert werden, bei denen nicht mehr alle Slices verfügbar sind.

---

## Inhaltsverzeichnis

1. [Grundkonzept](#grundkonzept)
2. [Unterstützte Archivtypen](#unterstützte-archivtypen)
3. [Archive öffnen](#archive-öffnen)
4. [Vollständige Archive](#vollständige-archive)
5. [Geslicte Archive](#geslicte-archive)
6. [Unvollständige Archive](#unvollständige-archive)
7. [Temporärer isolierter Katalog](#temporärer-isolierter-katalog)
8. [Isolierte Kataloge](#isolierte-kataloge)
9. [Dateibrowser](#dateibrowser)
10. [Extraktion](#extraktion)
11. [Interaktive DAR-Operationen](#interaktive-dar-operationen)
12. [Archiv erstellen](#archiv-erstellen)
13. [Backup-Typen](#backup-typen)
14. [Kompression](#kompression)
15. [Verschlüsselung](#verschlüsselung)
16. [Slicing](#slicing)
17. [Referenzarchive und Referenzkataloge](#referenzarchive-und-referenzkataloge)
18. [Phase-2-Werkzeuge](#phase-2-werkzeuge)
19. [Filesystem-/Archiv-Vergleich](#filesystemarchiv-vergleich)
20. [Katalog isolieren](#katalog-isolieren)
21. [Repair](#repair)
22. [`dar_xform`](#dar_xform)
23. [Passwortbehandlung](#passwortbehandlung)
24. [Terminal und VTE](#terminal-und-vte)
25. [Archiverkennung und Slice-Erkennung](#archiverkennung-und-slice-erkennung)
26. [Besonderheiten bei unvollständigen Archiven](#besonderheiten-bei-unvollständigen-archiven)
27. [Geplante Erweiterungen](#geplante-erweiterungen)
28. [Technischer Aufbau](#technischer-aufbau)
29. [Voraussetzungen](#voraussetzungen)
30. [Grundlegende DAR-Begriffe](#grundlegende-dar-begriffe)

---

## Fensteraufteilung: höhenverstellbares Terminal

Zwischen **Archivansicht** (oben) und **Terminal-/Logbereich** (unten) befindet
sich eine **mit der Maus verschiebbare horizontale Trennleiste**. Ziehe sie
nach oben, um DAR-/LTFS-Terminal und Log mehr Höhe zu geben, oder nach unten,
um die Dateitabelle zu vergrößern. Innerhalb des unteren Bereichs bleibt
die bereits vorhandene vertikale Trennleiste zwischen Terminal und Log erhalten.

Die Reiter **DAR-Terminal** und **LTFS-Terminal** nutzen unverändert zwei
unabhängige VTE-Terminals mit eigenen Shells/PTYs. Weder das Ziehen der Leiste
noch ein Reiterwechsel beendet oder pausiert einen laufenden Prozess. Der
Schalter **Terminal & Log** kann die untere Ausgabe weiterhin ausblenden.

Die Position der Trennleiste wird derzeit beim Neustart auf eine sinnvolle
Ausgangsposition zurückgesetzt.

---

# Grundkonzept

DAR speichert Archive häufig nicht als eine einzige Datei, sondern kann ein Archiv in mehrere sogenannte **Slices** aufteilen.

Beispielsweise:

```text
backup.1.dar
backup.2.dar
backup.3.dar
backup.4.dar
```

Diese Dateien gehören gemeinsam zu einem DAR-Archiv.

Der DAR Explorer unterscheidet deshalb zwischen:

```text
                    DAR-Archiv
                        │
            ┌───────────┴───────────┐
            │                       │
      vollständiges              unvollständig
         Archiv                     Archiv
            │                       │
      alle Slices              Slices fehlen
            │                       │
            │               temporärer isolierter
            │                    Katalog
            │                       │
            └───────────────┬───────┘
                            │
                       Dateibrowser
                            │
                       Extraktion
```

Zusätzlich können **isolierte Kataloge** existieren. Ein isolierter Katalog enthält die Kataloginformationen eines DAR-Archivs getrennt vom eigentlichen Archivinhalt.

Das ist insbesondere bei großen oder nicht vollständig verfügbaren Archiven interessant.

---

# Unterstützte Archivtypen

Der Explorer berücksichtigt mehrere unterschiedliche Situationen:

### 1. Normales vollständiges Archiv

Alle benötigten Slices sind vorhanden.

Beispiel:

```text
backup.1.dar
backup.2.dar
backup.3.dar
```

Der Explorer kann das Archiv normal öffnen und durchsuchen.

### 2. Geslictes Archiv

Ein Archiv besteht aus mehreren Slices.

Beispiel:

```text
backup.1.dar
backup.2.dar
backup.3.dar
...
```

Der Explorer erkennt die zusammengehörigen Slices.

### 3. Unvollständiges Archiv

Nicht mehr alle Slices sind vorhanden.

Beispiel:

```text
backup.1.dar
backup.2.dar
backup.4.dar
```

`backup.3.dar` fehlt.

Der Explorer behandelt diesen Zustand speziell und erzeugt bei Bedarf einen temporären isolierten Katalog.

### 4. Isolierter Katalog

Ein isolierter Katalog ist ein separat gespeicherter DAR-Katalog. Er kann verwendet werden, wenn der Katalog eines Archivs unabhängig vom eigentlichen Archiv aufbewahrt oder verwendet werden soll.

Ein isolierter Katalog ist **nicht mit einem vollständigen normalen DAR-Archiv gleichzusetzen**.

---

# Archive öffnen

Beim Öffnen eines Pfades versucht der Explorer zu bestimmen, um welchen DAR-Zustand es sich handelt.

Dabei wird insbesondere berücksichtigt:

- normale DAR-Slice
- mehrere vorhandene Slices
- fehlende Slices
- vollständiges Archiv
- unvollständiges Archiv
- isolierter Katalog

Das ist wichtig, weil nicht jeder Zustand mit demselben DAR-Aufruf behandelt werden kann.

Der Explorer verwendet daher je nach Situation unterschiedliche Strategien.

---

# Vollständige Archive

Bei einem vollständigen Archiv kann DAR direkt mit dem Archiv arbeiten.

Beispiel:

```text
backup.1.dar
backup.2.dar
backup.3.dar
```

Sind alle erforderlichen Slices vorhanden, kann der Explorer:

- den Katalog lesen
- die Verzeichnisstruktur anzeigen
- Dateien auswählen
- Dateien extrahieren
- das Archiv testen
- weitere Archivoperationen durchführen

Der Benutzer muss sich dabei nicht selbst um die einzelnen Slice-Dateien kümmern.

---

# Geslicte Archive

Bei geslicten Archiven betrachtet der Explorer die einzelnen Dateien nicht als unabhängige Archive.

Beispielsweise:

```text
backup.1.dar
backup.2.dar
backup.3.dar
```

bilden gemeinsam das logische Archiv:

```text
backup
```

Der Explorer erkennt die zugehörigen Slices und kann feststellen, ob die erwartete Slice-Reihe vollständig vorhanden ist.

---

# Unvollständige Archive

Ein besonderer Schwerpunkt des Explorers ist der Umgang mit unvollständigen Archiven.

Angenommen, ein Archiv besteht aus:

```text
backup.1.dar
backup.2.dar
backup.3.dar
backup.4.dar
backup.5.dar
```

aber verfügbar sind nur:

```text
backup.1.dar
backup.2.dar
backup.4.dar
backup.5.dar
```

Dann fehlt:

```text
backup.3.dar
```

Der Explorer erkennt diesen Zustand.

Wichtig ist:

> Ein fehlender Slice kann nicht durch den Explorer oder DAR rekonstruiert werden, wenn die darin enthaltenen Daten physisch nicht mehr vorhanden sind.

Trotzdem kann der Katalog Informationen über das gesamte Archiv enthalten. Genau hier kommt der temporäre isolierte Katalog ins Spiel.

---

# Temporärer isolierter Katalog

Bei einem unvollständigen Archiv erzeugt der Explorer einen **temporären isolierten Katalog**.

Dieser wird beispielsweise unter:

```text
/tmp/dar-explorer/catalogs/
```

abgelegt.

Ein Beispiel:

```text
/tmp/dar-explorer/catalogs/testarchiv.dar.1.dar
```

Der Zweck dieses Katalogs ist es, die vorhandene Archivstruktur unabhängig davon zugänglich zu machen, dass nicht alle ursprünglichen Slices verfügbar sind.

Das ist eine zentrale Funktion des Explorers.

## Warum wird der Katalog temporär erzeugt?

Ein unvollständiges Archiv kann nicht wie ein vollständig verfügbares Archiv behandelt werden.

Der Explorer nutzt deshalb die Kataloginformationen, um trotzdem:

- die Archivstruktur darzustellen
- vorhandene Dateien zu identifizieren
- verfügbare Daten zu extrahieren
- fehlende Daten von tatsächlich vorhandenen Daten zu unterscheiden

Der temporäre Katalog ist damit eine Art **Arbeitskatalog für ein nur teilweise verfügbares Archiv**.

## Wichtig: Temporärer Katalog ≠ Reparatur

Der temporäre isolierte Katalog repariert das Archiv nicht.

Er stellt lediglich die vorhandenen Kataloginformationen separat bereit.

Wenn beispielsweise:

```text
backup.3.dar
```

fehlt, werden die darin gespeicherten Daten dadurch nicht wiederhergestellt.

Der Explorer kann aber trotzdem feststellen, welche Dateien betroffen sind und welche Daten aus den vorhandenen Slices noch extrahiert werden können.

---

# Isolierte Kataloge

Ein isolierter Katalog kann auch unabhängig vom automatischen Umgang mit unvollständigen Archiven geöffnet werden.

Beispiel:

```text
mein_katalog.1.dar
```

Ein isolierter Katalog ist dabei ein besonderer DAR-Zustand.

Er darf insbesondere nicht einfach mit einem vollständigen Archiv gleichgesetzt werden.

Der Explorer erkennt und behandelt diesen Zustand separat.

---

# Dateibrowser

Nach erfolgreichem Öffnen wird die im Katalog gespeicherte Verzeichnisstruktur dargestellt.

Typischerweise werden Informationen wie:

- Dateiname
- Verzeichnis
- Größe
- Datum
- Dateityp

angezeigt.

Der Dateibereich bildet die Grundlage für die Auswahl von Dateien und Verzeichnissen zur weiteren Verarbeitung.

---

# Extraktion

Die Extraktion ist abhängig vom Zustand des geöffneten Archivs.

## Normales Archiv

Bei einem vollständigen Archiv kann die Extraktion direkt über DAR erfolgen.

Der Benutzer kann Dateien oder Verzeichnisse auswählen und als Ziel ein Dateisystemverzeichnis angeben.

## Mehrere Dateien

Der Explorer übergibt mehrere ausgewählte Pfade möglichst in **einem DAR-Aufruf**.

Dadurch wird vermieden, dass für jede einzelne Datei ein separater DAR-Prozess gestartet werden muss.

## Unvollständiges Archiv

Bei einem unvollständigen Archiv wird der zuvor erzeugte temporäre isolierte Katalog verwendet.

Damit kann DAR die vorhandenen Archivinformationen nutzen, obwohl nicht alle Slices vorhanden sind.

Die Extraktion kann dabei nur Daten liefern, die tatsächlich in den vorhandenen Slices verfügbar sind.

## Komplexe Extraktionen

Komplexe Fälle werden nicht zwangsläufig als rein grafischer Vorgang ausgeführt.

Dazu gehören beispielsweise Situationen mit:

- fehlenden Slices
- isoliertem Katalog
- Passwortabfrage
- DAR-Rückfragen
- komplexeren Archivzuständen

Hier wird das DAR-Terminal sichtbar gemacht.

---

# Interaktive DAR-Operationen

Für Operationen, bei denen DAR mit dem Benutzer kommunizieren muss, besitzt der Explorer ein eingebettetes Terminal.

Dieses basiert auf einer PTY/VTE-Umgebung.

Dadurch können DAR-Ausgaben und Rückfragen direkt angezeigt werden.

Beispielsweise:

```text
DAR fragt nach Passwort
        │
        ▼
Terminal
        │
        ▼
Benutzereingabe
        │
        ▼
DAR setzt Operation fort
```

Das Verhalten ist damit näher an der direkten Verwendung von DAR im Terminal als eine vollständig abstrahierte GUI.

---

# Archiv erstellen

Der Explorer kann neue DAR-Archive erstellen.

Dabei werden unter anderem folgende Optionen unterstützt:

- Quelle
- Zielarchiv
- Full Backup
- Differential Backup
- Incremental Backup
- Referenzarchiv
- Referenzkatalog
- Kompression
- Kompressionsstufe
- Verschlüsselung
- Slice-Größe
- Größe des ersten Slices
- leere Verzeichnisse
- Zugriffzeiten
- Hardlinks
- Sparse Files
- Extended Attributes
- ACLs
- numerische Eigentümer
- DAR-Terminologie
- Dry Run

---

# Backup-Typen

## Full Backup

Ein vollständiges Archiv enthält den vollständigen Datenbestand.

Beispiel:

```text
backup-full.1.dar
backup-full.2.dar
...
```

## Differential Backup

Ein Differential Backup verwendet ein Referenzarchiv. Es enthält die Änderungen gegenüber dieser Referenz.

Beispiel:

```text
full.1.dar
full.2.dar
```

als Referenz und anschließend:

```text
diff.1.dar
diff.2.dar
```

## Incremental Backup

Incremental Backups bauen auf vorherigen Sicherungsständen auf.

Dadurch kann eine Folge von Sicherungsständen entstehen:

```text
Full
  │
  ├── Incremental 1
  │
  ├── Incremental 2
  │
  └── Incremental 3
```

Der Explorer unterstützt die entsprechenden Referenzinformationen bei der Archiv-Erstellung.

---

# Kompression

Bei der Archiv-Erstellung kann die Kompression ausgewählt werden.

Die Kompressionsstufe kann ebenfalls eingestellt werden.

Die tatsächlich verfügbaren Algorithmen hängen von der verwendeten DAR-Version und deren Build ab.

---

# Verschlüsselung

DAR unterstützt verschlüsselte Archive.

Der Explorer bietet hierfür die entsprechenden Einstellungen und übergibt die Konfiguration an DAR.

Bei passwortgeschützten Archiven wird das Passwort nicht als normale Dateiinformation im Dateibrowser behandelt.

Bei interaktiven Vorgängen kann DAR die Passworteingabe über das eingebettete Terminal anfordern.

---

# Slicing

Große Archive können in mehrere Slices aufgeteilt werden.

Beispiel:

```text
backup.1.dar
backup.2.dar
backup.3.dar
```

Die Slice-Größe kann bei der Archiv-Erstellung festgelegt werden.

Zusätzlich kann eine separate Größe für den ersten Slice angegeben werden.

Das ist insbesondere für spezielle Speichermedien und spätere Tape-/LTFS-Szenarien relevant.

---

# Referenzarchive und Referenzkataloge

Für Differential- und Incremental-Backups kann eine Referenz angegeben werden.

Dabei kann die Referenz entweder ein normales DAR-Archiv oder ein entsprechender Katalog sein.

Der Explorer unterscheidet diese Fälle.

Dadurch kann beispielsweise ein isolierter Katalog als Referenz verwendet werden, ohne dass das gesamte ursprüngliche Archiv für diesen Zweck verfügbar sein muss.

---

# Phase-2-Werkzeuge

Die zweite Entwicklungsphase konzentriert sich auf grundlegende DAR-Operationen.

Die geplante Struktur umfasst:

1. Filesystem-/Archiv-Vergleich
2. Katalog isolieren
3. Repair
4. `dar_xform`
5. Filter-Grundlagen
6. Archiv-Kommentare
7. Slice-Grundlagen
8. `dar_manager`-Grundlagen

Einige dieser Funktionen befinden sich noch in der Weiterentwicklung.

---

# Filesystem-/Archiv-Vergleich

Der Explorer kann ein Archiv mit einem Verzeichnis im Dateisystem vergleichen.

Konzeptionell:

```text
Archiv
  │
  │ Vergleich
  ▼
Dateisystem
```

Dabei wird DAR verwendet, um Unterschiede zwischen dem archivierten Zustand und dem aktuellen Dateisystem zu ermitteln.

Der Vergleich funktioniert auch in Verbindung mit einem isolierten Katalog.

---

# Katalog isolieren

Mit der Funktion **Katalog isolieren** kann der Katalog eines Archivs separat gespeichert werden.

Das ist besonders interessant für:

- große Archive
- Offline-Medien
- Tape-Archive
- unvollständige Archive
- spätere Referenzoperationen

Das Ergebnis ist ein separater DAR-Katalog.

Ein solcher Katalog kann später unabhängig vom ursprünglichen vollständigen Archiv weiterverwendet werden.

---

# Repair

DAR besitzt mit `-y` eine Reparaturfunktion.

Dabei wird nicht einfach ein vorhandenes Archiv „an Ort und Stelle“ repariert.

Stattdessen wird ein neues Archiv erzeugt, soweit dies aus den verfügbaren Daten möglich ist.

Grundprinzip:

```text
beschädigtes / unvollständiges Archiv
                │
                │ dar -y
                ▼
         neues Archiv
```

Dabei gilt:

> Physisch fehlende Daten können nicht rekonstruiert werden.

Die Reparatur kann deshalb nur Daten übernehmen, die noch verfügbar und lesbar sind.

Die Reparaturfunktion wird im Explorer als eigenständige Operation behandelt und nicht automatisch auf ein gerade geöffnetes Archiv angewendet.

---

# `dar_xform`

`dar_xform` ist ein separates DAR-Werkzeug zur Transformation bestehender Archive.

Es kann unter anderem verwendet werden, um die Slice-Struktur eines vorhandenen Archivs zu verändern.

Der Explorer stellt dafür insbesondere folgende Parameter bereit:

- Quellarchiv
- Zielarchiv
- Slice-Größe
- Größe des ersten Slices

Die Größe des ersten Slices kann unabhängig von der normalen Slice-Größe angegeben werden.

Beispiel:

```text
dar_xform
    │
    ├── Quelle
    ├── Ziel
    ├── -s ...
    └── -S ...
```

---

# Passwortbehandlung

Passwortgeschützte Archive benötigen eine sichere Eingabemöglichkeit.

Für einfache Operationen kann das Passwort über einen entsprechenden Dialog eingegeben werden.

Bei interaktiven DAR-Vorgängen wird dagegen das eingebettete Terminal verwendet.

Dadurch kann DAR seine normalen Passwort- und Rückfragemechanismen verwenden.

---

# Terminal und VTE

Der Explorer verwendet ein eingebettetes Terminal für DAR-Operationen, bei denen eine direkte Kommunikation mit DAR notwendig ist.

Das Terminal ist standardmäßig möglichst unauffällig bzw. eingeklappt.

Bei komplexen Vorgängen, Fehlern oder interaktiven Rückfragen wird es automatisch sichtbar.

Damit wird die Oberfläche nicht unnötig mit Terminalausgaben überladen, ohne auf die Diagnosemöglichkeiten der DAR-Kommandozeile zu verzichten.

---

# Archiverkennung und Slice-Erkennung

Eine wichtige Aufgabe des Explorers besteht darin, nicht nur einzelne Dateien zu öffnen, sondern den Zusammenhang zwischen DAR-Slices zu verstehen.

Beispiel:

```text
testarchiv.1.dar
testarchiv.2.dar
testarchiv.3.dar
```

werden als ein Archiv behandelt:

```text
testarchiv
```

Fehlt beispielsweise:

```text
testarchiv.2.dar
```

wird der Zustand als unvollständig erkannt.

Dadurch kann der Explorer anschließend die passende Strategie auswählen.

---

# Besonderheiten bei unvollständigen Archiven

Der Umgang mit unvollständigen Archiven ist eine der wichtigsten Funktionen des Explorers.

Der entscheidende Unterschied lautet:

### Zugriff auf ein unvollständiges Archiv

Ziel:

> So viele Informationen und Daten wie möglich zugänglich machen.

Dafür wird der temporäre isolierte Katalog verwendet.

### Reparatur eines unvollständigen Archivs

Ziel:

> Aus den noch verfügbaren Daten ein neues, möglichst vollständiges Archiv erzeugen.

Dafür wird DARs `-y`-Funktion verwendet.

Diese beiden Vorgänge dürfen nicht miteinander verwechselt werden.

---

# Temporärer Katalog und zukünftige Tape-Unterstützung

Die temporären isolierten Kataloge sind nicht nur für lokale unvollständige Archive wichtig.

Sie bilden auch eine Grundlage für den später geplanten Umgang mit **LTFS und Tape-Archiven**.

Ein Archiv kann beispielsweise aus mehreren Slices bestehen, die auf einem Band gespeichert sind:

```text
Tape
 │
 ├── backup.1.dar
 ├── backup.2.dar
 ├── backup.3.dar
 └── ...
```

Ist das Tape momentan nicht vollständig verfügbar, kann ein zuvor gesicherter Katalog trotzdem Informationen über das Archiv liefern.

Dadurch kann der Explorer später zwischen

```text
Katalog verfügbar
       +
einige Slices verfügbar
       +
andere Slices momentan nicht verfügbar
```

unterscheiden.

Das ist insbesondere für große Tape-Archive von Bedeutung.

---

# Geplante Erweiterungen

Die weitere Entwicklung ist in Phasen organisiert.

## Phase 3 – Archivbrowser

Geplant bzw. in Arbeit:

1. echte Fortschrittsanzeige
2. Kontextmenü im Dateibereich
3. Extraktion per Doppelklick
4. Auswahl ganzer Verzeichnisbäume
5. Sortierung nach Name, Größe und Datum
6. Filter nach Datei/Verzeichnis
7. Archivintegritätsprüfung
8. Slice-Übersicht
9. Detaildialog mit DAR-Metadaten
10. optionaler Hintergrundindex für sehr große Archive

## Phase 4 – LTFS / Tape

Eine spätere Phase konzentriert sich auf die direkte Verwaltung von Tape-/LTFS-Archiven.

Geplant sind unter anderem:

- LTFS-Modus beim Erstellen/Öffnen
- Geräteerkennung
- Tape Manager
- Initialisieren und Formatieren
- Mount/Unmount
- Load/Eject
- Verschlüsselung über `stenc`
- Slice-to-Tape
- Tape-to-Disk
- Tape-/Slice-Planung
- Kopieren von DAR-Slices auf LTFS
- Wiederherstellung von DAR-Slices von LTFS

Ein wesentlicher Anwendungsfall ist ein DAR-Archiv, dessen Slices über ein Tape verteilt sind.

## Phase 5 – Advanced DAR

Die weiterführenden DAR-Funktionen umfassen unter anderem:

- Filter-Editor
- Archiv-Kommentare
- Slice-Manager
- `dar_manager`
- Merge / Subsets
- Snapshots
- Binary Delta
- Remote/SFTP
- Hooks
- Scheduling
- `dar_slave`

Diese Funktionen werden bewusst von den grundlegenden Phase-2- und Browser-Funktionen getrennt entwickelt.

---

# Technischer Aufbau

Der Explorer ist in mehrere Bereiche gegliedert.

Vereinfacht:

```text
dar-explorer/
│
├── dar_explorer.py
│
├── dar_backend/
│   ├── __init__.py
│   ├── archive.py
│   ├── capabilities.py
│   └── operations.py
│
├── dialogs/
│   └── phase2_tools.py
│
└── ...
```

Das Backend kapselt die Erzeugung der DAR-Kommandos.

Beispielsweise wird nicht an jeder Stelle des GUI-Codes ein eigener DAR-Aufruf zusammengebaut.

Stattdessen existieren dedizierte Funktionen wie:

```text
command_compare()
command_isolate()
command_repair()
command_test()
command_xform()
```

Dadurch bleibt die GUI von den konkreten DAR-Kommandozeilenparametern weitgehend getrennt.

---

# DAR-Kommandos

Der Explorer verwendet das installierte DAR-Programm.

Typischerweise wird zunächst nach dem ausführbaren Programm gesucht.

Die lokale Installation wird dabei berücksichtigt; als Fallback kann beispielsweise

```text
/usr/local/bin/dar
```

verwendet werden.

Für `dar_xform` gilt entsprechend das separate Programm `dar_xform`.

---

# Voraussetzungen

Für den Betrieb werden mindestens benötigt:

- Linux
- Python 3
- GTK 4
- VTE / GTK-Terminal-Unterstützung
- DAR 2.8.x bzw. eine kompatible DAR-Version
- `dar_xform` für die entsprechende Transformationsfunktion

Die tatsächlich verfügbaren DAR-Funktionen hängen zusätzlich von der verwendeten DAR-Konfiguration und deren Build ab.

---

# Grundlegende DAR-Begriffe

## Archiv

Das logische Gesamtergebnis einer DAR-Sicherung.

Beispiel:

```text
backup
```

## Slice

Ein physischer Teil eines geslicten DAR-Archivs.

Beispiel:

```text
backup.1.dar
backup.2.dar
backup.3.dar
```

## Katalog

Der Katalog enthält die Informationen, die DAR benötigt, um die Struktur und Inhalte des Archivs zu beschreiben.

## Isolierter Katalog

Ein separat gespeicherter Katalog.

Er ermöglicht es, Kataloginformationen unabhängig vom normalen internen Archivkatalog zu verwenden.

## Temporärer isolierter Katalog

Ein vom Explorer erzeugter Arbeitskatalog für ein unvollständiges Archiv.

Er wird insbesondere benötigt, wenn nicht alle Slices eines Archivs verfügbar sind.

## Vollständiges Archiv

Alle für die gewünschte Operation benötigten Slices sind verfügbar.

## Unvollständiges Archiv

Mindestens ein benötigter Slice fehlt.

Die Kataloginformationen können trotzdem noch ausreichen, um die Struktur des Archivs darzustellen und vorhandene Daten zu extrahieren.

## Reparatur

Die Rekonstruktion eines neuen Archivs aus den noch verfügbaren Daten eines beschädigten oder unvollständigen Archivs.

Reparatur bedeutet **nicht**, dass fehlende Daten wiederhergestellt werden können.

---

# Designprinzip

Der wichtigste Grundsatz des DAR Explorers lautet:

> **Die GUI soll DAR nicht vereinfachen, indem sie seine Möglichkeiten entfernt, sondern DAR so kapseln, dass auch komplexe Archivzustände kontrollierbar bleiben.**

Deshalb werden insbesondere:

- normale Archive
- geslicte Archive
- unvollständige Archive
- isolierte Kataloge
- temporäre Kataloge
- interaktive DAR-Prozesse

unterschiedlich behandelt.

Dadurch kann der Explorer sowohl für gewöhnliche lokale Backups als auch später für große, geslicte und auf Tape ausgelagerte Archive eingesetzt werden.

---

## Status

Der aktuelle Entwicklungsstand bildet die grundlegenden DAR-Funktionen und den Umgang mit vollständigen, geslicten und unvollständigen Archiven bereits ab.

Die Entwicklung erfolgt schrittweise nach den definierten Phasen. Funktionen späterer Phasen werden bewusst nicht vorgezogen, damit die grundlegenden Archivoperationen zunächst stabil abgeschlossen werden.

---

# LTFS-Integration (erste Ausbaustufe)

DAR Explorer besitzt **zwei voneinander unabhängige VTE-Terminals** als Reiter im unteren Fensterbereich:

- **DAR-Terminal:** bisherige DAR-Kommandos, inklusive interaktiver Rückfragen zum nächsten Slice.
- **LTFS-Terminal:** separate Shell/PTY für Mount, Unmount und weitere LTFS-Kommandos.

Die Terminals laufen parallel. Ein Wechsel des Reiters unterbricht die gerade laufende Operation nicht. Auch das bestehende DAR-Ausgabelog bleibt unverändert.

## Einrichtung

LTFS muss separat installiert und für das Bandlaufwerk eingerichtet sein. Die Anwendung überprüft vor dem Mounten, ob der Befehl `ltfs` verfügbar ist. Das eingetragene **Bandlaufwerk** ist beispielsweise `/dev/sg3` (je nach LTFS-Version auch Laufwerksseriennummer); den korrekten Gerätenamen muss man am eigenen System ermitteln. Das Mount-Verzeichnis, beispielsweise `/mnt/ltfs`, muss bereits existieren. Es gibt keine automatische Formatierung: Die **manuelle** `mkltfs`-Formatierung befindet sich in einem gesonderten Sicherheitsdialog (siehe unten). Das Programm nutzt **kein** `sudo`.

Optional lassen sich Vorgabewerte beim Programmstart über Umgebungsvariablen setzen:

```sh
export DAR_EXPLORER_LTFS_DEVICE=/dev/sg3
export DAR_EXPLORER_LTFS_MOUNTPOINT=/mnt/ltfs
./run.sh
```

## Bedienung / Bandwechsel

1. Im Reiter **LTFS-Terminal** ein formatiertes LTFS-Band einlegen, Gerät und Mountpoint angeben und **Band mounten** anklicken. Der LTFS-Befehl wird in der separaten Shell im Hintergrund gestartet, damit die Shell auch beim Unmount bedienbar bleibt.
2. Bei der Archiv-Erstellung als Archivziel den **LTFS-Mountpoint** wählen und das Slicing aktivieren. DAR arbeitet weiterhin im eigenen Terminal.
3. **Erst wenn DAR das aktuelle Slice vollständig abgeschlossen hat und auf das nächste Band wartet**, im LTFS-Reiter **Band aushängen** aufrufen. Die Sicherheitsabfrage bestätigt, dass DAR nicht mehr auf das Medium schreibt.
4. **Nicht unmittelbar das Band entnehmen:** `umount` kann zurückkehren, während der LTFS-Prozess noch die Metadaten auf dem Band abschließt. Die Ausgabe des LTFS-Terminals und die vollständig abgeschlossene Abmeldung des Bandlaufwerks abwarten. Die Anzeige „Nicht gemountet“ allein ist **kein** sicheres Eject-Signal.
5. Nach vollständigem LTFS-Abschluss über **Status / Freigabe** explizit bestätigen, dass LTFS beendet ist. Erst dann das Band physisch wechseln, ein neues Band einlegen, **Band mounten** wählen und den erfolgreichen Mount prüfen.
6. Erst dann im **DAR-Terminal** die wartende DAR-Abfrage bestätigen.

Die Schaltflächen verwenden normale LTFS-/FUSE-Kommandos (`ltfs -o devname=… MOUNTPOINT`; `fusermount3 -u`, ersatzweise `fusermount -u` oder `umount`). **Kein** Force-/Lazy-Unmount und **kein** automatisches Auswerfen, Formatieren oder Überschreiben; Formatieren ist ausschließlich über den gesonderten manuellen Dialog möglich. Ein Mountpoint mit einem anderen Dateisystem wird nicht ausgehängt.

**Aktuelle Grenze:** Die Anwendung kann die DAR-Bandwechselaufforderung derzeit nicht zuverlässig aus dem Terminal erkennen. Die Koordination bleibt daher absichtlich manuell. Ein erfolgreicher Mount/Unmount ist erst auf einem realen LTFS-Laufwerk verifizierbar; hier wurden ausschließlich die Python-Kommandos getestet.

Falls Mount oder Unmount fehlschlägt, lässt sich der gesperrte Schalter über **Status / Freigabe** erst nach einer gesonderten Bestätigung zurücksetzen.

### LTFS – vollständige manuelle Bedienung (Ausbaustufe 2)

Die LTFS-Bedienung liegt ausschließlich im separaten LTFS-Terminal. DAR läuft
währenddessen unabhängig weiter und kann auf eine Medienabfrage warten.

1. **Laufwerke ermitteln:** „LTFS-Laufwerke anzeigen“ sendet `ltfs -o device_list`
   an das LTFS-Terminal. Gerät wie `/dev/sg3` bzw. eine unterstützte Seriennummer
   in **LTFS-Laufwerk** eintragen. Der Gerätename für `mt` ist davon zu
   unterscheiden: oft `/dev/nst0`. Beide müssen demselben physischen Laufwerk
   entsprechen. „Laufwerksstatus (mt)“ ist nur bei nicht aktivem LTFS freigegeben.
2. **Mountpoint vorbereiten:** absoluten Verzeichnispfad angeben. Die
   Schaltfläche „Mountpoint anlegen“ erzeugt ihn mit eigenen Benutzerrechten,
   ohne `sudo`, und greift nicht auf das Band zu. Fehlende Berechtigungen müssen
   außerhalb der Anwendung administrativ eingerichtet werden.
3. **Einstellungen speichern:** Speichert die drei Eingaben in
   `~/.config/dar-explorer/config.json`. Die Umgebungsvariablen
   `DAR_EXPLORER_LTFS_DEVICE`, `DAR_EXPLORER_LTFS_TAPE_DEVICE` und
   `DAR_EXPLORER_LTFS_MOUNTPOINT` können diese Vorgaben beim Start übersteuern.
4. **Band mounten:** „1. Band mounten“ startet LTFS in der LTFS-Shell; den
   tatsächlichen Erfolg im Terminal sowie den gemeldeten Mountstatus prüfen.
5. **DAR schreibt Slices:** im DAR-Terminal das Ziel im LTFS-Mountpoint angeben.
   Der Bandwechsel darf erst erfolgen, wenn DAR wirklich auf das **nächste**
   Slice wartet und nicht mehr auf das alte Band schreibt.
6. **Aushängen:** „2. Band aushängen“ erfordert eine explizite Bestätigung und
   sendet einen regulären FUSE-Unmount ins **LTFS-Terminal**. Keinesfalls
   einen erzwungenen/lazy Unmount verwenden. Auch wenn der Mountpoint bereits
   verschwunden ist, warten, bis **LTFS selbst** seine Index-Schreibarbeiten
   und den Prozessabschluss im Terminal beendet hat.
7. **Freigeben:** „3. Status / Abschluss bestätigen“ erst nach Kontrolle des
   tatsächlichen LTFS-Abschlusses wählen. Ab diesem Zeitpunkt ist ein
   manueller Bandwechsel zulässig. „Band auswerfen (mt)“ ist optional:
   `mt -f /dev/nstX offline` (spult zurück und entlädt bei unterstütztem
   Laufwerk). Das erfordert nochmals eine explizite Bestätigung.
8. **Neues Band:** manuell einsetzen, mit „1. Band mounten“ einhängen, Erfolg
   prüfen und **erst danach** die DAR-Abfrage in dessen eigenem Terminal
   bestätigen.

**Schutz:** Gerät und Mountpoint sind während aktiver LTFS-Prozesse gesperrt.
Ein versehentliches Schließen der Anwendung während LTFS-Mount oder -Unmount
wird verhindert, um den Index nicht zu gefährden. Hardwareoperationen sind
noch nicht auf realer Tape-Hardware getestet. „Laufwerksstatus (mt)“ und
„Band auswerfen (mt)“ verwenden ausdrücklich **nicht** das LTFS-SG-Gerät.
Formatieren/Neuinitialisieren von Medien (`mkltfs`/`unltfs`) ist hier bewusst
nicht implementiert, weil es vorhandene Daten zerstören kann.

Nach dem optionalen `mt offline` erscheint eine weitere Statussperre: Erst
wenn der Befehl im LTFS-Terminal beendet ist, über „Status / Abschluss
bestätigen“ den Bandwechsel freigeben.

### Manuelle Vorbereitung / Formatierung eines neuen LTFS-Bandes

Eine neue Bandkassette muss vor dem LTFS-Mount gegebenenfalls formatiert werden.
Dazu im LTFS-Reiter den eingeklappten Bereich **„Neues Band vorbereiten / LTFS-Formatierung (Datenverlust!)“** öffnen.

1. Mit „LTFS-Laufwerke anzeigen“ und ggf. „Laufwerksstatus (mt)“ das richtige **physische Laufwerk** bestimmen; Bandetikett vor Ort kontrollieren. Das eingelegte Band muss ausdrücklich zur Neuformatierung freigegeben sein. **Ein unbekanntes Band ist nicht leer.**
2. Sicherstellen, dass DAR nicht auf dieses Band schreibt und dass LTFS vollständig ausgehängt ist. Der Knopf ist bei gemountetem LTFS oder noch laufender LTFS-Operation gesperrt.
3. „Formatierung prüfen …“ öffnen, die **Bandkennung vom physischen Etikett** eingeben, optional einen LTFS-Volumenamen festlegen. Alle drei Sicherheitsbestätigungen aktivieren. Zur letzten Freigabe `FORMATIEREN BANDKENNUNG` exakt eingeben und bestätigen.
4. Die Anwendung startet **ausschließlich im LTFS-Terminal** `mkltfs -d GERÄT` (optional `-n NAME`) und zeigt dort den Exit-Code. Ohne `--force`, `--wipe` oder sonstige erzwungene Löschoptionen. **Auch ohne `--force` können Daten auf bislang nicht als LTFS formatierten Medien verloren gehen.**
5. Erst **nach tatsächlichem Programmende** im LTFS-Terminal „3. Status / Abschluss bestätigen“ auswählen und Abschluss freigeben. Bei Erfolg (`Exit-Code: 0`) kann das Band danach wie gewohnt gemountet werden. Bei Fehlern zuerst die Terminalausgabe analysieren, niemals blind wiederholt formatieren.

**Wesentliche Einschränkung:** Der zukünftige Medien-/Slice-Katalog existiert noch nicht. Die Anwendung kann folglich **nicht feststellen**, ob ein eingelegtes Band bereits Teil eines früheren Backups ist. Weder das Etikett noch die Bestätigung beweisen die Datenfreiheit. Ohne sichere Kenntnis der Bandhistorie **nicht formatieren**. Die endgültige kataloggestützte Formatierungssperre wird nach der LTFS-Grundintegration implementiert.

Die GUI läuft ohne `sudo`. Zugriffsrechte müssen separat eingerichtet sein. Der manuelle Bandwechsel bleibt unabhängig: DAR wartet im eigenen Terminal, LTFS wird im zweiten Terminal bedient und abgeschlossen, DAR wird erst nach erfolgreichem Mount manuell fortgesetzt.
