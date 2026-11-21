from __future__ import annotations

import datetime as dt
import ipaddress
import ssl

import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from wmprevival import tls
from wmprevival.paths import SERVICE_HOST
from wmprevival.recorder import server_context


def test_ca_is_name_constrained_to_the_service(ca: tls.KeyPair) -> None:
    ext = ca.cert.extensions.get_extension_for_class(x509.NameConstraints)
    assert ext.critical
    assert ext.value.permitted_subtrees == [x509.DNSName(SERVICE_HOST)]
    assert set(ext.value.excluded_subtrees or []) == {
        x509.IPAddress(ipaddress.ip_network("0.0.0.0/0")),
        x509.IPAddress(ipaddress.ip_network("::/0")),
    }


def test_ca_cannot_have_intermediates(ca: tls.KeyPair) -> None:
    bc = ca.cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical and bc.value.ca and bc.value.path_length == 0
    ku = ca.cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.key_cert_sign and ku.crl_sign and not ku.digital_signature


def test_server_cert_for_the_service_signed_by_ca(ca: tls.KeyPair, server: tls.KeyPair) -> None:
    cert = server.cert
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == [SERVICE_HOST]
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert cert.issuer == ca.cert.subject
    cert.verify_directly_issued_by(ca.cert)
    assert isinstance(server.key, ec.EllipticCurvePrivateKey)


def test_thumbprint_is_windows_style(ca: tls.KeyPair) -> None:
    thumb = tls.thumbprint(ca.cert)
    assert len(thumb) == 40 and thumb == thumb.upper()
    int(thumb, 16)


def test_write_and_load_pki_keeps_no_ca_key(tmp_path, ca, server) -> None:
    """The CA key is never written: once the server certificate exists nothing needs it (#2)."""
    files = tls.PkiFiles.in_dir(tmp_path / "pki")
    restricted = []
    tls.write_pki(files, ca, server, restrict=restricted.append)
    assert files.ready()
    assert not files.ca_key.exists()
    assert sorted(p.name for p in files.ca_cert.parent.iterdir()) == ["ca.cer", "server.key", "server.pem"]
    assert restricted == [files.server_key]
    assert tls.load_ca_cert(files) == ca.cert
    assert tls.load_server_cert(files) == server.cert


def test_pki_is_ready_without_a_ca_key(pki: tls.PkiFiles) -> None:
    assert pki.ready()
    pki.server_key.unlink()
    assert not pki.ready()


def test_ca_lives_just_longer_than_the_server_cert(ca: tls.KeyPair, server: tls.KeyPair) -> None:
    """No key is kept, so the CA only has to outlive the one certificate it signed."""
    assert ca.cert.not_valid_after_utc >= server.cert.not_valid_after_utc
    margin = tls.CA_VALIDITY - tls.SERVER_VALIDITY
    assert dt.timedelta(0) <= margin <= dt.timedelta(days=31)


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(-1, "expired"), (0.5, "expiring"), (29, "expiring"), (31, "valid")],
)
def test_server_cert_expiry(server: tls.KeyPair, days_left: float, expected: str) -> None:
    now = server.cert.not_valid_after_utc - dt.timedelta(days=days_left)
    assert tls.expiry(server.cert, now) == expected


async def _handshake(cert_files: tls.PkiFiles, client_ctx: ssl.SSLContext, hostname: str) -> None:
    import asyncio

    runner = web.AppRunner(web.Application())
    await runner.setup()
    site = web.TCPSite(
        runner, "127.0.0.1", 0, ssl_context=server_context(cert_files.server_cert, cert_files.server_key)
    )
    await site.start()
    port = runner.addresses[0][1]
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=client_ctx, server_hostname=hostname)
        writer.close()
        await writer.wait_closed()
    finally:
        await runner.cleanup()


async def test_tls_handshake_with_the_service_name(pki, client_ctx) -> None:
    await _handshake(pki, client_ctx, SERVICE_HOST)


async def test_client_hello_is_reported_even_if_the_client_rejects_the_cert(pki) -> None:
    """A client that doesn't trust the CA closes during the handshake: the notice must still arrive."""
    import asyncio

    hellos: list[str | None] = []
    ctx = server_context(pki.server_cert, pki.server_key, on_hello=hellos.append)
    runner = web.AppRunner(web.Application())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ctx)
    await site.start()
    port = runner.addresses[0][1]
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            await asyncio.open_connection(
                "127.0.0.1", port, ssl=ssl.create_default_context(), server_hostname=SERVICE_HOST
            )
    finally:
        await runner.cleanup()
    assert hellos == [SERVICE_HOST]


async def test_name_constraints_reject_a_cert_for_another_site(tmp_path, ca, client_ctx) -> None:
    """Even signed by our CA, a certificate for another name is not valid."""
    rogue = tls.issue_server_cert(ca, "www.example.com")
    files = tls.PkiFiles.in_dir(tmp_path / "rogue")
    tls.write_pki(files, ca, rogue, restrict=lambda _p: None)
    with pytest.raises(ssl.SSLCertVerificationError):
        await _handshake(files, client_ctx, "www.example.com")
