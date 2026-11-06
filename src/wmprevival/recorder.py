"""Registratore HTTPS (fase 1): salva ogni richiesta di WMP in un file JSON.

Senza inoltro risponde 404 senza corpo: veloce, e non è un 5xx, quindi non fa scattare
il rallentamento di WMP. Con un ``Forwarder`` restituisce la risposta del server vero;
se l'inoltro fallisce per un problema locale risponde comunque 404.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import gzip
import ipaddress
import itertools
import json
import re
import ssl
import urllib.parse
import weakref
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from aiohttp import web

from wmprevival import __version__
from wmprevival.forward import Forwarder, UpstreamResponse, response_headers_for_client

SCHEMA_VERSION = 1
MAX_BODY = 4 * 1024 * 1024

_sni: weakref.WeakKeyDictionary[ssl.SSLObject, str] = weakref.WeakKeyDictionary()


def server_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)

    def remember_sni(obj: ssl.SSLObject, name: str | None, _ctx: ssl.SSLContext) -> None:
        if name:
            _sni[obj] = name

    ctx.sni_callback = remember_sni
    return ctx


def check_listen(addresses: Sequence[str], allow_lan: bool) -> list[str]:
    """Solo indirizzi IP letterali; fuori dal loopback solo se richiesto esplicitamente."""
    result = []
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise ValueError(f"'{addr}' non è un indirizzo IP") from exc
        if not ip.is_loopback and not allow_lan:
            raise ValueError(f"{addr} non è un indirizzo di loopback: serve --allow-lan")
        result.append(str(ip))
    return result


FORM_TYPE = "application/x-www-form-urlencoded"
_CHARSET_RE = re.compile(r"charset\s*=\s*\"?([\w.:-]+)", re.IGNORECASE)


def _charset(content_type: str | None) -> str | None:
    match = _CHARSET_RE.search(content_type or "")
    return match.group(1).lower() if match else None


_TEXTUAL_TYPES = ("text/", "application/xml", "application/xhtml", "application/x-www-form-urlencoded")


def _is_textual(content_type: str | None) -> bool:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media.startswith(_TEXTUAL_TYPES) or media.endswith("+xml")


def _decode_text(plain: bytes, content_type: str | None) -> tuple[str, str, bool] | None:
    """Testo, charset usato e se il charset è un'ipotesi; ``None`` se non è testo."""
    declared = _charset(content_type)
    if plain.startswith((b"\xff\xfe", b"\xfe\xff")):
        attempts = [("utf-16", False)]
    elif plain.startswith(b"\xef\xbb\xbf"):
        attempts = [("utf-8-sig", False)]
    elif declared:
        attempts = [(declared, False)]
    else:
        # Niente charset dichiarato (es. HTML con solo <meta>): UTF-8, poi Windows-1252,
        # ma solo per tipi testuali: cp1252 decodifica qualunque byte, anche un binario.
        attempts = [("utf-8", False)]
        if _is_textual(content_type):
            attempts.append(("cp1252", True))
    for charset, guessed in attempts:
        try:
            text = plain.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\x00" in text:
            return None
        return text, charset, guessed
    return None


def encode_body(data: bytes, content_encoding: str | None = None, content_type: str | None = None) -> dict[str, Any]:
    """Corpo registrato: byte esatti in base64, più testo e campi del form se si può."""
    out: dict[str, Any] = {"length": len(data), "base64": base64.b64encode(data).decode("ascii")}
    if not data:
        return out
    plain = data
    if content_encoding:
        enc = content_encoding.lower()
        try:
            if enc == "gzip":
                plain = gzip.decompress(data)
            elif enc == "deflate":
                plain = zlib.decompress(data)
            out["decoded_length"] = len(plain)
        except (OSError, zlib.error, EOFError) as exc:
            out["decode_error"] = f"{type(exc).__name__}: {exc}"
            return out
    decoded = _decode_text(plain, content_type)
    if decoded is None:
        return out
    text, charset, guessed = decoded
    out["text"] = text
    out["text_encoding"] = charset
    if guessed:
        out["text_encoding_guessed"] = True
    if content_type and content_type.split(";", 1)[0].strip().lower() == FORM_TYPE:
        pairs = urllib.parse.parse_qsl(text, keep_blank_values=True, encoding=_charset(content_type) or "utf-8")
        out["form"] = [[k, v] for k, v in pairs]
    return out


def _headers(raw: Sequence[tuple[bytes, bytes]]) -> list[list[str]]:
    return [[k.decode("latin-1"), v.decode("latin-1")] for k, v in raw]


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", path).strip("-")[:60] or "root"


def _header(headers: Sequence[tuple[str, str]], name: str) -> str | None:
    return next((v for k, v in headers if k.lower() == name), None)


class Recorder:
    def __init__(
        self,
        captures_dir: Path,
        forwarder: Forwarder | None = None,
        on_record: Callable[[dict[str, Any], Path], None] | None = None,
    ) -> None:
        self.captures_dir = captures_dir
        self.forwarder = forwarder
        self.on_record = on_record
        self._seq = itertools.count(1)

    def _write(self, record: dict[str, Any], received: dt.datetime, seq: int, method: str, path: str) -> Path:
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        name = f"{received:%Y%m%d-%H%M%S-%f}_{seq:04d}_{method}_{_slug(path)}.json"
        dest = self.captures_dir / name
        dest.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return dest

    async def handle(self, request: web.Request) -> web.StreamResponse:
        received = dt.datetime.now(dt.UTC).astimezone()
        seq = next(self._seq)
        body = await request.read()
        ssl_obj = request.transport.get_extra_info("ssl_object") if request.transport else None
        record: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "recorder": f"wmprevival/{__version__}",
            "received_at": received.isoformat(),
            "client": request.remote,
            "tls": {
                "version": ssl_obj.version() if ssl_obj else None,
                "cipher": ssl_obj.cipher()[0] if ssl_obj and ssl_obj.cipher() else None,
                "sni": _sni.get(ssl_obj) if ssl_obj else None,
            },
            "request": {
                "method": request.method,
                "http_version": f"{request.version.major}.{request.version.minor}",
                # raw_path: percorso e query esattamente come li ha scritti WMP.
                "raw_path": request.raw_path,
                "path": request.path,
                # Query grezza: request.query_string di aiohttp è già decodificata e
                # trasforma '+' in spazio, che nel TOC di WMP (E+96+1FA9...) è un separatore.
                "query_string": request.raw_path.partition("?")[2],
                "headers": _headers(request.raw_headers),
                "body": encode_body(body, request.headers.get("Content-Encoding"), request.headers.get("Content-Type")),
            },
            "forwarded": self.forwarder is not None,
        }
        response: web.Response
        if self.forwarder is None:
            response = web.Response(status=404)
        else:
            try:
                headers = [(k, v) for k, v in record["request"]["headers"]]
                upstream: UpstreamResponse = await self.forwarder.forward(
                    request.method, request.raw_path, headers, body
                )
                record["upstream"] = {
                    "addresses": self.forwarder.addresses,
                    "status": upstream.status,
                    "reason": upstream.reason,
                    "headers": [list(h) for h in upstream.headers],
                    "body": encode_body(
                        upstream.body,
                        _header(upstream.headers, "content-encoding"),
                        _header(upstream.headers, "content-type"),
                    ),
                }
                response = web.Response(
                    status=upstream.status,
                    reason=upstream.reason or None,
                    headers=response_headers_for_client(upstream.headers),
                    body=upstream.body,
                )
            except Exception as exc:  # noqa: BLE001 - qualunque errore locale diventa un 404 registrato
                record["upstream_error"] = f"{type(exc).__name__}: {exc}"
                response = web.Response(status=404)
        record["response"] = {"status": response.status}
        dest = self._write(record, received, seq, request.method, request.path)
        if self.on_record:
            self.on_record(record, dest)
        return response


def build_app(recorder: Recorder) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_route("*", "/{tail:.*}", recorder.handle)
    return app


async def start(
    recorder: Recorder, listen: Sequence[str], port: int, ssl_ctx: ssl.SSLContext
) -> tuple[web.AppRunner, list[str], list[str]]:
    """Avvia il server; restituisce runner, indirizzi attivi ed errori sugli altri."""
    runner = web.AppRunner(build_app(recorder), access_log=None)
    await runner.setup()
    started, errors = [], []
    for host in listen:
        site = web.TCPSite(runner, host, port, ssl_context=ssl_ctx)
        try:
            await site.start()
            started.append(host)
        except OSError as exc:
            errors.append(f"{host}:{port}: {exc}")
    if not started:
        await runner.cleanup()
        raise OSError("impossibile ascoltare: " + "; ".join(errors))
    return runner, started, errors


async def serve_forever(runner: web.AppRunner) -> None:
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
