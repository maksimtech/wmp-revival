from __future__ import annotations

import json
from pathlib import Path

import pytest

from wmprevival import mdq

CAPTURE = Path(__file__).parent / "fixtures" / "captures" / "fai_file_2_post_mdq.json"


def _real_mdq() -> str:
    rec = json.loads(CAPTURE.read_text(encoding="utf-8"))
    [(name, value)] = rec["request"]["body"]["form"]
    assert name == "mdq"
    return str(value)


def test_parse_the_real_mdq() -> None:
    query = mdq.parse(_real_mdq())
    assert query.request_id == "00000000-0000-0000-0000-000000000001"
    [track] = query.tracks
    assert track == mdq.MdqTrack(
        request_id=0,
        title="Big in Japan",
        artist="Alphaville",
        album="Forever Young",
        album_artist="Alphaville",
        number=3,
        duration_ms=286000,
        filename="03. Alphaville - Big in Japan.flac",
    )


def test_entity_expansion_is_refused() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        "<METADATA><MDQ-CD><mdqRequestID>&b;</mdqRequestID></MDQ-CD></METADATA>"
    )
    with pytest.raises(mdq.MdqError):
        mdq.parse(bomb)


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        ("<nope>", "invalid"),
        ("<OTHER/>", "MDQ-CD"),
        ("<METADATA><MDQ-CD><mdqRequestID>x</mdqRequestID></MDQ-CD></METADATA>", "GUID"),
        (
            "<METADATA><MDQ-CD><mdqRequestID>00000000-0000-0000-0000-000000000001</mdqRequestID></MDQ-CD></METADATA>",
            "without tracks",
        ),
    ],
)
def test_invalid_mdq(xml: str, message: str) -> None:
    with pytest.raises(mdq.MdqError, match=message):
        mdq.parse(xml)
