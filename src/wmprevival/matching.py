"""Scelta del disco: dal TOC di WMP ai candidati di MusicBrainz, con un punteggio.

Il test zero ha mostrato che il Disc ID esatto spesso non è collegato (2 CD su 3) e che la
ricerca per TOC di MusicBrainz restituisce anche dischi estranei (12 per un CD da 14 tracce).
Per questo ogni candidato si misura confrontando, traccia per traccia, le durate del TOC con
quelle del medium in MusicBrainz:

- ``exact``: il Disc ID è collegato a quel medium: corrispondenza certa;
- altrimenti si accetta solo se tutte le tracce con durata nota distano al massimo
  ``MAX_TRACK_DIFF_MS`` e le durate note coprono almeno metà delle tracce.

Ordinamento deterministico: esatti prima, poi scarto massimo, scarto medio, data, MBID.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wmprevival.toc import Toc

MAX_TRACK_DIFF_MS = 3000


@dataclass(frozen=True)
class Candidate:
    release_id: str
    title: str
    artist: str
    date: str
    country: str
    medium_position: int
    medium_format: str
    exact: bool
    max_diff_ms: int | None
    mean_diff_ms: float | None
    known_lengths: int

    @property
    def accepted(self) -> bool:
        return self.exact or (self.max_diff_ms is not None and self.max_diff_ms <= MAX_TRACK_DIFF_MS)

    def sort_key(self) -> tuple[bool, int, float, str, str]:
        big = 10**9
        return (
            not self.exact,
            self.max_diff_ms if self.max_diff_ms is not None else big,
            self.mean_diff_ms if self.mean_diff_ms is not None else big,
            self.date or "9999",
            self.release_id,
        )


def artist_credit(credits: list[dict[str, Any]]) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits)


def _medium_candidate(toc: Toc, release: dict[str, Any], medium: dict[str, Any], disc_id: str) -> Candidate:
    exact = any(d.get("id") == disc_id for d in medium.get("discs", []))
    diffs = [
        abs(track["length"] - expected)
        for track, expected in zip(medium.get("tracks", []), toc.track_lengths_ms, strict=False)
        if track.get("length") is not None
    ]
    enough = len(diffs) * 2 >= toc.track_count
    return Candidate(
        release_id=release["id"],
        title=release.get("title", ""),
        artist=artist_credit(release.get("artist-credit", [])),
        date=release.get("date", ""),
        country=release.get("country") or "",
        medium_position=medium.get("position", 1),
        medium_format=medium.get("format") or "",
        exact=exact,
        max_diff_ms=max(diffs) if diffs and enough else None,
        mean_diff_ms=sum(diffs) / len(diffs) if diffs and enough else None,
        known_lengths=len(diffs),
    )


def candidates(toc: Toc, releases: list[dict[str, Any]]) -> list[Candidate]:
    """Un candidato per ogni medium con lo stesso numero di tracce del TOC, ordinati."""
    disc_id = toc.disc_id
    found: dict[tuple[str, int], Candidate] = {}
    for release in releases:
        for medium in release.get("media", []):
            if medium.get("track-count") != toc.track_count:
                continue
            cand = _medium_candidate(toc, release, medium, disc_id)
            key = (cand.release_id, cand.medium_position)
            if key not in found or cand.sort_key() < found[key].sort_key():
                found[key] = cand
    return sorted(found.values(), key=Candidate.sort_key)


def is_cd_stub(body: dict[str, Any] | None) -> bool:
    """Una risposta di ``/discid`` che è un CD stub (dati anonimi) e non un elenco di release."""
    return body is not None and "releases" not in body and "tracks" in body
