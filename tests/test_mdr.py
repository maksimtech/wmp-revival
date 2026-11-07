from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from tests.fake_mb import body
from wmprevival import mdr


def _parse(xml: str) -> ET.Element:
    assert xml.startswith('<?xml version="1.0"?>\n')
    xml.encode("ascii")  # ASCII only, as after EncodeOMI
    return ET.fromstring(xml)


def test_joy_cd_album() -> None:
    release = body("release_joy.json")
    album = mdr.cd_album(release, 1)
    xml = mdr.build(album)
    # The namespace declaration of the wmp.dll template (ElementTree doesn't expose it after parsing).
    assert xml.split("\n")[1].startswith(f'<METADATA xmlns:sql="{mdr.SQL_NS}"><MDR-CD><version>4.0</version>')
    root = _parse(xml)
    assert root.tag == "METADATA"
    m = root.find("MDR-CD")
    assert m is not None
    assert m.findtext("version") == "4.0"
    assert m.findtext("WMCollectionID") == "{" + release["id"].upper() + "}"
    assert m.findtext("WMCollectionGroupID") == "{" + release["release-group"]["id"].upper() + "}"
    assert m.findtext("mdqRequestID") == mdr.ZERO_GUID
    assert m.findtext("albumTitle") == "Joy"
    assert m.findtext("albumArtist") == "Giovanni Allevi"
    assert m.findtext("releaseDate") == "2006"
    assert m.findtext("label") == "Ricordi"
    assert m.findtext("genre") == "Classical"  # three genres tied on votes: the first alphabetically
    assert m.findtext("dataProvider") == "MusicBrainz"
    tracks = m.findall("track")
    assert len(tracks) == 12
    assert [t.findtext("trackRequestID") for t in tracks] == [str(i) for i in range(12)]
    assert [t.findtext("trackNumber") for t in tracks] == [str(i) for i in range(1, 13)]
    assert tracks[0].findtext("trackTitle") == "Panic"
    assert tracks[0].findtext("trackPerformer") == "Giovanni Allevi"
    rec_id = release["media"][0]["tracks"][0]["recording"]["id"]
    assert tracks[0].findtext("WMContentID") == "{" + rec_id.upper() + "}"
    # Every element of the wmp.dll template is present, even if empty.
    for tag in ("uniqueFileID", "providerStyle", "largeCoverParams", "smallCoverParams", "validated"):
        assert m.find(tag) is not None
    for tag in ("WMContentID", "uniqueFileID", "trackComposer", "trackConductor", "period"):
        assert tracks[0].find(tag) is not None


def test_composers_come_from_the_performed_work() -> None:
    release = body("release_forever_young_10f85058.json")
    track = release["media"][0]["tracks"][2]
    assert mdr.composers(track["recording"]) == "Marian Gold; Bernhard Lloyd; Frank Mertens"
    assert mdr.genre(release) == "Electronic"
    assert mdr.label(release) == "Warner Music Germany"


def test_non_ascii_and_markup_survive_as_character_references() -> None:
    title = 'Caffè – Sigur Rós & <Amici> "live" l’alba'
    album = mdr.MdrAlbum(
        release_id="r",
        release_group_id="",
        request_id=mdr.ZERO_GUID,
        title=title,
        artist="Björk",
        tracks=(mdr.MdrTrack(request_id=0, recording_id="x", title="Jóga", number=1),),
    )
    xml = mdr.build(album)
    assert "Caff&#232;" in xml and "&#8211;" in xml and "&lt;Amici&gt;" in xml and "&amp;" in xml
    m = _parse(xml).find("MDR-CD")
    assert m is not None
    assert m.findtext("albumTitle") == title
    assert m.findtext("albumArtist") == "Björk"
    assert m.findtext("track/trackTitle") == "Jóga"


def test_invalid_albums_are_refused() -> None:
    empty = mdr.MdrAlbum(release_id="r", release_group_id="", request_id=mdr.ZERO_GUID, title="t", artist="a")
    with pytest.raises(ValueError, match="without tracks"):
        mdr.build(empty)
    zero = mdr.MdrAlbum(
        release_id="r",
        release_group_id="",
        request_id=mdr.ZERO_GUID,
        title="t",
        artist="a",
        tracks=(mdr.MdrTrack(request_id=0, recording_id="x", title="t", number=0),),
    )
    with pytest.raises(ValueError, match="trackNumber"):
        mdr.build(zero)


def test_not_found_has_the_shape_of_the_microsoft_answer() -> None:
    root = _parse(mdr.build_not_found())
    assert root.tag == "METADATA"
    assert root.findtext("Backoff/Time") == "5"
    assert root.findtext("mdqRequestID") == mdr.ZERO_GUID
    assert root.findtext("ResponseCode") == "No match"
