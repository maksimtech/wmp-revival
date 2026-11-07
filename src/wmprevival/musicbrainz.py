"""Client for the MusicBrainz ``/ws/2`` API, with rate limiting and an on-disk cache.

The same code serves the public API and a local mirror (``musicbrainz-docker`` exposes the
same API): only ``base_url`` and ``min_interval`` change in the configuration.

- One request at a time, at least ``min_interval`` seconds apart.
- 503 and 429 (rate limit exceeded) are retried, honouring ``Retry-After``.
- SQLite cache: found responses for 30 days, "not found" for 1 day (a Disc ID attached
  today with Picard should show up soon).
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
RELEASE_INC = (
    "artist-credits+labels+recordings+release-groups+genres+recording-level-rels+work-rels+work-level-rels+artist-rels"
)


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
    # '+' stays literal: it separates the inc and toc values, as in the documentation examples.
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
        """The response JSON, or ``None`` if MusicBrainz answers 404."""
        url = self.url_for(path, params)
        key = str(url)
        if self.cache is not None and (hit := self.cache.get(key)) is not None:
            return hit[1]
        if self._session is None:
            raise RuntimeError("MusicBrainzClient must be used with 'async with'")
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
                        raise MusicBrainzError(f"MusicBrainz {resp.status} on {path}: {text}")
                    if self.cache is not None:
                        self.cache.put(key, resp.status, body)
                    return body
        raise MusicBrainzError(f"MusicBrainz not responding after {self.max_retries} retries on {path}")

    async def lookup_discid(self, disc_id: str) -> Any | None:
        """Releases with this Disc ID (or a CD stub); ``None`` if the Disc ID is not attached."""
        return await self.get(f"discid/{disc_id}", {"inc": DISCID_INC})

    async def release(self, mbid: str) -> Any | None:
        """Full release: tracks, credits, labels, genres, composers (via works) and conductors."""
        return await self.get(f"release/{mbid}", {"inc": RELEASE_INC})

    async def search_recordings(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Recording search (Lucene syntax); each result lists its releases."""
        body = await self.get("recording", {"query": query, "limit": str(limit)})
        recordings: list[dict[str, Any]] = (body or {}).get("recordings", [])
        return recordings

    async def search_toc(self, toc: Toc) -> list[dict[str, Any]]:
        """Fuzzy TOC search (same track count, close lengths). No CD stubs."""
        body = await self.get("discid/-", {"toc": toc.mb_toc, "cdstubs": "no", "inc": DISCID_INC})
        releases: list[dict[str, Any]] = (body or {}).get("releases", [])
        return releases
