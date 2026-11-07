"""CD TOCs in WMP's format and MusicBrainz Disc IDs (phase 2).

WMP's format, verified on three real CDs (test zero and the 2026-11-06 captures), is the
same in the library's ``Album.TOC`` and in the ``CD=``/``cd=`` request parameter::

    C+96+5370+9D42+...+3AC1A
    │ │                 └ lead-out
    │ └ start offset of each track (12 here)
    └ track count

All values are hexadecimal without leading zeros; the offsets already include the 150
pregap sectors, like the Disc ID ones. WMP does not give the first track number: 1 is
assumed (nearly all CDs). Enhanced CDs (final data track) are still to be verified.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

SECTORS_PER_SECOND = 75


class TocError(ValueError):
    pass


@dataclass(frozen=True)
class Toc:
    offsets: tuple[int, ...]
    leadout: int
    first_track: int = 1

    @property
    def track_count(self) -> int:
        return len(self.offsets)

    @property
    def last_track(self) -> int:
        return self.first_track + self.track_count - 1

    @classmethod
    def from_wmp(cls, value: str) -> Toc:
        try:
            numbers = [int(part, 16) for part in value.strip().split("+")]
        except ValueError as exc:
            raise TocError(f"TOC is not hexadecimal: {value!r}") from exc
        if len(numbers) < 3:
            raise TocError(f"TOC too short: {value!r}")
        count, offsets, leadout = numbers[0], tuple(numbers[1:-1]), numbers[-1]
        if count != len(offsets):
            raise TocError(f"the TOC declares {count} tracks but has {len(offsets)} offsets")
        if not 1 <= count <= 99:
            raise TocError(f"impossible track count: {count}")
        if list(offsets) != sorted(set(offsets)) or offsets[0] < 150 or leadout <= offsets[-1]:
            raise TocError(f"offsets not increasing or invalid lead-out: {value!r}")
        return cls(offsets=offsets, leadout=leadout)

    def to_wmp(self) -> str:
        return "+".join(f"{n:X}" for n in (self.track_count, *self.offsets, self.leadout))

    @property
    def disc_id(self) -> str:
        """MusicBrainz Disc ID (libdiscid algorithm)."""
        sha = hashlib.sha1()  # noqa: S324 - this is the Disc ID definition, not security
        sha.update(f"{self.first_track:02X}{self.last_track:02X}{self.leadout:08X}".encode("ascii"))
        for i in range(99):
            sha.update(f"{self.offsets[i] if i < self.track_count else 0:08X}".encode("ascii"))
        return base64.b64encode(sha.digest()).decode("ascii").translate(str.maketrans("+/=", "._-"))

    @property
    def mb_toc(self) -> str:
        """Value of the ``toc`` parameter of ``/ws/2/discid/-``: first, last, lead-out, offsets."""
        return "+".join(str(n) for n in (self.first_track, self.last_track, self.leadout, *self.offsets))

    @property
    def track_lengths_ms(self) -> list[int]:
        bounds = (*self.offsets, self.leadout)
        return [round((bounds[i + 1] - bounds[i]) * 1000 / SECTORS_PER_SECOND) for i in range(self.track_count)]
