"""Fake MusicBrainz for the tests, fed with the real responses recorded in fixtures/musicbrainz."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wmprevival.toc import Toc

FIXTURES = Path(__file__).parent / "fixtures" / "musicbrainz"

JOY = Toc.from_wmp("C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A")
CD14 = Toc.from_wmp("E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186")
CD21 = Toc.from_wmp(
    "15+96+3AC4+8111+BAEF+F10C+12F5C+181FC+1B036+1F8B9+22FE3+2662D+299A8+2D446+30978+35545"
    "+3844F+3BCAF+3E2C9+42F56+466AA+49694+4CFA3"
)
USER_EDITION = "10f85058-4ff3-4e50-bb3e-28c3b4279217"  # the user's Forever Young (Digital Media)


def body(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["body"]


class FixtureMB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.releases: dict[str, Any] = {}
        for path in FIXTURES.glob("release_*.json"):
            data = body(path.name)
            self.releases[data["id"]] = data
        self.discids = {
            JOY.disc_id: body("discid_12_tracks_joy.json"),
            CD14.disc_id: body("discid_14_tracks.json"),
            CD21.disc_id: body("discid_21_tracks_stub.json"),
        }
        self.tocs = {
            JOY.mb_toc: body("toc_12_tracks_joy.json")["releases"],
            CD14.mb_toc: body("toc_14_tracks.json")["releases"],
        }

    async def lookup_discid(self, disc_id: str) -> Any | None:
        self.calls.append(("discid", disc_id))
        return self.discids.get(disc_id)

    async def search_toc(self, toc: Toc) -> list[dict[str, Any]]:
        self.calls.append(("toc", toc.mb_toc))
        return list(self.tocs.get(toc.mb_toc, []))

    async def release(self, mbid: str) -> Any | None:
        self.calls.append(("release", mbid))
        return self.releases.get(mbid)

    EXACT_QUERIES = {
        'recording:"Arrival" AND artist:"ABBA"': "search_arrival_abba_no_album.json",
    }

    async def search_recordings(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.calls.append(("search", query))
        if query in self.EXACT_QUERIES:
            exact: list[dict[str, Any]] = body(self.EXACT_QUERIES[query])["recordings"]
            return exact
        for needle, name in (
            ("Big in Japan", "search_big_in_japan.json"),
            ("Mine All Day", "search_mine_all_day.json"),
            ('recording:"Long Way to Happy"', "search_long_way_to_happy.json"),
            ('recording:"Stupid Girls"', "search_stupid_girls.json"),
            ('artist:"ABBA, Abba"', "search_arrival_abba_doubled.json"),  # zero results, as live
            ('recording:"Arrival" AND artist:"ABBA"', "search_arrival_abba.json"),
        ):
            if needle in query:
                recordings: list[dict[str, Any]] = body(name)["recordings"]
                return recordings
        return []


class FailingMB(FixtureMB):
    async def lookup_discid(self, disc_id: str) -> Any | None:
        raise OSError("MusicBrainz unreachable")

    async def search_recordings(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise OSError("MusicBrainz unreachable")
