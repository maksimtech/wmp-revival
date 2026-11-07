"""Regression of 2026-11-06: three P!nk files all turned into "Long Way to Happy".

Actual sequence (captures and the WMP library database):

1. FAI on 12 files of "I'm Not Dead": MDR of release 2EBD0F3C with trackRequestID 0 =
   "Long Way to Happy" (track 3). Applied by WMP.
2. Automatic lookup of "Who Knew" and of "Stupid Girls", one request each: our responses
   were correct (trackRequestID 0 = the right track), but with the **same WMCollectionID**
   as in 1.
3. WMP keeps a single MDR per WMCollectionID: it updated the old document's header, kept
   its tracks and applied trackRequestID 0 -> "Long Way to Happy".

Fixes: one WMCollectionID for every file response, and search/matching that also uses the
file name when the tag title doesn't fit the length.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.fake_mb import JOY, FixtureMB
from wmprevival import mdq
from wmprevival.mdq import MdqTrack
from wmprevival.service import MetadataService, file_collection_id, filename_title, title_candidates

CAPTURE = Path(__file__).parent / "fixtures" / "captures" / "fai_file_pink_damaged.json"
IM_NOT_DEAD = "2ebd0f3c-758d-465c-b329-09f9905c73d5"


def _track(**kw: object) -> MdqTrack:
    base: dict = {
        "request_id": 0,
        "title": "",
        "artist": "P!nk",
        "album": "",
        "album_artist": "P!nk",
        "number": None,
        "duration_ms": None,
        "filename": "",
    }
    base.update(kw)
    return MdqTrack(**base)


def _ruined_mdq() -> mdq.Mdq:
    rec = json.loads(CAPTURE.read_text(encoding="utf-8"))
    return mdq.parse(dict(rec["request"]["body"]["form"])["mdq"])


def test_file_responses_never_share_a_collection_id() -> None:
    a = file_collection_id(IM_NOT_DEAD, "3305FF32-0000-0000-0000-000000000000")
    b = file_collection_id(IM_NOT_DEAD, "F26A1FC6-0000-0000-0000-000000000000")
    assert a != b
    assert a != IM_NOT_DEAD
    assert a == file_collection_id(IM_NOT_DEAD.upper(), "3305ff32-0000-0000-0000-000000000000".upper())


async def test_two_requests_for_the_same_release_get_different_collection_ids() -> None:
    service = MetadataService(FixtureMB())
    base = _ruined_mdq()
    first = mdq.Mdq(request_id="11111111-1111-1111-1111-111111111111", tracks=base.tracks[:1])
    second = mdq.Mdq(request_id="22222222-2222-2222-2222-222222222222", tracks=base.tracks[2:3])
    ids: list[str] = []
    for query in (first, second):
        xml = await service.file_automatic(query)
        assert xml is not None
        m = ET.fromstring(xml).find("MDR-CD")
        assert m is not None
        assert m.findtext("dataProviderParams") == f"musicbrainz-release={IM_NOT_DEAD}"
        ids.append(m.findtext("WMCollectionID") or "")
    assert ids[0] != ids[1]
    assert IM_NOT_DEAD.upper() not in ids[0] and IM_NOT_DEAD.upper() not in ids[1]


async def test_cd_responses_keep_the_release_id() -> None:
    xml = await MetadataService(FixtureMB()).cd_automatic(JOY)
    assert xml is not None
    assert ET.fromstring(xml).findtext("MDR-CD/WMCollectionID") == "{12C5606B-1B33-4AB3-BEC1-F556972B5F89}"


async def test_ruined_tags_are_repaired_from_the_file_names() -> None:
    fake = FixtureMB()
    query = _ruined_mdq()
    assert [t.title for t in query.tracks[:3]] == ["Long Way to Happy"] * 3  # the damage
    choices = await MetadataService(fake).file_choices(query)
    searches = [c[1] for c in fake.calls if c[0] == "search"]
    assert searches[0].startswith('recording:"Long Way to Happy"')  # tag: length 197 s, rejected
    assert searches[1].startswith('recording:"Stupid Girls"')  # file name: found
    best = choices[0]
    assert best.release_id == IM_NOT_DEAD
    assert best.matches == tuple(range(14))
    m = ET.fromstring(best.mdr).find("MDR-CD")
    assert m is not None
    got = [(t.findtext("trackRequestID"), t.findtext("trackNumber"), t.findtext("trackTitle")) for t in m.iter("track")]
    assert got[:4] == [
        ("0", "1", "Stupid Girls"),
        ("1", "2", "Who Knew"),
        ("2", "3", "Long Way to Happy"),
        ("3", "4", "Nobody Knows"),
    ]
    assert len({n for _r, n, _t in got}) == 14  # no track used twice
    assert m.findtext("WMCollectionID") == "{" + file_collection_id(IM_NOT_DEAD, query.request_id).upper() + "}"


def test_filename_titles() -> None:
    assert filename_title(_track(filename="01. Stupid Girls.flac")) == "Stupid Girls"
    assert filename_title(_track(filename="07. 'Cuz I Can.flac")) == "'Cuz I Can"
    assert filename_title(_track(filename="1-03 Long Way to Happy.mp3")) == "Long Way to Happy"
    assert filename_title(_track(filename="P!nk - Who Knew.wma")) == "Who Knew"
    assert filename_title(_track(filename="03 - P!nk - Who Knew.m4a")) == "Who Knew"
    assert filename_title(_track(filename="Mine All Day [Edit].flac")) == "Mine All Day [Edit]"
    assert filename_title(_track(filename="")) == ""
    # Tag title first, file name only if different.
    assert title_candidates(_track(title="Stupid Girls", filename="01. Stupid Girls.flac")) == ["Stupid Girls"]
    assert title_candidates(_track(title="Long Way to Happy", filename="02. Who Knew.flac")) == [
        "Long Way to Happy",
        "Who Knew",
    ]


async def test_a_track_is_never_given_to_two_files() -> None:
    fake = FixtureMB()
    release = fake.releases[IM_NOT_DEAD]
    same = [
        _track(request_id=i, title="Long Way to Happy", duration_ms=229000, filename=f"0{i}. Long Way to Happy.flac")
        for i in range(3)
    ]
    query = mdq.Mdq(request_id="33333333-3333-3333-3333-333333333333", tracks=tuple(same))
    pairs = MetadataService._match_tracks(release, query, same[0], recording_id="")
    assert len(pairs) == 1  # only one "Long Way to Happy" track: the other files are left out
