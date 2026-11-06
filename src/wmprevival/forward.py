"""Inoltro delle richieste di WMP al server Microsoft vero (``record --forward``).

Il file hosts punta il nome su 127.0.0.1, quindi l'indirizzo vero si chiede direttamente
a un DNS pubblico. Il client si collega a quell'indirizzo tramite un resolver fissato, ma
l'URL resta ``https://musicmatch-ssl.xboxlive.com/...``: SNI, header Host e verifica del
certificato usano il nome originale, con l'archivio di fiducia di sistema.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from types import TracebackType

import aiohttp
import dns.asyncresolver
import dns.resolver
from aiohttp.abc import AbstractResolver, ResolveResult
from multidict import CIMultiDict
from yarl import URL

PUBLIC_RESOLVERS = ("1.1.1.1", "9.9.9.9")

# RFC 9110 §7.6.1 più quelli che il client ricalcola da sé.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_REQUEST_DROP = HOP_BY_HOP | {"host", "content-length"}
_RESPONSE_DROP = HOP_BY_HOP | {"content-length"}


class UnsafeUpstreamError(RuntimeError):
    """Il DNS ha restituito un indirizzo che non può essere il server vero."""


def check_public(addresses: Iterable[str]) -> list[str]:
    """Rifiuta loopback, reti private e simili: inoltrare lì farebbe un giro su sé stessi."""
    result = []
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if not ip.is_global:
            raise UnsafeUpstreamError(f"{addr} non è un indirizzo pubblico")
        result.append(str(ip))
    if not result:
        raise UnsafeUpstreamError("nessun indirizzo")
    return result


async def resolve_direct(
    hostname: str, nameservers: Sequence[str] = PUBLIC_RESOLVERS, timeout: float = 5.0
) -> list[str]:
    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = list(nameservers)
    resolver.lifetime = timeout
    found: list[str] = []
    for rdtype in ("A", "AAAA"):
        try:
            answer = await resolver.resolve(hostname, rdtype)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        found.extend(rr.to_text() for rr in answer)
    return check_public(found)


class PinnedResolver(AbstractResolver):
    """Risolve un solo nome, sugli indirizzi dati; qualunque altro nome è un errore."""

    def __init__(self, hostname: str, addresses: Sequence[str]) -> None:
        self._hostname = hostname.lower()
        self._addresses = list(addresses)

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        if host.lower() != self._hostname:
            raise OSError(f"il resolver di inoltro conosce solo {self._hostname}, non {host}")
        results: list[ResolveResult] = []
        for addr in self._addresses:
            fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
            if family not in (socket.AF_UNSPEC, fam):
                continue
            results.append(
                ResolveResult(hostname=host, host=addr, port=port, family=fam, proto=0, flags=socket.AI_NUMERICHOST)
            )
        if not results:
            raise OSError(f"nessun indirizzo per {host} nella famiglia {family!r}")
        return results

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class UpstreamResponse:
    status: int
    reason: str
    headers: list[tuple[str, str]]
    body: bytes


def _decode_raw(raw: Iterable[tuple[bytes, bytes]]) -> list[tuple[str, str]]:
    return [(k.decode("latin-1"), v.decode("latin-1")) for k, v in raw]


def response_headers_for_client(headers: Iterable[tuple[str, str]]) -> CIMultiDict[str]:
    return CIMultiDict((k, v) for k, v in headers if k.lower() not in _RESPONSE_DROP)


class Forwarder:
    def __init__(
        self,
        hostname: str,
        addresses: Sequence[str],
        *,
        port: int = 443,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.hostname = hostname
        self.addresses = list(addresses)
        self._port = port
        self._ssl: ssl.SSLContext | bool = ssl_context if ssl_context is not None else True
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> Forwarder:
        connector = aiohttp.TCPConnector(resolver=PinnedResolver(self.hostname, self.addresses), ssl=self._ssl)
        # auto_decompress=False: a WMP torna esattamente il corpo che ha mandato il server.
        self._session = aiohttp.ClientSession(connector=connector, timeout=self._timeout, auto_decompress=False)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def url_for(self, raw_path: str) -> URL:
        authority = self.hostname if self._port == 443 else f"{self.hostname}:{self._port}"
        # encoded=True: percorso e query passano byte per byte, senza ricodifiche di yarl.
        return URL(f"https://{authority}{raw_path}", encoded=True)

    async def forward(
        self, method: str, raw_path: str, headers: Iterable[tuple[str, str]], body: bytes
    ) -> UpstreamResponse:
        if self._session is None:
            raise RuntimeError("Forwarder va usato con 'async with'")
        out = CIMultiDict((k, v) for k, v in headers if k.lower() not in _REQUEST_DROP)
        async with self._session.request(
            method,
            self.url_for(raw_path),
            headers=out,
            data=body or None,
            allow_redirects=False,
            # Niente header aggiunti da aiohttp: al server arriva quello che ha mandato WMP.
            skip_auto_headers=("User-Agent", "Accept", "Accept-Encoding", "Content-Type"),
        ) as resp:
            data = await resp.read()
            return UpstreamResponse(resp.status, resp.reason or "", _decode_raw(resp.raw_headers), data)
