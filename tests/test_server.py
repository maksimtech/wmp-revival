"""Il nostro server dal punto di vista di WMP: stesse richieste delle catture, nostre risposte."""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from tests.fake_mb import JOY, USER_EDITION, FailingMB, FixtureMB
from tests.test_mdq import _real_mdq
from wmprevival import recorder
from wmprevival.server import MetadataResponder, raw_query
from wmprevival.service import MetadataService

QUERY = "locale=410&geoid=76&version=12.0.26100.9549&userlocale=410"
IE7 = "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 10.0; WOW64; Trident/7.0; .NET4.0C; .NET4.0E)"


async def _serve(tmp_path: Path, mb: FixtureMB):
    rec = recorder.Recorder(tmp_path / "cap", responder=MetadataResponder(MetadataService(mb)))
    runner = web.AppRunner(recorder.build_app(rec))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


@pytest.fixture
async def server(tmp_path):
    runner, base = await _serve(tmp_path, FixtureMB())
    yield base, tmp_path / "cap"
    await runner.cleanup()


async def _get(url: str, **kwargs) -> tuple[int, str, str]:
    async with aiohttp.ClientSession() as s, s.get(URL(url, encoded=True), **kwargs) as r:
        return r.status, r.headers.get("Content-Type", ""), await r.text()


def _js_array(html: str, name: str) -> list:
    match = re.search(rf"var {name} = (.*);", html)
    assert match, name
    values: list = json.loads(match.group(1))
    return values


async def test_automatic_cd_lookup_returns_an_mdr(server) -> None:
    base, captures = server
    status, ctype, text = await _get(f"{base}/cdinfo/GetMDRCD.aspx?{QUERY}&CD={JOY.to_wmp()}")
    assert status == 200 and ctype.startswith("text/xml")
    root = ET.fromstring(text)
    assert root.findtext("MDR-CD/albumTitle") == "Joy"
    assert len(root.findall("MDR-CD/track")) == 12
    [cap] = [json.loads(p.read_text(encoding="utf-8")) for p in captures.glob("*.json")]
    assert cap["served_locally"] and "MDR-CD" in cap["served"]["body"]["text"]


async def test_unknown_cd_gets_the_no_data_answer_not_an_error(server) -> None:
    base, _ = server
    status, _ctype, text = await _get(f"{base}/cdinfo/GetMDRCD.aspx?{QUERY}&CD=2+96+2000+4000")
    assert status == 200 and ET.fromstring(text).findtext("ResponseCode") == "No match"
    status, _ctype, text = await _get(f"{base}/cdinfo/GetMDRCD.aspx?{QUERY}&CD=garbage")
    assert status == 200 and ET.fromstring(text).findtext("ResponseCode") == "Invalid TOC"


async def test_fai_on_a_cd_lists_the_editions(server) -> None:
    base, _ = server
    status, ctype, html = await _get(f"{base}/FAI/default.aspx?{QUERY}&cd={JOY.to_wmp()}", headers={"User-Agent": IE7})
    assert status == 200 and ctype.startswith("text/html")
    assert 'id="album-0"' in html and 'id="album-1"' in html and 'id="track-11"' in html
    assert 'onclick="select(0)"' in html
    mdrcds = _js_array(html, "mdrcds")
    assert len(mdrcds) == 2 and all(ET.fromstring(x).findtext("MDR-CD/albumTitle") == "Joy" for x in mdrcds)
    assert _js_array(html, "matches") == [list(range(12))] * 2
    assert '<script type="text/javascript" src="suggestions.js">' in html


async def test_file_flow_bridge_then_results(server) -> None:
    base, _ = server
    request_id = "5FC5C9AD-67FD-4592-8C0D-8DE4A20F2DAF"
    url = f"{base}/FAI/default.aspx?{QUERY}&requestid={request_id}"
    status, _ctype, bridge = await _get(url)
    assert status == 200
    assert f'var requestId = "{request_id}";' in bridge
    assert "window.external.GetMDQByRequestID(requestId)" in bridge
    assert '<form id="bridge" method="post">' in bridge

    mdq_xml = _real_mdq()
    async with (
        aiohttp.ClientSession() as s,
        s.post(URL(url, encoded=True), data={"mdq": mdq_xml}, headers={"User-Agent": IE7}) as r,
    ):
        assert r.status == 200
        html = await r.text()
    assert "Forever Young" in html and "Big in Japan" in html
    mdrcds = _js_array(html, "mdrcds")
    assert len(mdrcds) == 5
    users = next(x for x in mdrcds if USER_EDITION.upper() in x)
    root = ET.fromstring(users)
    assert root.findtext("MDR-CD/mdqRequestID") == "00000000-0000-0000-0000-000000000001"
    assert root.findtext("MDR-CD/track/trackRequestID") == "0"
    assert _js_array(html, "matches") == [[0]] * 5


async def test_bridge_refuses_anything_but_a_guid(server) -> None:
    base, _ = server
    evil = urllib.parse.quote('");alert(1);//')
    status, _ctype, text = await _get(f"{base}/FAI/default.aspx?{QUERY}&requestid={evil}")
    assert status == 404 and "alert" not in text


async def test_static_files_and_log(server) -> None:
    base, _ = server
    status, ctype, js = await _get(f"{base}/FAI/suggestions.js")
    assert status == 200 and "javascript" in ctype
    assert 'window.external.WriteNamesEx(2, "", mdrcds[chosen], true)' in js
    assert "window.external.ReturnToMainTask()" in js
    status, ctype, css = await _get(f"{base}/FAI/style.css")
    assert status == 200 and ctype.startswith("text/css") and ".album" in css
    status, _ctype, _ = await _get(f"{base}/FAI/log?kind=WriteNamesEx-error&detail=0x80004005")
    assert status == 204
    status, _ctype, _ = await _get(f"{base}/altro")
    assert status == 404


async def test_hostile_metadata_cannot_escape_the_page(tmp_path) -> None:
    """Regola 8: un titolo o un artista ostili da MusicBrainz restano testo."""
    mb = FixtureMB()
    for release in mb.releases.values():
        if release["title"] == "Joy":
            release["title"] = "</script><script>alert(1)</script>"
            release["artist-credit"] = [{"name": '"onmouseover="alert(2)" x="', "joinphrase": ""}]
    runner, base = await _serve(tmp_path, mb)
    try:
        _status, _ctype, html = await _get(f"{base}/FAI/default.aspx?{QUERY}&cd={JOY.to_wmp()}")
    finally:
        await runner.cleanup()
    assert "<script>alert(1)" not in html
    assert "</script><script>" not in html
    assert '"onmouseover="' not in html and 'onmouseover="alert' not in html
    assert "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html  # nell'HTML, come testo
    mdrcds = _js_array(html, "mdrcds")  # nello script: JSON con <, e XML valido
    assert ET.fromstring(mdrcds[0]).findtext("MDR-CD/albumTitle") == "</script><script>alert(1)</script>"


async def test_musicbrainz_down_never_gives_5xx(tmp_path) -> None:
    runner, base = await _serve(tmp_path, FailingMB())
    try:
        status, _ctype, text = await _get(f"{base}/cdinfo/GetMDRCD.aspx?{QUERY}&CD={JOY.to_wmp()}")
        assert status == 200 and ET.fromstring(text).findtext("ResponseCode") == "Lookup failed"
        status, _ctype, html = await _get(f"{base}/FAI/default.aspx?{QUERY}&cd={JOY.to_wmp()}")
        assert status == 200 and "MusicBrainz non risponde" in html
        url = URL(f"{base}/FAI/default.aspx?{QUERY}&requestid=5FC5C9AD-67FD-4592-8C0D-8DE4A20F2DAF", encoded=True)
        async with aiohttp.ClientSession() as s, s.post(url, data={"mdq": _real_mdq()}) as r:
            assert r.status == 200 and "MusicBrainz non risponde" in await r.text()
    finally:
        await runner.cleanup()


async def test_no_auto_answers_no_data_to_the_automatic_lookup(tmp_path) -> None:
    mb = FixtureMB()
    rec = recorder.Recorder(tmp_path / "cap", responder=MetadataResponder(MetadataService(mb), auto=False))
    runner = web.AppRunner(recorder.build_app(rec))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        _status, _ctype, text = await _get(f"{base}/cdinfo/GetMDRCD.aspx?{QUERY}&CD={JOY.to_wmp()}")
        assert ET.fromstring(text).findtext("ResponseCode") == "Automatic lookup disabled"
        assert mb.calls == []  # nessuna richiesta a MusicBrainz
        _status, _ctype, html = await _get(f"{base}/FAI/default.aspx?{QUERY}&cd={JOY.to_wmp()}")
        assert 'id="album-0"' in html  # la scelta manuale funziona
    finally:
        await runner.cleanup()


def test_raw_query_keeps_plus_signs() -> None:
    params = raw_query(f"/FAI/default.aspx?{QUERY}&cd=C+96+5370&X=%41")
    assert params["cd"] == "C+96+5370" and params["x"] == "A" and params["locale"] == "410"


def test_the_fixture_mb_is_not_shared_between_tests() -> None:
    a, b = FixtureMB(), FixtureMB()
    assert a.releases is not b.releases
    copy.deepcopy(a.releases)
