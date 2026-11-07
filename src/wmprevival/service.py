"""From WMP requests to the choices to offer: CDs (TOC) and files (MDQ).

Each choice carries a ready-made MDR-CD and the indices of the requested tracks it covers
(``matches`` in the results page). A full release costs one MusicBrainz request, so the
choices are capped at ``max_choices``.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, replace
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
    """Lenient comparison: case, accents, typographic apostrophes, whitespace."""
    text = unicodedata.normalize("NFKD", text).replace("’", "'").replace("‘", "'")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


# Markers added by online stores ("[Explicit]", "(Clean)"): MusicBrainz titles don't have
# them. "[Edit]" stays, though: it really marks a different version.
_STORE_MARKERS = re.compile(r"\s*[\[(](?:explicit|clean)(?: version)?[\])]\s*", re.IGNORECASE)


def strip_store_markers(title: str) -> str:
    return " ".join(_STORE_MARKERS.sub(" ", title).split())


def norm_title(text: str) -> str:
    return norm(strip_store_markers(text))


def _details(release: dict[str, Any], med: dict[str, Any] | None = None) -> str:
    parts = [release.get("date") or "", release.get("country") or ""]
    if med is not None:
        parts += [med.get("format") or "", f"{med.get('track-count', '?')} tracks"]
    if release.get("disambiguation"):
        parts.append(release["disambiguation"])
    return " · ".join(p for p in parts if p)


def lucene_phrase(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


MAX_ANCHORS = 3  # files to start the search from, if the first one finds nothing
MAX_SEARCHES = 5  # max searches per request (1 per second on the public API)

_ARTIST_SEPARATORS = re.compile(r"\s*[;/]\s*|,\s+")
_VARIOUS = {"various artists", "artisti vari", "various", "va", "verschiedene interpreten"}


def collapse_artist(artist: str) -> str:
    """'ABBA, Abba' -> 'ABBA': WMP joins multiple artist tag values with ', '.

    Only if all parts are the same name: 'Earth, Wind & Fire' stays as is.
    """
    parts = [p for p in _ARTIST_SEPARATORS.split(artist) if p.strip()]
    if len(parts) > 1 and len({norm(p) for p in parts}) == 1:
        return parts[0].strip()
    return artist


def artist_candidates(track: MdqTrack) -> list[str]:
    """Track artist, its collapsed form, album artist (except 'Various Artists')."""
    found: list[str] = []
    raw = (track.artist, collapse_artist(track.artist), track.album_artist, collapse_artist(track.album_artist))
    for artist in raw:
        if artist and norm(artist) not in _VARIOUS and norm(artist) not in {norm(a) for a in found}:
            found.append(artist)
    return found or [""]


_FILENAME_NUMBER = re.compile(r"^\s*(?:\d+\s*-\s*)?(\d{1,3})\s*[.\-_)]?\s+")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def filename_guess(track: MdqTrack) -> tuple[str, str]:
    """(artist, title) taken from the file name.

    '01. Stupid Girls.flac' -> ('', 'Stupid Girls'); 'P!nk - Who Knew.wma' with artist P!nk
    -> ('', 'Who Knew'); '10. ABBA, Abba - Arrival.flac' with the damaged tag "Giovanni Allevi"
    -> ('ABBA, Abba', 'Arrival'). The artist is empty unless the name gives a different one.
    """
    name = _EXTENSION.sub("", track.filename.strip())
    name = _FILENAME_NUMBER.sub("", name, count=1)
    for artist in (track.artist, track.album_artist):
        if artist and norm(name).startswith(norm(artist) + " - "):
            return "", name[len(artist) + 3 :].strip(" -_.")
    if " - " in name:
        left, right = name.split(" - ", 1)
        return left.strip(), right.strip(" -_.")
    return "", name.strip(" -_.")


def filename_title(track: MdqTrack) -> str:
    return filename_guess(track)[1]


def filename_number(track: MdqTrack) -> int | None:
    match = _FILENAME_NUMBER.match(_EXTENSION.sub("", track.filename.strip()))
    return int(match.group(1)) if match else None


def tag_is_suspect(track: MdqTrack) -> bool:
    """The file name gives another artist and another title: the tag can't be trusted.

    Happens when WMP has already written another album's data into the library (ABBA's
    "Arrival" FLAC turned into Giovanni Allevi's "Panic").
    """
    artist, title = filename_guess(track)
    if not artist or norm_title(title) == norm_title(track.title):
        return False
    return norm(collapse_artist(artist)) not in {norm(collapse_artist(a)) for a in artist_candidates(track)}


def title_candidates(track: MdqTrack) -> list[str]:
    """The tag title and, if different, the one from the file name."""
    found: list[str] = []
    for title in (track.title, filename_title(track) if track.filename else ""):
        if title and norm_title(title) not in {norm_title(t) for t in found}:
            found.append(title)
    return found


_COLLECTION_NS = uuid.UUID("6f1d0b1e-3c55-5a8e-9d2b-7d3b6a9c4e10")


def file_collection_id(release_id: str, request_id: str) -> str:
    """``WMCollectionID`` for a file response: a new one for every request.

    WMP keeps **a single MDR document per WMCollectionID** and, when it receives another one
    with the same ID, keeps the old one's tracks while applying the new ``trackRequestID``s,
    which however count from 0 in every request. Result seen on 2026-11-06: three P!nk files
    all turned into "Long Way to Happy". One ID per request avoids the reuse; CDs keep the
    release MBID, because there ``trackRequestID`` is always the index in the TOC.
    """
    return str(uuid.uuid5(_COLLECTION_NS, f"{release_id.lower()}:{request_id.upper()}"))


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
        """The MDR of the best choice, for the automatic lookup; ``None`` if there is none."""
        choices = await self.cd_choices(toc)
        return choices[0].mdr if choices else None

    # --- file ---------------------------------------------------------------------------

    def _query(self, track: MdqTrack, with_album: bool) -> str:
        parts = [f"recording:{lucene_phrase(strip_store_markers(track.title))}"]
        if track.artist:
            parts.append(f"artist:{lucene_phrase(track.artist)}")
        if with_album and track.album:
            parts.append(f"release:{lucene_phrase(strip_store_markers(track.album))}")
        return " AND ".join(parts)

    def _rank_file_releases(self, track: MdqTrack, recordings: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        ranked = []
        for rec in recordings:
            title_ok = norm_title(rec.get("title", "")) == norm_title(track.title)
            length = rec.get("length")
            diff = abs(length - track.duration_ms) if length is not None and track.duration_ms else None
            if not title_ok or (diff is not None and diff > MAX_FILE_LENGTH_DIFF_MS):
                continue
            for rel in rec.get("releases", []):
                med = (rel.get("media") or [{}])[0]
                number = ((med.get("track") or [{}])[0]).get("number", "")
                group = rel.get("release-group") or {}
                key = (
                    norm_title(rel.get("title", "")) != norm_title(track.album) if track.album else False,
                    str(number) != str(track.number) if track.number is not None else False,
                    rel.get("status") != "Official",
                    group.get("primary-type") != "Album",
                    # A file more often comes from a digital purchase (e.g. the user's Qobuz
                    # FLAC, an undated Digital Media edition): those first.
                    med.get("format") != "Digital Media",
                    diff if diff is not None else MAX_FILE_LENGTH_DIFF_MS,
                    rel.get("date") or "9999",
                    rel["id"],
                )
                ranked.append((key, rel["id"], rec["id"]))
        ranked.sort()
        return ranked

    @staticmethod
    def _match_tracks(
        release: dict[str, Any], mdq: Mdq, anchor: MdqTrack, recording_id: str
    ) -> list[tuple[MdqTrack, dict[str, Any]]]:
        """Match each file to a different track of the release.

        The file used for the search gets the recording that was found; the others go by the
        tag title or, failing that, the one from the file name (without store markers),
        always with a length difference of at most 5 s; on a tie, the closest length wins.
        Two files never get the same track. The file name rescues already damaged tags
        (e.g. three different files all titled "Long Way to Happy").
        """
        all_tracks = [t for med in release.get("media", []) for t in med.get("tracks", [])]
        used: set[str] = set()
        pairs = []
        for wanted in mdq.tracks:
            hit = None
            if wanted is anchor:
                hit = next(
                    (
                        t
                        for t in all_tracks
                        if (t.get("recording") or {}).get("id") == recording_id and t.get("id") not in used
                    ),
                    None,
                )
            if hit is None:
                titles = {norm_title(t) for t in title_candidates(wanted) if t}
                options = []
                for t in all_tracks:
                    if t.get("id") in used or norm_title(t.get("title", "")) not in titles:
                        continue
                    length, duration = t.get("length"), wanted.duration_ms
                    diff = abs(length - duration) if length is not None and duration else None
                    if diff is not None and diff > MAX_FILE_LENGTH_DIFF_MS:
                        continue
                    options.append((diff if diff is not None else MAX_FILE_LENGTH_DIFF_MS, t.get("position", 0), t))
                options.sort(key=lambda o: (o[0], o[1]))
                hit = options[0][2] if options else None
            if hit is not None:
                used.add(hit.get("id", ""))
                pairs.append((wanted, hit))
        return pairs

    async def _ranked_for(self, mdq: Mdq) -> tuple[list[tuple[Any, ...]], MdqTrack]:
        """Search starting from the first files, with the tag title and then the file name one.

        A damaged tag (title right for another track, different length) no longer blocks the
        whole search: the next alternative is tried. At most ``MAX_SEARCHES``.
        """
        attempts: list[tuple[MdqTrack, bool]] = []
        for track in mdq.tracks[:MAX_ANCHORS]:
            if tag_is_suspect(track):
                # The file name first, without the tag's album (suspect as well).
                guess_artist, guess_title = filename_guess(track)
                number = filename_number(track)
                for artist in dict.fromkeys((guess_artist, collapse_artist(guess_artist))):
                    variant = replace(track, title=guess_title, artist=artist, album="", number=number)
                    attempts.append((variant, False))
            for artist in artist_candidates(track):
                for title in title_candidates(track):
                    attempts.append((replace(track, title=title, artist=artist), True))
        for track in mdq.tracks[:1]:
            if track.album:
                attempts += [
                    (replace(track, title=title, artist=artist), False)
                    for artist in artist_candidates(track)
                    for title in title_candidates(track)
                ]
        tried: set[str] = set()
        for variant, with_album in attempts:
            query = self._query(variant, with_album)
            if not variant.title or query in tried or len(tried) >= MAX_SEARCHES:
                continue
            tried.add(query)
            ranked = self._rank_file_releases(variant, await self.mb.search_recordings(query))
            if ranked:
                anchor = next(t for t in mdq.tracks if t.request_id == variant.request_id)
                return ranked, anchor
        return [], mdq.tracks[0]

    async def file_automatic(self, mdq: Mdq) -> str | None:
        """Automatic file lookup (POST to GetMDRCD): only if the album matches too.

        Never when the tag is suspect (the file name gives another artist): there the user
        decides from the "Find album info" page, not WMP in the background.
        """
        if any(tag_is_suspect(t) for t in mdq.tracks):
            return None
        ranked, anchor = await self._ranked_for(mdq)
        if anchor.album:
            ranked = [r for r in ranked if not r[0][0]]  # key[0]: different album
        choices = await self._file_choices_from(mdq, ranked, anchor, limit=1)
        return choices[0].mdr if choices else None

    async def file_choices(self, mdq: Mdq) -> list[Choice]:
        ranked, anchor = await self._ranked_for(mdq)
        return await self._file_choices_from(mdq, ranked, anchor, self.max_file_choices)

    async def _file_choices_from(
        self, mdq: Mdq, ranked: list[tuple[Any, ...]], anchor: MdqTrack, limit: int
    ) -> list[Choice]:
        choices: list[Choice] = []
        seen: set[str] = set()
        for _key, release_id, recording_id in ranked:
            if release_id in seen:
                continue
            seen.add(release_id)
            release = await self.mb.release(release_id)
            if release is None:
                continue
            pairs = self._match_tracks(release, mdq, anchor, recording_id)
            if not pairs:
                continue
            tracks = [mdr.track_from_mb(mb_track, wanted.request_id) for wanted, mb_track in pairs]
            album = mdr.album_from_release(
                release, tracks, mdq.request_id, collection_id=file_collection_id(release_id, mdq.request_id)
            )
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
            if len(choices) >= limit:
                break
        return choices
