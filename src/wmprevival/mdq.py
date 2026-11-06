"""Lettura dell'MDQ-CD che WMP manda con la pagina ponte di FAI (campo ``mdq``).

Formato osservato il 2026-11-06 (vedi ``tests/fixtures/captures/fai_file_2_post_mdq.json``).
Parsing con defusedxml: l'MDQ arriva da una pagina web, niente entity expansion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

GUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
MAX_MDQ = 1024 * 1024


class MdqError(ValueError):
    pass


@dataclass(frozen=True)
class MdqTrack:
    request_id: int
    title: str
    artist: str
    album: str
    album_artist: str
    number: int | None
    duration_ms: int | None
    filename: str


@dataclass(frozen=True)
class Mdq:
    request_id: str
    tracks: tuple[MdqTrack, ...]


def _int(text: str | None) -> int | None:
    try:
        return int((text or "").strip())
    except ValueError:
        return None


def _text(el: Element, path: str) -> str:
    return (el.findtext(path) or "").strip()


def parse(xml: str) -> Mdq:
    if len(xml) > MAX_MDQ:
        raise MdqError("MDQ troppo grande")
    try:
        root = SafeET.fromstring(xml)
    except Exception as exc:  # noqa: BLE001 - defusedxml solleva tipi diversi per ogni attacco
        raise MdqError(f"MDQ non valido: {type(exc).__name__}") from exc
    query = root.find("MDQ-CD") if root.tag == "METADATA" else None
    if query is None:
        raise MdqError("manca METADATA/MDQ-CD")
    request_id = _text(query, "mdqRequestID")
    if not GUID_RE.match(request_id):
        raise MdqError("mdqRequestID non è un GUID")
    # Nella ricerca automatica (POST a GetMDRCD) l'album sta a livello di MDQ-CD, una volta
    # sola per tutte le tracce; in FAI sta dentro ogni <track>.
    shared_album = _text(query, "album/title/text")
    shared_album_artist = _text(query, "album/artist/text")
    tracks = []
    for i, track in enumerate(query.findall("track")):
        rid = _int(track.findtext("trackRequestID"))
        tracks.append(
            MdqTrack(
                request_id=rid if rid is not None else i,
                title=_text(track, "title/text"),
                artist=_text(track, "artist/text"),
                album=_text(track, "album/title/text") or shared_album,
                album_artist=_text(track, "album/artist/text") or shared_album_artist,
                number=_int(track.findtext("trackNumber")),
                duration_ms=_int(track.findtext("trackDuration")),
                filename=_text(track, "filename"),
            )
        )
    if not tracks:
        raise MdqError("MDQ senza tracce")
    return Mdq(request_id=request_id, tracks=tuple(tracks))
