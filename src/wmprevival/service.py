"""Dalle richieste di WMP alle scelte da proporre: CD (TOC) e file (MDQ).

Ogni scelta porta l'MDR-CD già pronto e gli indici delle tracce richieste che copre
(``matches`` nella pagina dei risultati). Una release completa costa una richiesta a
MusicBrainz: le scelte sono limitate a ``max_choices``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from wmprevival import matching, mdr
from wmprevival.mdq import Mdq, MdqTrack
from wmprevival.toc import Toc

MAX_FILE_LENGTH_DIFF_MS = 5000


class MusicBrainzSource(Protocol):
    async def lookup_discid(self, disc_id: str) -> Any | None: ...
    async def search_toc(self, toc: Toc) -> list[dict[str, Any]]: ...
    async def release(self, mbid: str) -> Any | None: ...
    async def search_recordings(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Choice:
    title: str
    artist: str
    details: str
    mdr: str
    matches: tuple[int, ...]
    release_id: str


def norm(text: str) -> str:
    """Confronto tollerante: maiuscole, accenti, apostrofi tipografici, spazi."""
    text = unicodedata.normalize("NFKD", text).replace("’", "'").replace("‘", "'")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


def _details(release: dict[str, Any], med: dict[str, Any] | None = None) -> str:
    parts = [release.get("date") or "", release.get("country") or ""]
    if med is not None:
        parts += [med.get("format") or "", f"{med.get('track-count', '?')} tracce"]
    if release.get("disambiguation"):
        parts.append(release["disambiguation"])
    return " · ".join(p for p in parts if p)


def lucene_phrase(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MetadataService:
    def __init__(self, mb: MusicBrainzSource, max_choices: int = 3, max_file_choices: int = 5) -> None:
        self.mb = mb
        self.max_choices = max_choices
        self.max_file_choices = max_file_choices

    # --- CD -----------------------------------------------------------------------------

    async def cd_candidates(self, toc: Toc) -> list[matching.Candidate]:
        exact = await self.mb.lookup_discid(toc.disc_id)
        releases = [] if exact is None or matching.is_cd_stub(exact) else list(exact.get("releases", []))
        found = matching.candidates(toc, releases)
        if not any(c.exact for c in found):
            found = matching.candidates(toc, releases + await self.mb.search_toc(toc))
        return [c for c in found if c.accepted]

    async def cd_choices(self, toc: Toc, request_id: str = mdr.ZERO_GUID) -> list[Choice]:
        choices: list[Choice] = []
        for cand in (await self.cd_candidates(toc))[: self.max_choices]:
            release = await self.mb.release(cand.release_id)
            if release is None:
                continue
            album = mdr.cd_album(release, cand.medium_position, request_id)
            if len(album.tracks) != toc.track_count:
                continue
            med = mdr.medium(release, cand.medium_position)
            choices.append(
                Choice(
                    title=album.title,
                    artist=album.artist,
                    details=_details(release, med),
                    mdr=mdr.build(album),
                    matches=tuple(range(toc.track_count)),
                    release_id=album.release_id,
                )
            )
        return choices

    async def cd_automatic(self, toc: Toc) -> str | None:
        """L'MDR della scelta migliore, per la ricerca automatica; ``None`` se non c'è."""
        choices = await self.cd_choices(toc)
        return choices[0].mdr if choices else None

    # --- file ---------------------------------------------------------------------------

    def _query(self, track: MdqTrack, with_album: bool) -> str:
        parts = [f"recording:{lucene_phrase(track.title)}"]
        if track.artist or track.album_artist:
            parts.append(f"artist:{lucene_phrase(track.artist or track.album_artist)}")
        if with_album and track.album:
            parts.append(f"release:{lucene_phrase(track.album)}")
        return " AND ".join(parts)

    def _rank_file_releases(self, track: MdqTrack, recordings: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        ranked = []
        for rec in recordings:
            title_ok = norm(rec.get("title", "")) == norm(track.title)
            length = rec.get("length")
            diff = abs(length - track.duration_ms) if length is not None and track.duration_ms else None
            if not title_ok or (diff is not None and diff > MAX_FILE_LENGTH_DIFF_MS):
                continue
            for rel in rec.get("releases", []):
                med = (rel.get("media") or [{}])[0]
                number = ((med.get("track") or [{}])[0]).get("number", "")
                group = rel.get("release-group") or {}
                key = (
                    norm(rel.get("title", "")) != norm(track.album) if track.album else False,
                    str(number) != str(track.number) if track.number is not None else False,
                    rel.get("status") != "Official",
                    group.get("primary-type") != "Album",
                    # Un file viene più spesso da un acquisto digitale (es. il FLAC Qobuz
                    # dell'utente, edizione Digital Media senza data): prima quelle.
                    med.get("format") != "Digital Media",
                    diff if diff is not None else MAX_FILE_LENGTH_DIFF_MS,
                    rel.get("date") or "9999",
                    rel["id"],
                )
                ranked.append((key, rel["id"], rec["id"]))
        ranked.sort()
        return ranked

    def _match_tracks(
        self, release: dict[str, Any], mdq: Mdq, recording_id: str
    ) -> list[tuple[MdqTrack, dict[str, Any]]]:
        all_tracks = [t for med in release.get("media", []) for t in med.get("tracks", [])]
        pairs = []
        for wanted in mdq.tracks:
            hit = None
            if wanted is mdq.tracks[0]:
                hit = next((t for t in all_tracks if (t.get("recording") or {}).get("id") == recording_id), None)
            if hit is None:
                hit = next((t for t in all_tracks if norm(t.get("title", "")) == norm(wanted.title)), None)
            if hit is not None:
                pairs.append((wanted, hit))
        return pairs

    async def file_choices(self, mdq: Mdq) -> list[Choice]:
        first = mdq.tracks[0]
        if not first.title:
            return []
        recordings = await self.mb.search_recordings(self._query(first, with_album=True))
        ranked = self._rank_file_releases(first, recordings)
        if not ranked and first.album:
            ranked = self._rank_file_releases(first, await self.mb.search_recordings(self._query(first, False)))
        choices: list[Choice] = []
        seen: set[str] = set()
        for _key, release_id, recording_id in ranked:
            if release_id in seen:
                continue
            seen.add(release_id)
            release = await self.mb.release(release_id)
            if release is None:
                continue
            pairs = self._match_tracks(release, mdq, recording_id)
            if not pairs:
                continue
            tracks = [mdr.track_from_mb(mb_track, wanted.request_id) for wanted, mb_track in pairs]
            album = mdr.album_from_release(release, tracks, mdq.request_id)
            index = {t.request_id: i for i, t in enumerate(mdq.tracks)}
            choices.append(
                Choice(
                    title=album.title,
                    artist=album.artist,
                    details=_details(release, (release.get("media") or [None])[0]),
                    mdr=mdr.build(album),
                    matches=tuple(index[w.request_id] for w, _t in pairs),
                    release_id=album.release_id,
                )
            )
            if len(choices) >= self.max_file_choices:
                break
        return choices
