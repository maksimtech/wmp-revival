"""Shared paths and constants.

All local data (PKI, captures, state, hosts backups) lives outside the repository, in
``%LOCALAPPDATA%\\WMPRevival``. The ``WMPREVIVAL_HOME`` and ``WMPREVIVAL_HOSTS``
environment variables are for tests and for trying things without touching the system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVICE_HOST = "musicmatch-ssl.xboxlive.com"
"""The only name WMP asks for metadata (from ``wmploc.dll.mui``)."""

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

    @property
    def config_file(self) -> Path:
        return self.home / "config.toml"

    @property
    def mb_cache(self) -> Path:
        return self.home / "cache" / "musicbrainz.sqlite"
