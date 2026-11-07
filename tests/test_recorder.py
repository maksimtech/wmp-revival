from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from wmprevival import recorder
from wmprevival.forward import Forwarder, PinnedResolver
from wmprevival.paths import SERVICE_HOST

# Body in the format of the templates found in wmp.dll, with the TOC from test zero.
DAI = (
    '<?xml version="1.0" encoding="utf-8"?><DAI trackCount="14" '
    'toc="E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186"></DAI>'
)
WMP_UA = "Windows-Media-Player/12.0.26100.9549"


async def _start(rec: recorder.Recorder, pki) -> tuple[web.AppRunner, int]:
    ctx = recorder.server_context(pki.server_cert, pki.server_key)
    runner, started, errors = await recorder.start(rec, ["127.0.0.1"], 0, ctx)
    assert started == ["127.0.0.1"] and not errors
    return runner, runner.addresses[0][1]


def _client(client_ctx) -> aiohttp.ClientSession:
    """Client that reaches the recorder like WMP: service name, loopback IP."""
    connector = aiohttp.TCPConnector(resolver=PinnedResolver(SERVICE_HOST, ["127.0.0.1"]), ssl=client_ctx)
    return aiohttp.ClientSession(connector=connector)


def _captures(directory: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


async def test_records_request_and_answers_404(tmp_path, pki, client_ctx) -> None:
    seen = []
    rec = recorder.Recorder(tmp_path / "cap", on_record=lambda r, p: seen.append(p))
    runner, port = await _start(rec, pki)
    try:
        async with _client(client_ctx) as s:
            # encoded=True: the test client must not normalize %41%2b (yarl would).
            url = URL(f"https://{SERVICE_HOST}:{port}/cdinfo/GetMDRCD.aspx?CD=E+96+1FA9&x=%41%2b", encoded=True)
            async with s.post(
                url, data=DAI.encode(), headers={"User-Agent": WMP_UA, "Content-Type": "text/xml"}
            ) as resp:
                assert resp.status == 404
                assert await resp.read() == b""
    finally:
        await runner.cleanup()

    [cap] = _captures(tmp_path / "cap")
    assert seen and seen[0].name.endswith("_0001_POST_cdinfo-GetMDRCD.aspx.json")
    req = cap["request"]
    assert req["method"] == "POST"
    assert req["raw_path"] == "/cdinfo/GetMDRCD.aspx?CD=E+96+1FA9&x=%41%2b"  # no re-encoding
    assert req["query_string"] == "CD=E+96+1FA9&x=%41%2b"  # raw: '+' stays '+'
    assert ["User-Agent", WMP_UA] in req["headers"]
    assert req["body"]["text"] == DAI
    assert base64.b64decode(req["body"]["base64"]) == DAI.encode()
    assert cap["tls"]["sni"] == SERVICE_HOST
    assert cap["tls"]["version"] in ("TLSv1.2", "TLSv1.3")
    assert cap["response"] == {"status": 404}
    assert cap["forwarded"] is False


async def test_form_post_is_split_into_fields(tmp_path, pki, client_ctx) -> None:
    rec = recorder.Recorder(tmp_path / "cap")
    runner, port = await _start(rec, pki)
    mdq = '<?xml version="1.0"?><METADATA><MDQ-CD><mdqRequestID>1</mdqRequestID></MDQ-CD></METADATA>'
    try:
        async with (
            _client(client_ctx) as s,
            s.post(f"https://{SERVICE_HOST}:{port}/FAI/default.aspx", data={"mdq": mdq}) as resp,
        ):
            assert resp.status == 404
    finally:
        await runner.cleanup()
    [cap] = _captures(tmp_path / "cap")
    assert cap["request"]["body"]["form"] == [["mdq", mdq]]


def test_encode_body_html_with_declared_charset() -> None:
    html = "<html><body>Caffè – Sigur Rós</body></html>"
    body = recorder.encode_body(html.encode("cp1252"), content_type="text/html; charset=windows-1252")
    assert body["text"] == html
    assert body["text_encoding"] == "windows-1252"
    assert "text_encoding_guessed" not in body


def test_encode_body_guesses_cp1252_only_without_declared_charset() -> None:
    body = recorder.encode_body("Caffè".encode("cp1252"), content_type="text/html")
    assert body["text"] == "Caffè" and body["text_encoding_guessed"] is True


def test_encode_body_gzip_and_utf16_and_binary() -> None:
    gz = recorder.encode_body(gzip.compress(b"<p>ok</p>"), "gzip", "text/html; charset=utf-8")
    assert gz["text"] == "<p>ok</p>" and gz["decoded_length"] == 9
    u16 = recorder.encode_body("<a/>".encode("utf-16"))
    assert u16["text"] == "<a/>" and u16["text_encoding"] == "utf-16"
    broken = recorder.encode_body(b"\x00\x01", "gzip")
    assert "text" not in broken and "decode_error" in broken
    assert recorder.encode_body(b"") == {"length": 0, "base64": ""}


def test_connection_resets_are_silenced_but_other_errors_are_not() -> None:
    import asyncio
    from unittest import mock

    loop = asyncio.new_event_loop()
    try:
        with mock.patch.object(loop, "default_exception_handler") as default:
            recorder.quiet_connection_resets(loop)
            handler = loop.get_exception_handler()
            assert handler is not None
            # As Windows raises it: OSError with winerror 10054 (or 10053, 10057).
            reset = OSError(None, "connection closed by the remote host", None, 10054)
            assert reset.winerror == 10054 if hasattr(reset, "winerror") else True
            for exc in (ConnectionResetError(), ConnectionAbortedError(), reset):
                handler(loop, {"message": "Exception in callback _call_connection_lost", "exception": exc})
            default.assert_not_called()
            other = {"message": "other", "exception": ValueError("real error")}
            handler(loop, other)
            default.assert_called_once_with(other)
    finally:
        loop.close()


def test_check_listen() -> None:
    assert recorder.check_listen(["127.0.0.1", "::1"], allow_lan=False) == ["127.0.0.1", "::1"]
    with pytest.raises(ValueError, match="--allow-lan"):
        recorder.check_listen(["192.168.178.10"], allow_lan=False)
    assert recorder.check_listen(["192.168.178.10"], allow_lan=True) == ["192.168.178.10"]
    with pytest.raises(ValueError, match="is not an IP address"):
        recorder.check_listen(["localhost"], allow_lan=True)


# --- --forward against a fake Microsoft server ----------------------------------------

BRIDGE_HTML = (
    "<html><head><script>function go(){document.forms[0].mdq.value="
    'window.external.GetMDQByRequestID("");document.forms[0].submit();}</script></head>'
    '<body onload="go()"><form method="post"><input type="hidden" name="mdq"></form>'
    "<p>Album information – Sigur Rós</p></body></html>"
)


async def _fake_microsoft(pki) -> tuple[web.AppRunner, int, list[dict]]:
    received: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        received.append(
            {
                "method": request.method,
                "raw_path": request.raw_path,
                "host": request.host,
                "headers": list(request.headers.items()),
                "body": await request.read(),
            }
        )
        return web.Response(
            body=gzip.compress(BRIDGE_HTML.encode("utf-8")),
            headers={"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip", "X-Test": "1"},
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=recorder.server_context(pki.server_cert, pki.server_key))
    await site.start()
    return runner, runner.addresses[0][1], received


async def test_forward_relays_exactly_and_records_html_response(tmp_path, pki, client_ctx) -> None:
    upstream_runner, upstream_port, received = await _fake_microsoft(pki)
    try:
        async with Forwarder(SERVICE_HOST, ["127.0.0.1"], port=upstream_port, ssl_context=client_ctx) as fwd:
            rec = recorder.Recorder(tmp_path / "cap", forwarder=fwd)
            runner, port = await _start(rec, pki)
            try:
                async with (
                    _client(client_ctx) as s,
                    s.get(
                        f"https://{SERVICE_HOST}:{port}/FAI/default.aspx?id=%7B42%7D",
                        headers={"User-Agent": WMP_UA, "Accept": "*/*"},
                        auto_decompress=False,
                    ) as resp,
                ):
                    assert resp.status == 200
                    assert resp.headers["X-Test"] == "1"
                    assert resp.headers["Content-Encoding"] == "gzip"
                    assert gzip.decompress(await resp.read()).decode() == BRIDGE_HTML
            finally:
                await runner.cleanup()
    finally:
        await upstream_runner.cleanup()

    [up] = received
    assert up["raw_path"] == "/FAI/default.aspx?id=%7B42%7D"
    assert up["host"] == f"{SERVICE_HOST}:{upstream_port}"
    ua = [v for k, v in up["headers"] if k.lower() == "user-agent"]
    assert ua == [WMP_UA]  # no aiohttp User-Agent added or replaced

    [cap] = _captures(tmp_path / "cap")
    assert cap["forwarded"] is True
    assert cap["upstream"]["status"] == 200
    assert cap["upstream"]["body"]["text"] == BRIDGE_HTML
    assert ["X-Test", "1"] in cap["upstream"]["headers"]
    assert cap["response"] == {"status": 200}


async def test_forward_failure_becomes_404_not_5xx(tmp_path, pki, client_ctx) -> None:
    # Closed port: forwarding fails locally.
    async with Forwarder(SERVICE_HOST, ["127.0.0.1"], port=1, ssl_context=client_ctx, timeout=3) as fwd:
        rec = recorder.Recorder(tmp_path / "cap", forwarder=fwd)
        runner, port = await _start(rec, pki)
        try:
            async with _client(client_ctx) as s, s.get(f"https://{SERVICE_HOST}:{port}/FAI/default.aspx") as resp:
                assert resp.status == 404
        finally:
            await runner.cleanup()
    [cap] = _captures(tmp_path / "cap")
    assert "upstream_error" in cap and cap["response"] == {"status": 404}
