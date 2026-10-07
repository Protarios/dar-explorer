from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path


def dar_binary() -> str:
    return shutil.which("dar") or "/usr/local/bin/dar"


def dar_xform_binary() -> str:
    return shutil.which("dar_xform") or "/usr/local/bin/dar_xform"


def archive_base(path: str) -> str:
    p = Path(path).expanduser()
    name = p.name
    name = re.sub(r"\.\d+\.dar$", "", name)
    name = re.sub(r"\.dar$", "", name)
    return str(p.parent / name)


def command_compare(archive: str, filesystem_root: str, isolated_catalog: str | None = None) -> list[str]:
    cmd = [dar_binary(), "-d", archive_base(archive), "-R", str(Path(filesystem_root).expanduser())]
    if isolated_catalog:
        cmd += ["-A", str(Path(isolated_catalog).expanduser())]
    return cmd


def command_isolate(source_archive: str, destination: str, *, password: str | None = None,
                    slice_size: int | None = None, first_slice_size: int | None = None) -> list[str]:
    cmd = [dar_binary(), "-C", archive_base(destination), "-A", archive_base(source_archive)]
    if password:
        cmd += ["-J", password]
    if slice_size:
        cmd += ["-s", str(slice_size)]
    if first_slice_size:
        cmd += ["-S", str(first_slice_size)]
    return cmd


def command_repair(source_archive: str, destination: str, *, password: str | None = None,
                   slice_size: int | None = None, first_slice_size: int | None = None) -> list[str]:
    # DAR repair (-y) reads the interrupted source from -A and writes the repaired archive
    # under the command argument.  Compression cannot be changed during repair.
    cmd = [dar_binary(), "-y", archive_base(destination), "-A", archive_base(source_archive)]
    if password:
        cmd += ["-J", password]
    if slice_size:
        cmd += ["-s", str(slice_size)]
    if first_slice_size:
        cmd += ["-S", str(first_slice_size)]
    return cmd


def command_xform(source_archive: str, destination: str, slice_size: int, *, first_slice_size: int | None = None) -> list[str]:
    cmd = [dar_xform_binary(), "-s", str(slice_size)]
    if first_slice_size:
        cmd += ["-S", str(first_slice_size)]
    cmd += [archive_base(source_archive), archive_base(destination)]
    return cmd


def command_test(archive: str, isolated_catalog: str | None = None) -> list[str]:
    cmd = [dar_binary(), "-t", archive_base(archive)]
    if isolated_catalog:
        cmd += ["-A", str(Path(isolated_catalog).expanduser())]
    return cmd


def shell_command(args: list[str]) -> str:
    return shlex.join([str(x) for x in args])
