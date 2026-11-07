"""WMP's real requests captured on 2026-11-06 (WMP 12.0.26100.9549 x86, it-IT).

They pin down the observed format: if a WMP update changes it, new captures will fail
here before anywhere else.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "captures"

# TOCs saved by WMP in the library during test zero (same day).
TOCS = {
    "getmdrcd_12_tracks_joy.json": "C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A",
    "getmdrcd_14_tracks.json": "E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186",
    "getmdrcd_21_tracks.json": (
        "15+96+3AC4+8111+BAEF+F10C+12F5C+181FC+1B036+1F8B9+22FE3+2662D+299A8+2D446+30978+35545"
        "+3844F+3BCAF+3E2C9+42F56+466AA+49694+4CFA3"
    ),
}


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _raw_params(query: str) -> list[tuple[str, str]]:
    # No parse_qsl: it would turn the TOC's '+' into spaces.
    return [tuple(p.split("=", 1)) for p in query.split("&")]  # type: ignore[misc]


@pytest.mark.parametrize("name", sorted(TOCS))
def test_cd_lookup_is_a_get_with_the_toc_in_the_query(name: str) -> None:
    rec = _load(name)
    req = rec["request"]
    assert req["method"] == "GET"
    assert req["path"] == "/cdinfo/GetMDRCD.aspx"
    assert req["body"]["length"] == 0  # no DAI body
    assert _raw_params(req["query_string"]) == [
        ("locale", "410"),  # hex LCID: 0x410 = it-IT
        ("geoid", "76"),  # hex GEOID: 0x76 = 118 = Italy
        ("version", "12.0.26100.9549"),  # version of SysWOW64\wmp.dll
        ("userlocale", "410"),
        ("CD", TOCS[name]),  # same value saved in Album.TOC
    ]
    headers = dict(req["headers"])
    assert headers["User-Agent"].startswith("Windows-Media-Player/12.0.26100.")
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert rec["upstream"]["status"] == 200
    assert "omitted" in rec["upstream"]["body"]


def test_fai_on_a_cd_is_an_html_page_requested_by_the_ie7_engine() -> None:
    rec = _load("fai_cd_12_tracks_joy.json")
    req = rec["request"]
    assert req["method"] == "GET" and req["path"] == "/FAI/default.aspx"
    params = _raw_params(req["query_string"])
    assert params[-1] == ("cd", TOCS["getmdrcd_12_tracks_joy.json"])  # lowercase 'cd', here
    assert [k for k, _ in params[:-1]] == ["locale", "geoid", "version", "userlocale"]
    ua = dict(req["headers"])["User-Agent"]
    assert "MSIE 7.0" in ua and "Trident/7.0" in ua  # page in IE7 document mode
    assert dict(rec["upstream"]["headers"])["Content-Type"] == "text/html; charset=UTF-8"


REQUEST_ID = "00000000-0000-0000-0000-000000000001"  # WMP's GUID, anonymized


def test_fai_on_a_file_starts_with_a_get_carrying_the_request_id() -> None:
    rec = _load("fai_file_1_get_bridge.json")
    req = rec["request"]
    assert req["method"] == "GET" and req["path"] == "/FAI/default.aspx"
    params = _raw_params(req["query_string"])
    assert [k for k, _ in params] == ["locale", "geoid", "version", "userlocale", "requestid"]
    assert params[-1] == ("requestid", REQUEST_ID)  # without braces
    assert rec["upstream"]["status"] == 200  # the bridge page (omitted: it's Microsoft's)


def test_bridge_posts_the_mdq_back_to_the_same_url() -> None:
    import xml.etree.ElementTree as ET

    rec = _load("fai_file_2_post_mdq.json")
    req = rec["request"]
    first = _load("fai_file_1_get_bridge.json")["request"]
    assert req["method"] == "POST" and req["raw_path"] == first["raw_path"]
    headers = dict(req["headers"])
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert headers["Referer"] == f"https://musicmatch-ssl.xboxlive.com{first['raw_path']}"
    [(name, mdq)] = req["body"]["form"]
    assert name == "mdq"

    root = ET.fromstring(mdq)  # our own trusted fixture; the server uses defusedxml
    assert root.tag == "METADATA"
    [query] = list(root)
    assert query.tag == "MDQ-CD"
    assert query.findtext("mdqRequestID") == REQUEST_ID
    [track] = query.findall("track")
    assert track.findtext("title/text") == "Big in Japan"
    assert [w.text for w in track.findall("title/word")] == ["Big", "in", "Japan"]
    assert track.findtext("artist/text") == "Alphaville"
    assert track.findtext("album/title/text") == "Forever Young"
    assert track.findtext("album/artist/text") == "Alphaville"
    assert track.findtext("trackNumber") == "3"
    assert track.findtext("trackDuration") == "286000"  # milliseconds (4:46)
    assert track.findtext("trackRequestID") == "0"  # index of the track in the request
    assert track.findtext("filename") == "03. Alphaville - Big in Japan.flac"
    assert len((track.findtext("trace") or "").split()) == 30  # audio fingerprint

    # Today the Microsoft server answers 410 Gone with no body: that's the error WMP shows.
    assert rec["upstream"]["status"] == 410
    assert rec["response"] == {"status": 410}


def test_toc_hex_count_matches_offsets() -> None:
    for toc in TOCS.values():
        values = [int(v, 16) for v in toc.split("+")]
        assert values[0] == len(values) - 2  # track count, offsets..., lead-out
        assert values[1] == 150  # first track at LBA 0 + 150 of pregap
        assert values[1:] == sorted(values[1:])
    assert urllib.parse.unquote_plus("C+96") == "C 96"  # reminder: never unquote_plus on the TOC
