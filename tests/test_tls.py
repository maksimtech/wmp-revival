from __future__ import annotations

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


def test_write_and_load_pki_restricts_private_keys(tmp_path, ca, server) -> None:
    files = tls.PkiFiles.in_dir(tmp_path / "pki")
    restricted = []
    tls.write_pki(files, ca, server, restrict=restricted.append)
    assert files.all_exist()
    assert restricted == [files.ca_key, files.server_key]
    assert tls.load_ca_cert(files) == ca.cert
    assert tls.load_server_cert(files) == server.cert


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


async def test_name_constraints_reject_a_cert_for_another_site(tmp_path, ca, client_ctx) -> None:
    """Anche firmato dalla nostra CA, un certificato per un altro nome non è valido."""
    rogue = tls.issue_server_cert(ca, "www.example.com")
    files = tls.PkiFiles.in_dir(tmp_path / "rogue")
    tls.write_pki(files, ca, rogue, restrict=lambda _p: None)
    with pytest.raises(ssl.SSLCertVerificationError):
        await _handshake(files, client_ctx, "www.example.com")
