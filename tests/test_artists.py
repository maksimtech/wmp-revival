"""Artisti con valori multipli: "Arrival" degli ABBA non veniva trovato (2026-11-06).

Il tag artista dei FLAC conteneva due valori, che WMP unisce in "ABBA, Abba"; la ricerca
artist:"ABBA, Abba" su MusicBrainz non dà risultati.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.fake_mb import FixtureMB
from wmprevival import mdq
from wmprevival.mdq import MdqTrack
from wmprevival.service import MetadataService, artist_candidates, collapse_artist

CAPTURE = Path(__file__).parent / "fixtures" / "captures" / "auto_file_abba_arrival.json"


@pytest.mark.parametrize(
    ("raw", "collapsed"),
    [
        ("ABBA, Abba", "ABBA"),
        ("ABBA; ABBA", "ABBA"),
        ("Sigur Rós / Sigur Ros", "Sigur Rós"),
        ("Earth, Wind & Fire", "Earth, Wind & Fire"),  # artista vero con la virgola
        ("Simon & Garfunkel", "Simon & Garfunkel"),
        ("Queen, David Bowie", "Queen, David Bowie"),  # due artisti diversi: si lascia
        ("", ""),
    ],
)
def test_collapse_artist(raw: str, collapsed: str) -> None:
    assert collapse_artist(raw) == collapsed


def _track(artist: str, album_artist: str) -> MdqTrack:
    return MdqTrack(0, "t", artist, "a", album_artist, None, None, "")


def test_artist_candidates_order_and_various_artists() -> None:
    assert artist_candidates(_track("ABBA, Abba", "ABBA, Abba")) == ["ABBA, Abba", "ABBA"]
    assert artist_candidates(_track("ABBA, Abba", "ABBA")) == ["ABBA, Abba", "ABBA"]
    assert artist_candidates(_track("Queen", "Various Artists")) == ["Queen"]
    assert artist_candidates(_track("", "P!nk")) == ["P!nk"]
    assert artist_candidates(_track("", "")) == [""]


async def test_arrival_is_found_with_the_collapsed_artist() -> None:
    rec = json.loads(CAPTURE.read_text(encoding="utf-8"))
    query = mdq.parse(rec["request"]["body"]["text"])
    assert query.tracks[0].artist == "ABBA, Abba"
    fake = FixtureMB()
    xml = await MetadataService(fake).file_automatic(query)
    assert [c[1] for c in fake.calls if c[0] == "search"] == [
        'recording:"Arrival" AND artist:"ABBA, Abba" AND release:"Arrival"',  # zero risultati
        'recording:"Arrival" AND artist:"ABBA" AND release:"Arrival"',
    ]
    assert xml is not None
    m = ET.fromstring(xml).find("MDR-CD")
    assert m is not None
    assert (m.findtext("albumTitle"), m.findtext("albumArtist")) == ("Arrival", "ABBA")
    # L'edizione che l'utente ha confermato come sua.
    assert m.findtext("dataProviderParams") == "musicbrainz-release=f121c662-eaa3-4b58-836f-c20eea68d78a"
    [track] = m.findall("track")
    assert (track.findtext("trackRequestID"), track.findtext("trackNumber"), track.findtext("trackTitle")) == (
        "0",
        "10",
        "Arrival",
    )
    assert track.findtext("trackComposer") == "Benny Andersson; Björn Ulvaeus"
