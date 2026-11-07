from __future__ import annotations

import pytest

from wmprevival import __version__
from wmprevival.config import PUBLIC_MB, Config, ConfigError, MusicBrainzConfig


def test_defaults_without_file(tmp_path) -> None:
    cfg = Config.load(tmp_path / "missing.toml").musicbrainz
    assert cfg.base_url == PUBLIC_MB and cfg.is_public and cfg.min_interval >= 1.0


def test_public_api_requires_a_contact() -> None:
    with pytest.raises(ConfigError, match="contact"):
        MusicBrainzConfig().validate()


def test_public_api_refuses_a_faster_rate() -> None:
    with pytest.raises(ConfigError, match="1 second"):
        MusicBrainzConfig(contact="x@example.org", min_interval=0.5).validate()


def test_mirror_may_go_fast_and_anonymous() -> None:
    cfg = MusicBrainzConfig(base_url="http://localhost:5000/ws/2", min_interval=0).validate()
    assert not cfg.is_public
    assert cfg.user_agent == f"WMPRevival/{__version__}"


def test_user_agent_follows_the_musicbrainz_format(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[musicbrainz]\ncontact = "x@example.org"\n', encoding="utf-8")
    cfg = Config.load(path).musicbrainz.validate()
    assert cfg.user_agent == f"WMPRevival/{__version__} ( x@example.org )"


def test_unknown_keys_are_errors(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[musicbrainz]\nemail = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="email"):
        Config.load(path)
