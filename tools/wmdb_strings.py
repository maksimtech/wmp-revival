"""Extracts the strings from a WMP library database (*.wmdb) and compares two copies.

The .wmdb format is not ESE (signature a5 09 b6 f3, not ef cd ab 89) and is undocumented:
instead of decoding it, the ASCII and UTF-16LE strings are extracted and a copy made
before a WMP action is compared with one made after.

Usage:
    python -I tools/wmdb_strings.py DB                 # all strings
    python -I tools/wmdb_strings.py BEFORE AFTER       # only those that appeared in AFTER

Strings that look like a TOC (hex numbers separated by '+') or an MDR/MDQ document are
marked with '*'.

Always work on a COPY of the database, with WMP closed: the live file is at
%LOCALAPPDATA%\\Microsoft\\Media Player\\CurrentDatabase_*.wmdb.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ASCII_RE = re.compile(rb"[\x20-\x7e]{6,}")
UTF16_RE = re.compile(rb"(?:[\x20-\x7e\xa0-\xff]\x00){4,}")
TOC_RE = re.compile(r"[0-9A-Fa-f]+(?:\+[0-9A-Fa-f]+){3,}")
INTERESTING = ("MDR", "MDQ", "METADATA", "TOC", "musicmatch", "WMContentID", "trackRequestID")


def extract(data: bytes) -> Counter[str]:
    found: Counter[str] = Counter()
    for m in ASCII_RE.finditer(data):
        found[m.group().decode("ascii")] += 1
    for m in UTF16_RE.finditer(data):
        found[m.group().decode("utf-16le")] += 1
    return found


def mark(s: str) -> str:
    hit = TOC_RE.search(s) or any(k.lower() in s.lower() for k in INTERESTING)
    return ("* " if hit else "  ") + s


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__)
        return 2
    sets = [extract(Path(a).read_bytes()) for a in argv]
    shown = sets[-1] - sets[0] if len(sets) == 2 else sets[0]
    for s in sorted(shown):
        print(mark(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
