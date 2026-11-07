"""Local CA with name constraints and server certificate for the WMP service.

The CA can sign certificates valid only for ``musicmatch-ssl.xboxlive.com`` (and its
subdomains, given how DNS name constraints work) and for no IP address: even if its
private key left this PC, it couldn't be used to intercept other sites. ECDSA P-256 keys:
the real server uses ECC, so Schannel handles them.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_VALIDITY = dt.timedelta(days=3650)
SERVER_VALIDITY = dt.timedelta(days=825)
_BACKDATE = dt.timedelta(hours=1)


@dataclass(frozen=True)
class KeyPair:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey


@dataclass(frozen=True)
class PkiFiles:
    ca_cert: Path
    ca_key: Path
    server_cert: Path
    server_key: Path

    @classmethod
    def in_dir(cls, directory: Path) -> PkiFiles:
        return cls(
            ca_cert=directory / "ca.cer",
            ca_key=directory / "ca.key",
            server_cert=directory / "server.pem",
            server_key=directory / "server.key",
        )

    def all_exist(self) -> bool:
        return all(p.exists() for p in (self.ca_cert, self.ca_key, self.server_cert, self.server_key))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def create_ca(hostname: str, now: dt.datetime | None = None) -> KeyPair:
    now = now or _now()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WMP Revival"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"WMP Revival local CA ({hostname} only)"),
        ]
    )
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName(hostname)],
        excluded_subtrees=[
            x509.IPAddress(ipaddress.ip_network("0.0.0.0/0")),
            x509.IPAddress(ipaddress.ip_network("::/0")),
        ],
    )
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _BACKDATE)
        .not_valid_after(now + CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(constraints, critical=True)
        .add_extension(ski, critical=False)
        .sign(key, hashes.SHA256())
    )
    return KeyPair(cert, key)


def issue_server_cert(ca: KeyPair, hostname: str, now: dt.datetime | None = None) -> KeyPair:
    now = now or _now()
    key = ec.generate_private_key(ec.SECP256R1())
    ca_ski = ca.cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _BACKDATE)
        .not_valid_after(now + SERVER_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca.key, hashes.SHA256())
    )
    return KeyPair(cert, key)


def thumbprint(cert: x509.Certificate) -> str:
    """SHA-1 thumbprint in uppercase hex, as Windows shows it."""
    return cert.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303 - identifier, not security


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


Restrict = Callable[[Path], None]


def write_pki(files: PkiFiles, ca: KeyPair, server: KeyPair, restrict: Restrict) -> None:
    """Writes the four files. ``restrict`` limits the permissions of the private keys."""
    files.ca_cert.parent.mkdir(parents=True, exist_ok=True)
    for path, data in ((files.ca_key, _key_pem(ca.key)), (files.server_key, _key_pem(server.key))):
        path.write_bytes(data)
        restrict(path)
    # The CA in DER (.cer): the format certutil and Windows open directly.
    files.ca_cert.write_bytes(ca.cert.public_bytes(serialization.Encoding.DER))
    files.server_cert.write_bytes(server.cert.public_bytes(serialization.Encoding.PEM))


def load_ca_cert(files: PkiFiles) -> x509.Certificate:
    return x509.load_der_x509_certificate(files.ca_cert.read_bytes())


def load_server_cert(files: PkiFiles) -> x509.Certificate:
    return x509.load_pem_x509_certificate(files.server_cert.read_bytes())
