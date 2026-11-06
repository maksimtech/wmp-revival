"""Percorsi e costanti condivise.

Tutti i dati locali (PKI, catture, stato, backup di hosts) stanno fuori dal repository,
in ``%LOCALAPPDATA%\\WMPRevival``. Le variabili d'ambiente ``WMPREVIVAL_HOME`` e
``WMPREVIVAL_HOSTS`` servono ai test e a chi vuole provare senza toccare il sistema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVICE_HOST = "musicmatch-ssl.xboxlive.com"
"""L'unico nome a cui WMP chiede i metadati (da ``wmploc.dll.mui``)."""

HOSTS_MARKER = "# wmprevival"


def _default_home() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "WMPRevival"
    return Path.home() / ".local" / "share" / "wmprevival"


def _default_hosts() -> Path:
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return Path(root) / "System32" / "drivers" / "etc" / "hosts"


@dataclass(frozen=True)
class Paths:
    home: Path
    hosts_file: Path

    @classmethod
    def from_env(cls) -> Paths:
        home = os.environ.get("WMPREVIVAL_HOME")
        hosts = os.environ.get("WMPREVIVAL_HOSTS")
        return cls(
            home=Path(home) if home else _default_home(),
            hosts_file=Path(hosts) if hosts else _default_hosts(),
        )

    @property
    def pki(self) -> Path:
        return self.home / "pki"

    @property
    def captures(self) -> Path:
        return self.home / "captures"

    @property
    def backups(self) -> Path:
        return self.home / "backups"

    @property
    def state_file(self) -> Path:
        return self.home / "state.json"
