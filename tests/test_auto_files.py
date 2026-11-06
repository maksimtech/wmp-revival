"""Ricerca automatica dei file della libreria (POST a GetMDRCD), catturata nella prima prova
con il nostro server il 2026-11-06: quattro FLAC di "Mine All Day" (PewDiePie & Party in
Backyard), uno dei quali è la versione [Edit] che in quell'edizione non esiste."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from tests.fake_mb import FixtureMB
from wmprevival import mdq, recorder
from wmprevival.server import MetadataResponder
from wmprevival.service import MetadataService, norm_title, strip_store_markers

CAPTURE = Path(__file__).parent / "fixtures" / "captures" / "auto_file_post_mdq.json"
REQUEST_ID = "00000000-0000-0000-0000-000000000001"


def _capture() -> dict:
    data: dict = json.loads(CAPTURE.read_text(encoding="utf-8"))
    return data


def test_the_automatic_file_lookup_is_a_post_with_xml() -> None:
    req = _capture()["request"]
    assert req["method"] == "POST" and req["path"] == "/cdinfo/GetMDRCD.aspx"
    assert req["query_string"].endswith(f"&requestID={REQUEST_ID}")  # 'requestID', non 'requestid'
    headers = dict(req["headers"])
    assert headers["Content-Type"] == "text/xml"
    assert headers["User-Agent"].startswith("Windows-Media-Player/")
    query = ET.fromstring(req["body"]["text"]).find("MDQ-CD")
    assert query is not None
    assert [c.tag for c in query] == ["mdqRequestID", "album", "track", "track", "track", "track"]


def test_shared_album_is_applied_to_every_track() -> None:
    query = mdq.parse(_capture()["request"]["body"]["text"])
    assert query.request_id == REQUEST_ID
    assert [t.request_id for t in query.tracks] == [0, 3, 1, 2]
    assert {t.album for t in query.tracks} == {"Mine All Day [Explicit]"}
    assert {t.album_artist for t in query.tracks} == {"PewDiePie & Party in Backyard"}
    assert [t.duration_ms for t in query.tracks] == [250000, 119000, 250000, 250000]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Mine All Day [Explicit]", "Mine All Day"),
        ("Mine All Day (Instrumental) [Explicit]", "Mine All Day (Instrumental)"),
        ("Song (Clean Version)", "Song"),
        ("Mine All Day [Edit]", "Mine All Day [Edit]"),  # un'altra versione: resta
        ("Explicit Content", "Explicit Content"),  # parola nel titolo, non marcatore
    ],
)
def test_store_markers(raw: str, clean: str) -> None:
    assert strip_store_markers(raw) == clean
    assert norm_title(raw) == norm_title(clean)


async def test_automatic_mdr_matches_three_files_and_skips_the_edit() -> None:
    fake = FixtureMB()
    query = mdq.parse(_capture()["request"]["body"]["text"])
    xml = await MetadataService(fake).file_automatic(query)
    assert xml is not None
    assert fake.calls[0] == (
        "search",
        'recording:"Mine All Day" AND artist:"PewDiePie & Party in Backyard" AND release:"Mine All Day"',
    )
    m = ET.fromstring(xml).find("MDR-CD")
    assert m is not None
    assert m.findtext("mdqRequestID") == REQUEST_ID
    assert m.findtext("albumTitle") == "Mine All Day"
    assert m.findtext("label") == ""  # "[no label]" di MusicBrainz non va scritto
    tracks = [
        (t.findtext("trackRequestID"), t.findtext("trackNumber"), t.findtext("trackTitle")) for t in m.iter("track")
    ]
    assert tracks == [
        ("0", "1", "Mine All Day"),
        ("1", "2", "Mine All Day (Instrumental)"),
        ("2", "3", "Mine All Day (Acapella)"),
    ]


async def test_automatic_file_lookup_over_http(tmp_path) -> None:
    body = _capture()["request"]["body"]["text"].encode("utf-8")
    url_path = _capture()["request"]["raw_path"]
    for auto, expected in ((True, "Mine All Day"), (False, None)):
        rec = recorder.Recorder(tmp_path / str(auto), responder=MetadataResponder(MetadataService(FixtureMB()), auto))
        runner = web.AppRunner(recorder.build_app(rec))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        url = URL(f"http://127.0.0.1:{runner.addresses[0][1]}{url_path}", encoded=True)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=body, headers={"Content-Type": "text/xml"}) as r:
                    assert r.status == 200
                    root = ET.fromstring(await r.text())
                async with s.post(url, data=b"<not-xml", headers={"Content-Type": "text/xml"}) as r:
                    assert r.status == 200
                    bad = ET.fromstring(await r.text())
        finally:
            await runner.cleanup()
        if expected:
            assert root.findtext("MDR-CD/albumTitle") == expected
            assert bad.findtext("ResponseCode") == "Invalid MDQ"
            assert bad.findtext("mdqRequestID") == REQUEST_ID
        else:
            assert root.findtext("ResponseCode") == "Automatic lookup disabled"
            assert root.findtext("mdqRequestID") == REQUEST_ID
