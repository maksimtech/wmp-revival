"""MDR-CD 4.0 documents for WMP, built from a MusicBrainz release.

Structure taken from the empty template in ``wmp.dll`` and from the checks WMP runs on the
response (error messages in the same file):

- ``mdqRequestID`` always present ("request id tag not found in MDR");
- every ``<track>`` has ``trackRequestID`` (< number of requested tracks) and ``WMContentID``;
- ``trackNumber`` > 0; never an album without tracks.

To be verified on real WMP: GUID format (``{UPPERCASE}`` here), position of
``trackRequestID`` (last child of ``<track>`` here, as in the MDQ), format of
``releaseDate`` (the MusicBrainz date as is here: ``2006`` or ``2006-03-24``).

The XML is serialized as ASCII: every non-ASCII character becomes ``&#N;``, which is what
``EncodeOMI`` does in the Microsoft server page before ``WriteNamesEx``.
XML is built with ElementTree, never by concatenating strings.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from wmprevival.matching import artist_credit

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
SQL_NS = "urn:schemas-microsoft-com:xml-sql"
DATA_PROVIDER = "MusicBrainz"


def guid(mbid: str | None) -> str:
    return "{" + mbid.upper() + "}" if mbid else ""


@dataclass(frozen=True)
class MdrTrack:
    request_id: int
    recording_id: str
    title: str
    number: int
    performer: str = ""
    composer: str = ""
    conductor: str = ""


@dataclass(frozen=True)
class MdrAlbum:
    release_id: str
    release_group_id: str
    request_id: str
    title: str
    artist: str
    release_date: str = ""
    label: str = ""
    genre: str = ""
    tracks: tuple[MdrTrack, ...] = field(default_factory=tuple)
    # WMCollectionID when it differs from the release (file responses: see service.file_collection_id).
    collection_id: str = ""


def _names(relations: Iterable[dict[str, Any]], types: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for rel in relations:
        if rel.get("type") in types and rel.get("target-type") == "artist":
            name = (rel.get("artist") or {}).get("name", "")
            if name and name not in found:
                found.append(name)
    return found


def composers(recording: dict[str, Any]) -> str:
    """Composers of the works performed in the recording; failing that, the writers."""
    names: list[str] = []
    for rel in recording.get("relations", []):
        if rel.get("type") == "performance" and rel.get("target-type") == "work":
            work_rels = (rel.get("work") or {}).get("relations", [])
            for name in _names(work_rels, ("composer",)) or _names(work_rels, ("writer",)):
                if name not in names:
                    names.append(name)
    return "; ".join(names)


def conductors(recording: dict[str, Any]) -> str:
    return "; ".join(_names(recording.get("relations", []), ("conductor",)))


def genre(release: dict[str, Any]) -> str:
    """The top-voted genre of the release group (or of the release); ties broken alphabetically."""
    for source in (release.get("release-group") or {}, release):
        genres = sorted(source.get("genres", []), key=lambda g: (-g.get("count", 0), g.get("name", "")))
        if genres:
            return str(genres[0]["name"]).title()
    return ""


SPECIAL_LABELS = {"[no label]"}  # MusicBrainz special label for self-releases


def label(release: dict[str, Any]) -> str:
    for info in release.get("label-info", []):
        name = (info.get("label") or {}).get("name")
        if name and name not in SPECIAL_LABELS:
            return str(name)
    return ""


def medium(release: dict[str, Any], position: int) -> dict[str, Any]:
    for med in release.get("media", []):
        if med.get("position") == position:
            return dict(med)
    raise KeyError(f"release {release.get('id')} has no medium {position}")


def track_from_mb(track: dict[str, Any], request_id: int) -> MdrTrack:
    recording = track.get("recording") or {}
    credit = track.get("artist-credit") or recording.get("artist-credit") or []
    return MdrTrack(
        request_id=request_id,
        recording_id=recording.get("id", ""),
        title=track.get("title") or recording.get("title", ""),
        number=int(track.get("position") or 0),
        performer=artist_credit(credit),
        composer=composers(recording),
        conductor=conductors(recording),
    )


def album_from_release(
    release: dict[str, Any],
    tracks: Iterable[MdrTrack],
    request_id: str = ZERO_GUID,
    collection_id: str = "",
) -> MdrAlbum:
    return MdrAlbum(
        release_id=release["id"],
        collection_id=collection_id,
        release_group_id=(release.get("release-group") or {}).get("id", ""),
        request_id=request_id,
        title=release.get("title", ""),
        artist=artist_credit(release.get("artist-credit", [])),
        release_date=release.get("date", "") or "",
        label=label(release),
        genre=genre(release),
        tracks=tuple(tracks),
    )


def cd_album(release: dict[str, Any], medium_position: int, request_id: str = ZERO_GUID) -> MdrAlbum:
    """All tracks of the medium, with ``trackRequestID`` = 0-based index in the TOC."""
    tracks = [track_from_mb(t, i) for i, t in enumerate(medium(release, medium_position).get("tracks", []))]
    return album_from_release(release, tracks, request_id)


def _el(parent: ET.Element, tag: str, text: str | int | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text not in (None, ""):
        el.text = str(text)
    return el


def _serialize(root: ET.Element) -> str:
    raw: bytes = ET.tostring(root, encoding="us-ascii")
    return '<?xml version="1.0"?>\n' + raw.decode("ascii")


def build(album: MdrAlbum) -> str:
    if not album.tracks:
        raise ValueError("an MDR-CD without tracks is not valid for WMP")
    if any(t.number <= 0 for t in album.tracks):
        raise ValueError("trackNumber must be greater than zero")
    root = ET.Element("METADATA", {"xmlns:sql": SQL_NS})
    mdr = ET.SubElement(root, "MDR-CD")
    _el(mdr, "version", "4.0")
    _el(mdr, "WMCollectionID", guid(album.collection_id or album.release_id))
    _el(mdr, "WMCollectionGroupID", guid(album.release_group_id))
    _el(mdr, "mdqRequestID", album.request_id)
    _el(mdr, "uniqueFileID")
    _el(mdr, "albumTitle", album.title)
    _el(mdr, "albumArtist", album.artist)
    _el(mdr, "releaseDate", album.release_date)
    _el(mdr, "label", album.label)
    _el(mdr, "genre", album.genre)
    _el(mdr, "providerStyle", album.genre)
    for tag in ("publisherRating", "communityRating", "buyParams", "largeCoverParams", "smallCoverParams"):
        _el(mdr, tag)
    _el(mdr, "moreInfoParams")
    _el(mdr, "dataProvider", DATA_PROVIDER)
    # The release stays traceable even when WMCollectionID is one per request.
    _el(mdr, "dataProviderParams", f"musicbrainz-release={album.release_id}" if album.release_id else "")
    for tag in ("dataProviderLogo", "validated"):
        _el(mdr, tag)
    for track in album.tracks:
        t = ET.SubElement(mdr, "track")
        _el(t, "WMContentID", guid(track.recording_id))
        _el(t, "trackTitle", track.title)
        _el(t, "uniqueFileID")
        _el(t, "trackNumber", track.number)
        _el(t, "trackPerformer", track.performer)
        _el(t, "trackComposer", track.composer)
        _el(t, "trackConductor", track.conductor)
        _el(t, "period")
        _el(t, "trackRequestID", track.request_id)
    return _serialize(root)


def build_not_found(request_id: str = ZERO_GUID, code: str = "No match", backoff: int = 5) -> str:
    """A "no data" response shaped like the one the Microsoft server sends today."""
    root = ET.Element("METADATA")
    back = ET.SubElement(root, "Backoff")
    _el(back, "Time", backoff)
    _el(root, "mdqRequestID", request_id)
    _el(root, "ResponseCode", code)
    return _serialize(root)
