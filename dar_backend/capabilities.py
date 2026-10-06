from __future__ import annotations

import re
import shutil
import subprocess


KNOWN_CIPHERS = ["aes256", "camellia256", "serpent256", "twofish", "blowfish", "scrambling"]
KNOWN_COMPRESSORS = ["gzip", "bzip2", "xz", "lzo", "zstd"]


def dar_version(dar_binary: str | None = None) -> str:
    binary = dar_binary or shutil.which("dar") or "/usr/local/bin/dar"
    try:
        p = subprocess.run([binary, "-V"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
        return (p.stdout or "").splitlines()[0].strip()
    except Exception:
        return "unbekannt"


def supported_algorithms(dar_binary: str | None = None) -> tuple[list[str], list[str]]:
    """Return conservative UI choices; DAR validates the final command."""
    return list(KNOWN_CIPHERS), list(KNOWN_COMPRESSORS)
