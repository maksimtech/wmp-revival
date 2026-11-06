"""Dalle richieste di WMP alle scelte da proporre: CD (TOC) e file (MDQ).

Ogni scelta porta l'MDR-CD già pronto e gli indici delle tracce richieste che copre
(``matches`` nella pagina dei risultati). Una release completa costa una richiesta a
MusicBrainz: le scelte sono limitate a ``max_choices``.
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
    """Confronto tollerante: maiuscole, accenti, apostrofi tipografici, spazi."""
    text = unicodedata.normalize("NFKD", text).replace("’", "'").replace("‘", "'")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


# Marcatori aggiunti dai negozi online ("[Explicit]", "(Clean)"): MusicBrainz non li ha nei
# titoli. "[Edit]" invece resta: distingue davvero un'altra versione.
_STORE_MARKERS = re.compile(r"\s*[\[(](?:explicit|clean)(?: version)?[\])]\s*", re.IGNORECASE)


def strip_store_markers(title: str) -> str:
    return " ".join(_STORE_MARKERS.sub(" ", title).split())


def norm_title(text: str) -> str:
    return norm(strip_store_markers(text))


def _details(release: dict[str, Any], med: dict[str, Any] | None = None) -> str:
    parts = [release.get("date") or "", release.get("country") or ""]
    if med is not None:
        parts += [med.get("format") or "", f"{med.get('track-count', '?')} tracce"]
    if release.get("disambiguation"):
        parts.append(release["disambiguation"])
    return " · ".join(p for p in parts if p)


def lucene_phrase(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


MAX_ANCHORS = 3  # file da cui partire con la ricerca, se il primo non trova niente
MAX_SEARCHES = 4  # ricerche al massimo per una richiesta (1 al secondo sull'API pubblica)

_FILENAME_NUMBER = re.compile(r"^\s*(?:\d+\s*-\s*)?(\d{1,3})\s*[.\-_)]?\s+")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def filename_title(track: MdqTrack) -> str:
    """Titolo ricavato dal nome del file: '01. Stupid Girls.flac' -> 'Stupid Girls'.

    Toglie estensione, numero iniziale (anche '1-01 ') e un eventuale prefisso 'Artista - '.
    """
    name = _EXTENSION.sub("", track.filename.strip())
    name = _FILENAME_NUMBER.sub("", name, count=1)
    for artist in (track.artist, track.album_artist):
        if artist and norm(name).startswith(norm(artist) + " - "):
            name = name[len(artist) + 3 :]
    return name.strip(" -_.")


def title_candidates(track: MdqTrack) -> list[str]:
    """Titolo del tag e, se diverso, quello del nome del file."""
    found: list[str] = []
    for title in (track.title, filename_title(track) if track.filename else ""):
        if title and norm_title(title) not in {norm_title(t) for t in found}:
            found.append(title)
    return found


_COLLECTION_NS = uuid.UUID("6f1d0b1e-3c55-5a8e-9d2b-7d3b6a9c4e10")


def file_collection_id(release_id: str, request_id: str) -> str:
    """``WMCollectionID`` per una risposta sui file: uno nuovo per ogni richiesta.

    WMP conserva **un solo documento MDR per WMCollectionID** e, se ne riceve un altro con lo
    stesso ID, tiene le tracce di quello vecchio applicando i ``trackRequestID`` nuovi, che però
    contano da 0 in ogni richiesta. Risultato visto il 2026-11-06: tre file di P!nk diventati
    tutti "Long Way to Happy". Un ID per richiesta evita il riuso; per i CD resta l'MBID della
    release, perché lì ``trackRequestID`` è sempre l'indice nel TOC.
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
        """L'MDR della scelta migliore, per la ricerca automatica; ``None`` se non c'è."""
        choices = await self.cd_choices(toc)
        return choices[0].mdr if choices else None

    # --- file ---------------------------------------------------------------------------

    def _query(self, track: MdqTrack, with_album: bool) -> str:
        parts = [f"recording:{lucene_phrase(strip_store_markers(track.title))}"]
        if track.artist or track.album_artist:
            parts.append(f"artist:{lucene_phrase(track.artist or track.album_artist)}")
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

    @staticmethod
    def _match_tracks(
        release: dict[str, Any], mdq: Mdq, anchor: MdqTrack, recording_id: str
    ) -> list[tuple[MdqTrack, dict[str, Any]]]:
        """Abbina ogni file a una traccia diversa della release.

        Il file usato per la ricerca prende la registrazione trovata; gli altri il titolo del
        tag o, in alternativa, quello ricavato dal nome del file (senza marcatori dei negozi),
        sempre con uno scarto di durata di al massimo 5 s; a parità, la durata più vicina.
        Due file non prendono mai la stessa traccia. Il nome del file salva i tag già rovinati
        (es. tre file diversi tutti intitolati "Long Way to Happy").
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
        """Cerca partendo dai primi file, con il titolo del tag e poi quello del nome del file.

        Un tag rovinato (titolo giusto per un'altra traccia, durata diversa) non blocca più
        tutta la ricerca: si passa all'alternativa successiva. Al massimo ``MAX_SEARCHES``.
        """
        attempts: list[tuple[MdqTrack, bool]] = []
        for track in mdq.tracks[:MAX_ANCHORS]:
            for title in title_candidates(track):
                attempts.append((replace(track, title=title), True))
        for track in mdq.tracks[:1]:
            if track.album:
                attempts += [(replace(track, title=title), False) for title in title_candidates(track)]
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
        """Ricerca automatica dei file (POST a GetMDRCD): solo se anche l'album corrisponde."""
        ranked, anchor = await self._ranked_for(mdq)
        if anchor.album:
            ranked = [r for r in ranked if not r[0][0]]  # chiave[0]: album diverso
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
