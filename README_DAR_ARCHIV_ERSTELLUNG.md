# DAR Explorer – modulare Archiv-Erstellung

Diese Version baut die neue Archiv-Erstellung bewusst als eigene Module neben dem bestehenden Explorer auf.

## Neue Struktur

- `dar_explorer.py` – bestehende Explorer-/Extraktionslogik; nur dünne Integration des neuen Dialogs
- `dar_backend/archive.py` – Datenmodell und DAR-CLI-Erzeugung
- `dar_backend/capabilities.py` – DAR-Version und unterstützte UI-Auswahl
- `dialogs/archive_create.py` – GTK4-Dialog für Voll-/Differentialbackup, Verschlüsselung, Slicing, Kompression und Profile
- `config/profiles.py` – JSON-Profile (`*.dargui.json`)

## Wichtige Designentscheidung: echtes Terminal

Die Archiv-Erstellung wird **nicht** über `subprocess.PIPE` oder eine künstliche stdin-Pipeline gestartet. Der fertige, shell-quotierte DAR-Befehl wird in die bereits laufende VTE-Shell eingespeist.

Das ist absichtlich so: DAR kann Passwörter, Slice-/Medienwechsel und weitere Rückfragen interaktiv stellen. Das Terminal bleibt dabei die tatsächliche PTY-Verbindung zu DAR.

Passwörter werden beim Erstellen nicht als Klartext in die Kommandozeile geschrieben. Bei aktivierter symmetrischer Verschlüsselung wird z. B. `-K aes256:` verwendet; DAR fordert das Passwort anschließend interaktiv an.

## Differentialbackup und isolierter Katalog

Bei einem normalen Referenzarchiv wird die DAR-Archivbasis verwendet. Ein isolierter Katalog wird dagegen **mit seinem exakten Dateinamen** an `-A` übergeben, z. B. `testarchiv.dar.1.dar`.

Damit bleibt insbesondere der bereits reparierte Fall erhalten, dass ein isolierter Katalog nur Dateireferenzen enthält und die eigentlichen Daten weiterhin aus dem echten Archiv kommen.

## Extraktion aus isoliertem Katalog

Die Extraktion wird als **ein einziger** DAR-Aufruf ins Terminal gegeben, auch wenn mehrere Dateien ausgewählt wurden. Das verhindert, dass bei einem interaktiven Slice-/Passwort-Prompt ein nachfolgend gepuffertes Shell-Kommando versehentlich als DAR-Eingabe interpretiert wird.

Der Benutzer wählt dafür ein vorhandenes Slice des echten Archivs. DAR erhält daraus die Archivbasis und kann fehlende Slices anschließend selbst über das Terminal anfordern.

## Profile

Profile enthalten nur Einstellungen, niemals Passwörter. Ein Beispiel:

```json
{
  "format": 1,
  "dar_explorer": {
    "source": "/home/user/Daten",
    "archive": "/backup/daten",
    "reference": "/backup/daten_full.1.dar",
    "reference_is_catalog": false,
    "encryption_enabled": true,
    "encryption_algorithm": "aes256",
    "compression": "gzip",
    "compression_level": 6,
    "slice_enabled": true,
    "slice_size": "4G"
  }
}
```

Die Profile können frei gespeichert und wieder geladen werden. Zusätzlich wird der Standardordner `~/.config/dar-explorer/` für `.dargui.json`-Profile verwendet.

## Noch bewusst nicht über CLI-Schalter vorgetäuscht

DAR unterstützt viele weitere Optionen. Für ACL/XATTR/Hardlinks/Sparse-Dateien werden in dieser ersten modularen Stufe keine versionsabhängigen Fantasie-Schalter erzeugt. Diese Eigenschaften bleiben der DAR-Version und ihren Defaults überlassen, bis die exakten 2.8.x-Optionen als eigener Optionssatz integriert sind.
