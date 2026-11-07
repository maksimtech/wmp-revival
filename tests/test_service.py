from __future__ import annotations

import xml.etree.ElementTree as ET

from tests.fake_mb import CD14, CD21, JOY, USER_EDITION, FixtureMB
from tests.test_mdq import _real_mdq
from wmprevival import mdq
from wmprevival.service import MetadataService, lucene_phrase, norm


async def test_joy_gives_both_editions_with_full_mdr() -> None:
    service = MetadataService(FixtureMB())
    choices = await service.cd_choices(JOY)
    assert [c.title for c in choices] == ["Joy", "Joy"]
    assert {c.artist for c in choices} == {"Giovanni Allevi"}
    for choice in choices:
        assert choice.matches == tuple(range(12))
        root = ET.fromstring(choice.mdr)
        assert len(root.findall("MDR-CD/track")) == 12
    assert "CD" in choices[0].details and "12 tracks" in choices[0].details


async def test_automatic_lookup_returns_the_first_choice() -> None:
    service = MetadataService(FixtureMB())
    xml = await service.cd_automatic(JOY)
    assert xml is not None and ET.fromstring(xml).findtext("MDR-CD/albumTitle") == "Joy"


async def test_no_choice_without_a_release_or_for_a_cd_stub() -> None:
    # 14-track CD: the accepted candidate is not among the fixtures (release None) -> no choice.
    assert await MetadataService(FixtureMB()).cd_choices(CD14) == []
    # 21-track CD: only a CD stub, which is not used.
    assert await MetadataService(FixtureMB()).cd_automatic(CD21) is None


async def test_file_choices_include_the_users_edition() -> None:
    fake = FixtureMB()
    query = mdq.parse(_real_mdq())
    choices = await MetadataService(fake).file_choices(query)
    ids = [c.release_id for c in choices]
    assert ids[:3] == [
        "f42ad47d-cf1e-4234-99d8-16b05283ecfa",  # digital first: 2007
        "1eae8df6-f17c-45b0-a680-5ccd681c83d8",  # remaster 2019
        USER_EDITION,  # the user's one
    ]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert fake.calls[0] == ("search", 'recording:"Big in Japan" AND artist:"Alphaville" AND release:"Forever Young"')

    users = choices[ids.index(USER_EDITION)]
    assert users.title == "Forever Young" and users.matches == (0,)
    m = ET.fromstring(users.mdr).find("MDR-CD")
    assert m is not None
    assert m.findtext("mdqRequestID") == query.request_id
    assert m.findtext("releaseDate") == "2022-05-20"
    assert m.findtext("label") == "Warner Music Germany"
    [track] = m.findall("track")
    assert track.findtext("trackRequestID") == "0"
    assert track.findtext("trackNumber") == "3"
    assert track.findtext("trackTitle") == "Big in Japan"
    assert track.findtext("WMContentID") == "{0F0E8488-8FE6-41C5-88B5-B8729E093833}"
    assert track.findtext("trackComposer") == "Marian Gold; Bernhard Lloyd; Frank Mertens"


def test_lucene_phrase_and_norm() -> None:
    assert lucene_phrase('AC/DC "live" \\ x') == '"AC/DC \\"live\\" \\\\ x"'
    assert norm("Big In  Japan") == norm("big in japan")
    assert norm("Forever Young: Best of the 80’s") == norm("forever young: best of the 80's")
    assert norm("Sigur Rós") == norm("sigur ros")
