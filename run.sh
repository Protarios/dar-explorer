#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v dar >/dev/null 2>&1; then
    echo "Fehler: 'dar' wurde nicht im PATH gefunden." >&2
    echo "Prüfe die DAR-Installation mit: dar -V" >&2
    exit 1
fi

if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1; then
    cat >&2 <<'EOF'
Fehler: PyGObject/GTK4 ist nicht verfügbar.

Auf CachyOS/Arch Linux installieren:
    sudo pacman -S gtk4 python-gobject

Danach dieses Skript erneut starten.
EOF
    exit 1
fi

exec python3 "$SCRIPT_DIR/dar_explorer.py" "$@"
