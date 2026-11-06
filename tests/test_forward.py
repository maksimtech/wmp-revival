from __future__ import annotations

import socket

import pytest

from wmprevival import forward
from wmprevival.paths import SERVICE_HOST


def test_check_public_accepts_real_akamai_addresses() -> None:
    # Indirizzi visti il 2026-11-06 per musicmatch-ssl.xboxlive.com.
    assert forward.check_public(["184.25.52.9", "2a02:26f0:8d00:594::57"]) == ["184.25.52.9", "2a02:26f0:8d00:594::57"]


@pytest.mark.parametrize(
    "addr", ["127.0.0.1", "::1", "192.168.178.75", "10.0.0.1", "169.254.1.1", "0.0.0.0", "fe80::1"]
)
def test_check_public_refuses_loops_and_private(addr: str) -> None:
    with pytest.raises(forward.UnsafeUpstreamError):
        forward.check_public([addr])


def test_check_public_refuses_empty() -> None:
    with pytest.raises(forward.UnsafeUpstreamError):
        forward.check_public([])


async def test_pinned_resolver_only_knows_its_name() -> None:
    res = forward.PinnedResolver(SERVICE_HOST, ["184.25.52.9", "2a02:26f0:8d00:594::57"])
    v4 = await res.resolve(SERVICE_HOST.upper(), 443, socket.AF_INET)
    assert [r["host"] for r in v4] == ["184.25.52.9"]
    both = await res.resolve(SERVICE_HOST, 443, socket.AF_UNSPEC)
    assert [r["family"] for r in both] == [socket.AF_INET, socket.AF_INET6]
    with pytest.raises(OSError, match="conosce solo"):
        await res.resolve("www.example.com", 443)


def test_url_keeps_raw_path_and_default_port() -> None:
    fwd = forward.Forwarder(SERVICE_HOST, ["184.25.52.9"])
    url = fwd.url_for("/cdinfo/GetMDRCD.aspx?CD=E+96&x=%7B")
    assert str(url) == f"https://{SERVICE_HOST}/cdinfo/GetMDRCD.aspx?CD=E+96&x=%7B"
    assert url.raw_query_string == "CD=E+96&x=%7B"


def test_response_headers_drop_hop_by_hop() -> None:
    out = forward.response_headers_for_client(
        [("Content-Type", "text/html"), ("Transfer-Encoding", "chunked"), ("Content-Length", "5"), ("Set-Cookie", "a")]
    )
    assert list(out.items()) == [("Content-Type", "text/html"), ("Set-Cookie", "a")]
