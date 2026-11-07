"""Parsing of the MDQ-CD that WMP sends through the FAI bridge page (``mdq`` field).

Format observed on 2026-11-06 (see ``tests/fixtures/captures/fai_file_2_post_mdq.json``).
Parsed with defusedxml: the MDQ comes from a web page, so no entity expansion.
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
        raise MdqError("MDQ too large")
    try:
        root = SafeET.fromstring(xml)
    except Exception as exc:  # noqa: BLE001 - defusedxml raises a different type for each attack
        raise MdqError(f"invalid MDQ: {type(exc).__name__}") from exc
    query = root.find("MDQ-CD") if root.tag == "METADATA" else None
    if query is None:
        raise MdqError("missing METADATA/MDQ-CD")
    request_id = _text(query, "mdqRequestID")
    if not GUID_RE.match(request_id):
        raise MdqError("mdqRequestID is not a GUID")
    # In the automatic lookup (POST to GetMDRCD) the album sits at MDQ-CD level, once for
    # all tracks; in FAI it sits inside each <track>.
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
        raise MdqError("MDQ without tracks")
    return Mdq(request_id=request_id, tracks=tuple(tracks))
