from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shlex


@dataclass
class ArchiveCreateOptions:
    source: str
    archive: str
    reference: str | None = None
    reference_is_catalog: bool = False
    encryption_enabled: bool = False
    encryption_algorithm: str = "aes256"
    compression: str = "gzip"
    compression_level: int = 6
    slice_enabled: bool = False
    slice_size: str = ""
    first_slice_size: str = ""
    empty_dirs: bool = True
    preserve_atime: bool = False
    hard_links: bool = True
    sparse_files: bool = True
    xattrs: bool = True
    acl: bool = True
    numeric_owner: bool = False
    keep_dar_terminology: bool = True
    dry_run: bool = False

    @property
    def backup_type(self) -> str:
        return "differential" if self.reference else "full"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ArchiveCreateOptions":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def normalize_archive_base(path: str) -> str:
    """Convert a DAR slice/catalog-looking filename to the archive basename."""
    p = Path(path).expanduser()
    name = p.name
    import re
    name = re.sub(r"\.\d+\.dar$", "", name)
    name = re.sub(r"\.dar$", "", name)
    return str(p.with_name(name))


def build_create_argv(options: ArchiveCreateOptions, dar_binary: str = "dar") -> list[str]:
    """Build a DAR create command without embedding a secret passphrase.

    Encryption deliberately uses ``algo:`` so DAR asks for the password in the
    real PTY/terminal. This avoids putting passwords into argv/ps output.
    """
    source = str(Path(options.source).expanduser().resolve())
    archive = normalize_archive_base(options.archive)

    if not source:
        raise ValueError("Kein Quellverzeichnis angegeben.")
    if not archive:
        raise ValueError("Kein Archivname angegeben.")

    args = [dar_binary, "-c", archive, "-R", source]

    if options.reference:
        ref = str(Path(options.reference).expanduser().resolve())
        # Ein isolierter Katalog ist selbst die Referenzdatei. Sein exakter
        # .dar.1.dar-Pfad darf nicht wie ein normales Archiv normalisiert
        # werden. Bei einem normalen Archiv wird dagegen die DAR-Basis
        # verwendet, auch wenn der Benutzer eine konkrete Slice-Datei gewählt
        # hat.
        if options.reference_is_catalog:
            args += ["-A", ref]
        else:
            args += ["-A", normalize_archive_base(ref)]

    if options.encryption_enabled:
        algo = options.encryption_algorithm.strip().lower()
        if not algo:
            raise ValueError("Kein Verschlüsselungsverfahren ausgewählt.")
        args += ["-K", f"{algo}:"]

    if options.slice_enabled:
        size = options.slice_size.strip()
        if not size:
            raise ValueError("Slicing ist aktiviert, aber keine Slicegröße angegeben.")
        args += ["-s", size]
        if options.first_slice_size.strip():
            args += ["-S", options.first_slice_size.strip()]

    compression = options.compression.strip().lower()
    if compression and compression not in {"none", "keine", "uncompressed"}:
        level = max(0, min(9, int(options.compression_level)))
        args += ["-z", f"{compression}:{level}"]

    # Diese Metadaten werden von DAR standardmäßig entsprechend seiner
    # Plattform-/Dateisystemunterstützung behandelt. Wir erzeugen hier
    # bewusst nur Optionen, deren Semantik über die unterstützten DAR-
    # Versionen stabil ist. Erweiterte Schalter kommen in einem separaten
    # Advanced-Options-Modul, damit kein GUI-Schalter ungültige CLI-Optionen
    # erzeugt.
    if options.empty_dirs:
        args.append("-D")

    if options.dry_run:
        args.append("-N")

    return args


def shell_command(argv: list[str]) -> str:
    return " ".join(shlex.quote(str(a)) for a in argv)
