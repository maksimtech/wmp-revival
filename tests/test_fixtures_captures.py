"""Le richieste vere di WMP catturate il 2026-11-06 (WMP 12.0.26100.9549 x86, it-IT).

Fissano il formato osservato: se un aggiornamento di WMP lo cambia, le nuove catture
falliranno qui prima che altrove.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "captures"

# TOC salvati da WMP nella libreria durante il test zero (stesso giorno).
TOCS = {
    "getmdrcd_12_tracce_joy.json": "C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A",
    "getmdrcd_14_tracce.json": "E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186",
    "getmdrcd_21_tracce.json": (
        "15+96+3AC4+8111+BAEF+F10C+12F5C+181FC+1B036+1F8B9+22FE3+2662D+299A8+2D446+30978+35545"
        "+3844F+3BCAF+3E2C9+42F56+466AA+49694+4CFA3"
    ),
}


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _raw_params(query: str) -> list[tuple[str, str]]:
    # Niente parse_qsl: trasformerebbe i '+' del TOC in spazi.
    return [tuple(p.split("=", 1)) for p in query.split("&")]  # type: ignore[misc]


@pytest.mark.parametrize("name", sorted(TOCS))
def test_cd_lookup_is_a_get_with_the_toc_in_the_query(name: str) -> None:
    rec = _load(name)
    req = rec["request"]
    assert req["method"] == "GET"
    assert req["path"] == "/cdinfo/GetMDRCD.aspx"
    assert req["body"]["length"] == 0  # niente corpo DAI
    assert _raw_params(req["query_string"]) == [
        ("locale", "410"),  # LCID esadecimale: 0x410 = it-IT
        ("geoid", "76"),  # GEOID esadecimale: 0x76 = 118 = Italia
        ("version", "12.0.26100.9549"),  # versione di SysWOW64\wmp.dll
        ("userlocale", "410"),
        ("CD", TOCS[name]),  # stesso valore salvato in Album.TOC
    ]
    headers = dict(req["headers"])
    assert headers["User-Agent"].startswith("Windows-Media-Player/12.0.26100.")
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert rec["upstream"]["status"] == 200
    assert "omitted" in rec["upstream"]["body"]


def test_fai_on_a_cd_is_an_html_page_requested_by_the_ie7_engine() -> None:
    rec = _load("fai_cd_12_tracce_joy.json")
    req = rec["request"]
    assert req["method"] == "GET" and req["path"] == "/FAI/default.aspx"
    params = _raw_params(req["query_string"])
    assert params[-1] == ("cd", TOCS["getmdrcd_12_tracce_joy.json"])  # 'cd' minuscolo, qui
    assert [k for k, _ in params[:-1]] == ["locale", "geoid", "version", "userlocale"]
    ua = dict(req["headers"])["User-Agent"]
    assert "MSIE 7.0" in ua and "Trident/7.0" in ua  # pagina in modalità documento IE7
    assert dict(rec["upstream"]["headers"])["Content-Type"] == "text/html; charset=UTF-8"


REQUEST_ID = "00000000-0000-0000-0000-000000000001"  # GUID di WMP anonimizzato


def test_fai_on_a_file_starts_with_a_get_carrying_the_request_id() -> None:
    rec = _load("fai_file_1_get_ponte.json")
    req = rec["request"]
    assert req["method"] == "GET" and req["path"] == "/FAI/default.aspx"
    params = _raw_params(req["query_string"])
    assert [k for k, _ in params] == ["locale", "geoid", "version", "userlocale", "requestid"]
    assert params[-1] == ("requestid", REQUEST_ID)  # senza parentesi graffe
    assert rec["upstream"]["status"] == 200  # la pagina ponte (omessa: è di Microsoft)


def test_bridge_posts_the_mdq_back_to_the_same_url() -> None:
    import xml.etree.ElementTree as ET

    rec = _load("fai_file_2_post_mdq.json")
    req = rec["request"]
    first = _load("fai_file_1_get_ponte.json")["request"]
    assert req["method"] == "POST" and req["raw_path"] == first["raw_path"]
    headers = dict(req["headers"])
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert headers["Referer"] == f"https://musicmatch-ssl.xboxlive.com{first['raw_path']}"
    [(name, mdq)] = req["body"]["form"]
    assert name == "mdq"

    root = ET.fromstring(mdq)  # fixture nostra e fidata; il server userà defusedxml
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
    assert track.findtext("trackDuration") == "286000"  # millisecondi (4:46)
    assert track.findtext("trackRequestID") == "0"  # indice della traccia nella richiesta
    assert track.findtext("filename") == "03. Alphaville - Big in Japan.flac"
    assert len((track.findtext("trace") or "").split()) == 30  # impronta audio

    # Il server Microsoft oggi risponde 410 Gone senza corpo: è l'errore che WMP mostra.
    assert rec["upstream"]["status"] == 410
    assert rec["response"] == {"status": 410}


def test_toc_hex_count_matches_offsets() -> None:
    for toc in TOCS.values():
        values = [int(v, 16) for v in toc.split("+")]
        assert values[0] == len(values) - 2  # numero tracce, offset..., lead-out
        assert values[1] == 150  # prima traccia a LBA 0 + 150 di pregap
        assert values[1:] == sorted(values[1:])
    assert urllib.parse.unquote_plus("C+96") == "C 96"  # promemoria: mai unquote_plus sul TOC
