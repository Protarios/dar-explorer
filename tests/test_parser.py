#!/usr/bin/env python3
"""Small parser regression test using the DAR listing format."""

from dar_parser import parse_listing

SAMPLE = r"""
[Saved][-] [-L-][  0%][ ]  drwxr-xr-x  burda burda 90 o  Mon Sep 28 23:11:30 2026  Dokumente
[Saved][ ] [-L-][    ][ ]  -rw-r--r-- burda burda 14 o  Mon Sep 28 23:11:30 2026  Dokumente/Projekte/alpha.txt
[Saved][ ] [-L-][    ][ ]  -rw-r--r-- burda burda 13 o  Mon Sep 28 23:11:30 2026  Dokumente/Projekte/beta.txt
[Saved][-] [-L-][  0%][ ]  drwxr-xr-x  burda burda 16 o  Mon Sep 28 23:11:30 2026  Musik
"""

entries = parse_listing(SAMPLE)

assert len(entries) == 4, entries
assert entries[0].path == "Dokumente"
assert entries[0].is_dir
assert entries[1].path.endswith("alpha.txt")
assert not entries[1].is_dir

print("Parser-Test OK:", len(entries), "Einträge")
