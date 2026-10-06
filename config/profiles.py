from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict

from dar_backend.archive import ArchiveCreateOptions


DEFAULT_DIR = Path.home() / ".config" / "dar-explorer"
PROFILE_SUFFIX = ".dargui.json"


def _ensure_dir() -> Path:
    DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DIR


def save_profile(path: str, options: ArchiveCreateOptions) -> None:
    target = Path(path).expanduser()
    if target.suffix != ".json":
        target = target.with_suffix(target.suffix + PROFILE_SUFFIX if target.suffix else PROFILE_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"format": 1, "dar_explorer": options.to_dict()}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_profile(path: str) -> ArchiveCreateOptions:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return ArchiveCreateOptions.from_dict(data.get("dar_explorer", data))


def list_profiles() -> list[Path]:
    directory = _ensure_dir()
    return sorted(directory.glob(f"*{PROFILE_SUFFIX}"))
