"""MusicBrainz client against a fake local server: headers, query, cache, rate limits, retries."""

from __future__ import annotations

import time

import pytest
from aiohttp import web

from wmprevival import __version__
from wmprevival.config import MusicBrainzConfig
from wmprevival.musicbrainz import MusicBrainzClient, MusicBrainzError, ResponseCache
from wmprevival.toc import Toc

JOY = Toc.from_wmp("C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A")


class FakeMB:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, float]] = []
        self.statuses: list[int] = []

    async def handler(self, request: web.Request) -> web.Response:
        self.requests.append((request.raw_path, request.headers.get("User-Agent", ""), time.monotonic()))
        status = self.statuses.pop(0) if self.statuses else 200
        if status == 200:
            return web.json_response({"releases": [], "path": request.path})
        return web.Response(status=status, headers={"Retry-After": "0"} if status == 503 else {})


@pytest.fixture
async def fake_mb():
    fake = FakeMB()
    app = web.Application()
    app.router.add_get("/{tail:.*}", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield fake, f"http://127.0.0.1:{port}/ws/2"
    await runner.cleanup()


def _config(base: str, interval: float = 0.0) -> MusicBrainzConfig:
    return MusicBrainzConfig(base_url=base, contact="x@example.org", min_interval=interval)


async def test_headers_and_query_format(fake_mb) -> None:
    fake, base = fake_mb
    async with MusicBrainzClient(_config(base)) as client:
        assert await client.search_toc(JOY) == []
    [(raw_path, ua, _t)] = fake.requests
    assert ua == f"WMPRevival/{__version__} ( x@example.org )"
    assert raw_path == (
        "/ws/2/discid/-?toc=" + JOY.mb_toc + "&cdstubs=no&inc=artist-credits+labels+recordings+release-groups&fmt=json"
    )  # literal '+', as in the API documentation


async def test_404_means_not_found(fake_mb) -> None:
    fake, base = fake_mb
    fake.statuses = [404]
    async with MusicBrainzClient(_config(base)) as client:
        assert await client.lookup_discid(JOY.disc_id) is None


async def test_cache_avoids_the_second_request_and_remembers_404(fake_mb, tmp_path) -> None:
    fake, base = fake_mb
    fake.statuses = [404]
    cache = ResponseCache(tmp_path / "mb.sqlite")
    try:
        async with MusicBrainzClient(_config(base), cache) as client:
            assert await client.lookup_discid(JOY.disc_id) is None
            assert await client.lookup_discid(JOY.disc_id) is None
            await client.search_toc(JOY)
            await client.search_toc(JOY)
            assert client.requests_made == 2
    finally:
        cache.close()
    assert len(fake.requests) == 2


def test_cache_expiry(tmp_path) -> None:
    now = [1000.0]
    cache = ResponseCache(tmp_path / "mb.sqlite", clock=lambda: now[0])
    try:
        cache.put("found", 200, {"a": 1})
        cache.put("missing", 404, None)
        now[0] += 2 * 86400
        assert cache.get("found") == (200, {"a": 1})  # 30 days
        assert cache.get("missing") is None  # 1 day: fetched again
    finally:
        cache.close()


async def test_requests_are_spaced(fake_mb) -> None:
    fake, base = fake_mb
    async with MusicBrainzClient(_config(base, interval=0.25)) as client:
        await client.lookup_discid("a")
        await client.lookup_discid("b")
        await client.lookup_discid("c")
    starts = [t for _p, _u, t in fake.requests]
    assert all(b - a >= 0.24 for a, b in zip(starts, starts[1:], strict=False))


async def test_503_is_retried_then_succeeds(fake_mb) -> None:
    fake, base = fake_mb
    fake.statuses = [503, 503, 200]
    async with MusicBrainzClient(_config(base)) as client:
        assert await client.search_toc(JOY) == []
        assert client.requests_made == 3


async def test_persistent_503_and_other_errors_raise(fake_mb) -> None:
    fake, base = fake_mb
    fake.statuses = [503] * 4
    async with MusicBrainzClient(_config(base), max_retries=3) as client:
        with pytest.raises(MusicBrainzError, match="503"):
            await client.search_toc(JOY)
    fake.statuses = [400]
    async with MusicBrainzClient(_config(base)) as client:
        with pytest.raises(MusicBrainzError, match="400"):
            await client.lookup_discid("x")


def test_public_api_without_contact_cannot_even_start() -> None:
    from wmprevival.config import ConfigError

    with pytest.raises(ConfigError):
        MusicBrainzClient(MusicBrainzConfig())
