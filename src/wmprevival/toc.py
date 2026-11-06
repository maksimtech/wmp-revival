"""TOC dei CD nel formato di WMP e Disc ID di MusicBrainz (fase 2).

Formato di WMP, verificato su tre CD reali (test zero e catture del 2026-11-06), identico
in ``Album.TOC`` della libreria e nel parametro ``CD=``/``cd=`` delle richieste::

    C+96+5370+9D42+...+3AC1A
    │ │                 └ lead-out
    │ └ offset di inizio di ogni traccia (qui 12)
    └ numero di tracce

Tutti i valori in esadecimale senza zeri iniziali; gli offset includono già i 150 settori di
pregap, come quelli del Disc ID. WMP non indica il numero della prima traccia: si assume 1
(quasi tutti i CD). I CD Enhanced (traccia dati finale) restano da verificare.
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
            raise TocError(f"TOC non esadecimale: {value!r}") from exc
        if len(numbers) < 3:
            raise TocError(f"TOC troppo corto: {value!r}")
        count, offsets, leadout = numbers[0], tuple(numbers[1:-1]), numbers[-1]
        if count != len(offsets):
            raise TocError(f"il TOC dichiara {count} tracce ma ha {len(offsets)} offset")
        if not 1 <= count <= 99:
            raise TocError(f"numero di tracce impossibile: {count}")
        if list(offsets) != sorted(set(offsets)) or offsets[0] < 150 or leadout <= offsets[-1]:
            raise TocError(f"offset non crescenti o lead-out non valido: {value!r}")
        return cls(offsets=offsets, leadout=leadout)

    def to_wmp(self) -> str:
        return "+".join(f"{n:X}" for n in (self.track_count, *self.offsets, self.leadout))

    @property
    def disc_id(self) -> str:
        """Disc ID di MusicBrainz (algoritmo di libdiscid)."""
        sha = hashlib.sha1()  # noqa: S324 - è la definizione del Disc ID, non sicurezza
        sha.update(f"{self.first_track:02X}{self.last_track:02X}{self.leadout:08X}".encode("ascii"))
        for i in range(99):
            sha.update(f"{self.offsets[i] if i < self.track_count else 0:08X}".encode("ascii"))
        return base64.b64encode(sha.digest()).decode("ascii").translate(str.maketrans("+/=", "._-"))

    @property
    def mb_toc(self) -> str:
        """Valore del parametro ``toc`` di ``/ws/2/discid/-``: prima, ultima, lead-out, offset."""
        return "+".join(str(n) for n in (self.first_track, self.last_track, self.leadout, *self.offsets))

    @property
    def track_lengths_ms(self) -> list[int]:
        bounds = (*self.offsets, self.leadout)
        return [round((bounds[i + 1] - bounds[i]) * 1000 / SECTORS_PER_SECOND) for i in range(self.track_count)]
