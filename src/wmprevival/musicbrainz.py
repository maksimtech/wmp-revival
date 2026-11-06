"""Client dell'API ``/ws/2`` di MusicBrainz, con limite di richieste e cache su disco.

Lo stesso codice serve l'API pubblica e un mirror locale (``musicbrainz-docker`` espone la
stessa API): cambiano solo ``base_url`` e ``min_interval`` in configurazione.

- Una richiesta alla volta, distanziate di almeno ``min_interval`` secondi.
- 503 e 429 (limite superato) si ritentano rispettando ``Retry-After``.
- Cache SQLite: risposte trovate per 30 giorni, "non trovato" per 1 giorno (un Disc ID
  collegato oggi con Picard deve comparire presto).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any

import aiohttp
from yarl import URL

from wmprevival.config import MusicBrainzConfig
from wmprevival.toc import Toc

FOUND_TTL = 30 * 86400.0
NOT_FOUND_TTL = 86400.0
RETRY_STATUSES = (429, 503)
DISCID_INC = "artist-credits+labels+recordings+release-groups"


class MusicBrainzError(RuntimeError):
    pass


class ResponseCache:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, status INTEGER, body TEXT, fetched_at REAL)"
        )
        self._clock = clock

    def get(self, key: str) -> tuple[int, Any] | None:
        row = self._db.execute("SELECT status, body, fetched_at FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        status, body, fetched_at = row
        ttl = FOUND_TTL if status == 200 else NOT_FOUND_TTL
        if self._clock() - fetched_at > ttl:
            return None
        return status, json.loads(body) if body else None

    def put(self, key: str, status: int, body: Any) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)",
            (key, status, json.dumps(body) if body is not None else None, self._clock()),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


def _query(params: dict[str, str]) -> str:
    # '+' resta letterale: separa i valori di inc e del toc, come negli esempi della documentazione.
    return "&".join(f"{k}={urllib.parse.quote(v, safe='+-._~')}" for k, v in params.items())


class MusicBrainzClient:
    def __init__(
        self,
        config: MusicBrainzConfig,
        cache: ResponseCache | None = None,
        *,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        self.config = config.validate()
        self.cache = cache
        self.max_retries = max_retries
        self.requests_made = 0
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._last_start = -1e9

    async def __aenter__(self) -> MusicBrainzClient:
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            headers={"User-Agent": self.config.user_agent, "Accept": "application/json"},
        )
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def url_for(self, path: str, params: dict[str, str]) -> URL:
        return URL(f"{self.config.base_url.rstrip('/')}/{path}?{_query({**params, 'fmt': 'json'})}", encoded=True)

    async def _wait_turn(self) -> None:
        delay = self._last_start + self.config.min_interval - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_start = time.monotonic()

    async def get(self, path: str, params: dict[str, str]) -> Any | None:
        """JSON della risposta, o ``None`` se MusicBrainz risponde 404."""
        url = self.url_for(path, params)
        key = str(url)
        if self.cache is not None and (hit := self.cache.get(key)) is not None:
            return hit[1]
        if self._session is None:
            raise RuntimeError("MusicBrainzClient va usato con 'async with'")
        async with self._lock:
            for attempt in range(self.max_retries + 1):
                await self._wait_turn()
                self.requests_made += 1
                async with self._session.get(url) as resp:
                    if resp.status in RETRY_STATUSES and attempt < self.max_retries:
                        retry_after = resp.headers.get("Retry-After", "")
                        await asyncio.sleep(float(retry_after) if retry_after.isdigit() else 2.0**attempt)
                        continue
                    if resp.status == 404:
                        body = None
                    elif resp.status == 200:
                        body = await resp.json(content_type=None)
                    else:
                        text = (await resp.text())[:200]
                        raise MusicBrainzError(f"MusicBrainz {resp.status} su {path}: {text}")
                    if self.cache is not None:
                        self.cache.put(key, resp.status, body)
                    return body
        raise MusicBrainzError(f"MusicBrainz non risponde dopo {self.max_retries} tentativi su {path}")

    async def lookup_discid(self, disc_id: str) -> Any | None:
        """Release con questo Disc ID (o un CD stub); ``None`` se il Disc ID non è collegato."""
        return await self.get(f"discid/{disc_id}", {"inc": DISCID_INC})

    async def search_toc(self, toc: Toc) -> list[dict[str, Any]]:
        """Ricerca per somiglianza sul TOC (stesso numero di tracce, durate vicine). Niente CD stub."""
        body = await self.get("discid/-", {"toc": toc.mb_toc, "cdstubs": "no", "inc": DISCID_INC})
        releases: list[dict[str, Any]] = (body or {}).get("releases", [])
        return releases
