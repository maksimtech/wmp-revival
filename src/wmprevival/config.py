"""Local configuration: ``%LOCALAPPDATA%\\WMPRevival\\config.toml`` (outside the repository).

Example::

    [musicbrainz]
    contact = "you@example.org"   # required for the public API
    # base_url = "http://localhost:5000/ws/2"   # local mirror
    # min_interval = 0                          # only with a mirror
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yarl import URL

from wmprevival import __version__

PUBLIC_MB = "https://musicbrainz.org/ws/2"
PUBLIC_MIN_INTERVAL = 1.1  # rule: at most 1 request per second, with a margin


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
                    "the public MusicBrainz API wants a contact in the User-Agent: "
                    "set [musicbrainz] contact in config.toml"
                )
            if self.min_interval < 1.0:
                raise ConfigError("with the public API min_interval can't go below 1 second")
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
            raise ConfigError(f"unknown keys in [musicbrainz]: {', '.join(sorted(unknown))}")
        return cls(musicbrainz=MusicBrainzConfig(**mb))
