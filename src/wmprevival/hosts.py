"""Righe del file hosts che puntano il servizio di WMP su questo PC.

Le funzioni sul testo sono pure; ``apply`` legge, fa il backup e riscrive il file
**sul posto** (non con un file temporaneo rinominato) per non perdere i permessi
che Windows assegna a hosts.
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from wmprevival.paths import HOSTS_MARKER

LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _is_marked(line: str) -> bool:
    return line.rstrip().endswith(HOSTS_MARKER)


def mapped_names(line: str) -> tuple[str, list[str]] | None:
    """Indirizzo e nomi di una riga attiva di hosts; ``None`` per commenti e righe vuote."""
    content = line.split("#", 1)[0].split()
    if len(content) < 2:
        return None
    return content[0], [n.lower() for n in content[1:]]


def remove_entries(text: str) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(line for line in lines if not _is_marked(line))


def add_entries(text: str, hostname: str, addresses: tuple[str, ...] = LOOPBACK_ADDRESSES) -> str:
    """Aggiunge le righe marcate; idempotente (toglie prima quelle già presenti)."""
    nl = _newline(text)
    base = remove_entries(text)
    if base and not base.endswith(("\n", "\r")):
        base += nl
    added = "".join(f"{addr} {hostname}  {HOSTS_MARKER}{nl}" for addr in addresses)
    return base + added


def marked_entries(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _is_marked(line)]


def foreign_entries(text: str, hostname: str) -> list[str]:
    """Righe attive non nostre che mappano lo stesso nome: andrebbero in conflitto."""
    found = []
    for line in text.splitlines():
        parsed = mapped_names(line)
        if parsed and hostname.lower() in parsed[1] and not _is_marked(line):
            found.append(line.strip())
    return found


def read(path: Path) -> str:
    # surrogateescape: un byte strano in hosts sopravvive intatto alla riscrittura.
    return path.read_bytes().decode("utf-8", errors="surrogateescape")


def backup(path: Path, backup_dir: Path, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now()
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"hosts.{now:%Y%m%d-%H%M%S-%f}.bak"
    shutil.copy2(path, dest)
    return dest


def write(path: Path, text: str) -> None:
    with path.open("r+b") as fh:
        fh.write(text.encode("utf-8", errors="surrogateescape"))
        fh.truncate()
