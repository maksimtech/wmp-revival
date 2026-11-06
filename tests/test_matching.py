"""Scelta del disco sulle risposte vere di MusicBrainz registrate il 2026-11-06."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wmprevival import matching
from wmprevival.toc import Toc

FIXTURES = Path(__file__).parent / "fixtures" / "musicbrainz"
JOY = Toc.from_wmp("C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A")
CD14 = Toc.from_wmp("E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186")


def _body(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["body"]


def test_disc_ids_of_joy_and_cd14_are_not_attached() -> None:
    assert _body("discid_12_tracce_joy.json") is None
    assert _body("discid_14_tracce.json") is None


def test_joy_is_found_by_toc_search_and_confirmed_by_durations() -> None:
    found = matching.candidates(JOY, _body("toc_12_tracce_joy.json")["releases"])
    assert found, "la ricerca per TOC deve trovare Joy"
    best = found[0]
    assert (best.artist, best.title, best.country) == ("Giovanni Allevi", "Joy", "IT")  # confermato dall'utente
    assert best.accepted and not best.exact
    assert best.max_diff_ms is not None and best.max_diff_ms <= matching.MAX_TRACK_DIFF_MS
    assert {c.title for c in found if c.accepted} == {"Joy"}


def test_cd14_noise_is_rejected_by_track_durations() -> None:
    releases = _body("toc_14_tracce.json")["releases"]
    found = matching.candidates(CD14, releases)
    accepted = [c for c in found if c.accepted]
    rejected = [c for c in found if not c.accepted]
    # MusicBrainz propone molti dischi estranei: le durate traccia per traccia li scartano.
    assert len(rejected) >= 8
    assert all(c.max_diff_ms is None or c.max_diff_ms > matching.MAX_TRACK_DIFF_MS for c in rejected)
    assert len(accepted) <= 2
    assert found == sorted(found, key=matching.Candidate.sort_key)


def test_exact_disc_id_wins_over_durations() -> None:
    releases = _body("toc_12_tracce_joy.json")["releases"]
    patched = json.loads(json.dumps(releases))
    target = patched[-1]
    for medium in target["media"]:
        medium["discs"] = [{"id": JOY.disc_id}]
    found = matching.candidates(JOY, patched)
    assert found[0].exact and found[0].release_id == target["id"]


def test_cd_stub_is_recognised() -> None:
    stub = _body("discid_21_tracce_stub.json")
    assert matching.is_cd_stub(stub)
    assert stub["title"] == "Miniclub Compilation" and stub["track-count"] == 21
    assert not matching.is_cd_stub(_body("toc_12_tracce_joy.json"))
    assert not matching.is_cd_stub(None)


def test_media_with_other_track_counts_are_ignored() -> None:
    release = {"id": "r", "title": "t", "media": [{"position": 1, "track-count": 3, "tracks": []}]}
    assert matching.candidates(JOY, [release]) == []
