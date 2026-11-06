"""Configurazione locale: ``%LOCALAPPDATA%\\WMPRevival\\config.toml`` (fuori dal repository).

Esempio::

    [musicbrainz]
    contact = "indirizzo@esempio.it"   # obbligatorio per l'API pubblica
    # base_url = "http://localhost:5000/ws/2"   # mirror locale
    # min_interval = 0                          # solo con un mirror
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yarl import URL

from wmprevival import __version__

PUBLIC_MB = "https://musicbrainz.org/ws/2"
PUBLIC_MIN_INTERVAL = 1.1  # regola: al massimo 1 richiesta al secondo, con un margine


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MusicBrainzConfig:
    base_url: str = PUBLIC_MB
    contact: str = ""
    min_interval: float = PUBLIC_MIN_INTERVAL

    @property
    def is_public(self) -> bool:
        return URL(self.base_url).host in ("musicbrainz.org", "beta.musicbrainz.org")

    @property
    def user_agent(self) -> str:
        contact = f" ( {self.contact} )" if self.contact else ""
        return f"WMPRevival/{__version__}{contact}"

    def validate(self) -> MusicBrainzConfig:
        if self.is_public:
            if not self.contact.strip():
                raise ConfigError(
                    "l'API pubblica di MusicBrainz vuole un contatto nello User-Agent: "
                    "impostare [musicbrainz] contact in config.toml"
                )
            if self.min_interval < 1.0:
                raise ConfigError("con l'API pubblica min_interval non può scendere sotto 1 secondo")
        return self


@dataclass(frozen=True)
class Config:
    musicbrainz: MusicBrainzConfig

    @classmethod
    def load(cls, path: Path) -> Config:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        mb = data.get("musicbrainz", {})
        unknown = set(mb) - set(MusicBrainzConfig.__dataclass_fields__)
        if unknown:
            raise ConfigError(f"chiavi sconosciute in [musicbrainz]: {', '.join(sorted(unknown))}")
        return cls(musicbrainz=MusicBrainzConfig(**mb))
