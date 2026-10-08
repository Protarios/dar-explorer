# Phase 4 – LTFS-Grundintegration

Erste Umsetzung (2026-10-08):

- DAR-Terminal bleibt unverändert; neues LTFS-Terminal mit separatem VTE/PTY und eigener Shell.
- Reiterwechsel unterbricht keine laufende Shell/Operation.
- LTFS-Gerät und Mountpoint einstellbar; Mount/Unmount nur über LTFS-Shell.
- Mountstatus aus `/proc/self/mountinfo`, ohne Tape-Zugriff.
- Freigabe nach Unmount wird ausdrücklich bestätigt; kein Force-/Lazy-Unmount oder automatisches Eject.
- DAR-Erstellung, Slicing und bestehende DAR-Operationen bleiben unverändert.

Noch **nicht** integriert: automatische Bandwechselerkennung, LTFS-Eject-/Laufwerkssteuerung,
LTFS-Formatierung, automatische Geräteauswahl, Hardwareintegration und realer Bandlaufwerkstest.
