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
from wmprevival.service import (
    MetadataService,
    artist_candidates,
    collapse_artist,
    filename_guess,
    filename_number,
    tag_is_suspect,
)

CAPTURE = Path(__file__).parent / "fixtures" / "captures" / "auto_file_abba_arrival.json"
# Dopo il guasto: libreria di WMP con "Panic" / Giovanni Allevi / Joy, file ancora "...Arrival.flac".
CAPTURE_AS_PANIC = Path(__file__).parent / "fixtures" / "captures" / "fai_file_abba_come_panic.json"
USER_EDITION = "f121c662-eaa3-4b58-836f-c20eea68d78a"


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


def _panic_mdq() -> mdq.Mdq:
    rec = json.loads(CAPTURE_AS_PANIC.read_text(encoding="utf-8"))
    return mdq.parse(dict(rec["request"]["body"]["form"])["mdq"])


def _named(filename: str, artist: str = "Giovanni Allevi", title: str = "Panic") -> MdqTrack:
    return MdqTrack(0, title, artist, "Joy", artist, 1, 180000, filename)


@pytest.mark.parametrize(
    ("filename", "guess", "number"),
    [
        ("10. ABBA, Abba - Arrival.flac", ("ABBA, Abba", "Arrival"), 10),
        ("01. Stupid Girls.flac", ("", "Stupid Girls"), 1),
        ("Giovanni Allevi - Panic.flac", ("", "Panic"), None),  # prefisso = artista del tag
        ("1-03 Long Way to Happy.mp3", ("", "Long Way to Happy"), 3),
        ("Mine All Day [Edit].flac", ("", "Mine All Day [Edit]"), None),
    ],
)
def test_filename_guess(filename: str, guess: tuple[str, str], number: int | None) -> None:
    track = _named(filename)
    assert filename_guess(track) == guess
    assert filename_number(track) == number


def test_suspect_only_when_the_file_name_names_another_artist_and_title() -> None:
    assert tag_is_suspect(_named("10. ABBA, Abba - Arrival.flac"))
    assert not tag_is_suspect(_named("01. Panic.flac"))  # nessun artista nel nome
    assert not tag_is_suspect(_named("Giovanni Allevi - Panic.flac"))
    assert not tag_is_suspect(_named("ABBA - Arrival.flac", artist="ABBA", title="Arrival"))
    # Titoli con " - " nel nome ma tag coerente: stesso titolo, quindi non sospetto.
    assert not tag_is_suspect(_named("Peer Gynt - Morning.flac", artist="Grieg", title="Morning"))


async def test_the_damaged_arrival_is_found_from_its_file_name() -> None:
    query = _panic_mdq()
    [track] = query.tracks
    assert (track.title, track.artist, track.album) == ("Panic", "Giovanni Allevi", "Joy")  # il danno
    assert track.filename == "10. ABBA, Abba - Arrival.flac"
    fake = FixtureMB()
    choices = await MetadataService(fake).file_choices(query)
    assert [c[1] for c in fake.calls if c[0] == "search"][:2] == [
        'recording:"Arrival" AND artist:"ABBA, Abba"',  # niente album: anche quello del tag è sospetto
        'recording:"Arrival" AND artist:"ABBA"',
    ]
    assert choices and choices[0].title == "Arrival" and choices[0].artist == "ABBA"
    m = ET.fromstring(choices[0].mdr).find("MDR-CD")
    assert m is not None
    assert m.findtext("dataProviderParams") == f"musicbrainz-release={USER_EDITION}"
    assert (m.findtext("track/trackNumber"), m.findtext("track/trackTitle")) == ("10", "Arrival")


async def test_no_automatic_write_when_the_tag_is_suspect() -> None:
    fake = FixtureMB()
    assert await MetadataService(fake).file_automatic(_panic_mdq()) is None
    assert fake.calls == []  # nemmeno una richiesta a MusicBrainz
