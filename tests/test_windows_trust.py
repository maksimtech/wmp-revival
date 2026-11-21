"""Does Windows enforce the CA's name constraints? (issue #1)

``test_tls`` proves that the CA carries the extension and that OpenSSL enforces it. WMP
doesn't use OpenSSL: it goes through Schannel and the Windows chain engine. These tests
ask that engine (crypt32, SSL policy included) to judge leaves signed by our CA.

Two engines:
- an engine whose only trust anchor is the test CA, in memory: no store is touched, so
  it runs everywhere on Windows;
- the machine engine after ``certutil -addstore Root``, as ``setup`` does. It changes the
  machine store, so it runs only with ``WMPREVIVAL_TEST_MACHINE_ROOT=1`` (set in CI, never
  in a local run).

Measured result: Windows enforces the constraints on the SAN (other DNS names and IP
addresses are rejected), but not on a leaf **without a SAN**: the SSL policy then matches the
host name against the subject CN, and the chain engine doesn't apply the constraints to the
CN. A CN-only leaf for any site, or any IP address, is accepted. The constraints alone don't
make the CA harmless on Windows: the CA key must not outlive ``setup`` (issue #2).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from wmprevival import tls
from wmprevival.paths import SERVICE_HOST

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows chain engine")

MACHINE_ROOT_ENV = "WMPREVIVAL_TEST_MACHINE_ROOT"


def _leaf(ca: tls.KeyPair, cn: str, san: list[x509.GeneralName] | None) -> x509.Certificate:
    """A server leaf with any SAN (or none): ``tls.issue_server_cert`` only does DNS names."""
    now = dt.datetime.now(dt.UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    )
    if san is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return builder.sign(ca.key, hashes.SHA256())


@dataclass(frozen=True)
class Case:
    id: str
    server_name: str
    name_in: str  # where the leaf carries the name: "san-dns", "san-ip" or "cn-only"
    accepted: bool

    def leaf(self, ca: tls.KeyPair) -> x509.Certificate:
        if self.name_in == "san-ip":
            return _leaf(ca, self.server_name, [x509.IPAddress(ipaddress.ip_address(self.server_name))])
        if self.name_in == "cn-only":
            return _leaf(ca, self.server_name, None)
        return tls.issue_server_cert(ca, self.server_name).cert


CASES = [
    Case("service", SERVICE_HOST, "san-dns", accepted=True),
    Case("subdomain", f"sub.{SERVICE_HOST}", "san-dns", accepted=True),
    Case("other-name", "example.com", "san-dns", accepted=False),
    Case("ip", "127.0.0.1", "san-ip", accepted=False),
    # The gap: no SAN, name only in the CN. Accepted for any site and any IP address.
    Case("cn-only", "example.com", "cn-only", accepted=True),
    Case("cn-only-ip", "127.0.0.1", "cn-only", accepted=True),
]


def _der(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def _check(verdict, case: Case) -> None:
    if case.accepted:
        assert verdict.valid, verdict
    else:
        # Rejected, and because of the name constraint: not for some other reason.
        assert not verdict.valid, verdict
        assert verdict.name_constraint_violation, verdict


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_exclusive_root_engine(ca: tls.KeyPair, case: Case) -> None:
    from tests import winchain

    verdict = winchain.verify_with_exclusive_root(_der(case.leaf(ca)), _der(ca.cert), case.server_name)
    _check(verdict, case)


def test_exclusive_root_engine_trusts_only_the_given_root(ca: tls.KeyPair) -> None:
    """A leaf from another CA is not trusted: the machine's roots don't leak into the engine."""
    from tests import winchain

    other = tls.create_ca(SERVICE_HOST)
    leaf = tls.issue_server_cert(other, SERVICE_HOST).cert
    verdict = winchain.verify_with_exclusive_root(_der(leaf), _der(ca.cert), SERVICE_HOST)
    assert not verdict.valid and not verdict.name_constraint_violation, verdict


def test_a_wrong_host_name_is_not_reported_as_a_name_constraint(ca: tls.KeyPair) -> None:
    """The two checks are told apart: a valid leaf asked for another name fails the SSL policy only."""
    from tests import winchain

    leaf = tls.issue_server_cert(ca, SERVICE_HOST).cert
    verdict = winchain.verify_with_exclusive_root(_der(leaf), _der(ca.cert), "example.com")
    assert verdict.chain_errors == 0 and verdict.policy_error == winchain.CERT_E_CN_NO_MATCH, verdict


@pytest.fixture(scope="module")
def machine_root(tmp_path_factory: pytest.TempPathFactory, ca: tls.KeyPair) -> Iterator[None]:
    if os.environ.get(MACHINE_ROOT_ENV) != "1":
        pytest.skip(f"changes the machine root store: only with {MACHINE_ROOT_ENV}=1 (CI)")
    cer: Path = tmp_path_factory.mktemp("machine-root") / "ca.cer"
    cer.write_bytes(_der(ca.cert))
    subprocess.run(["certutil", "-addstore", "-f", "Root", str(cer)], check=True, capture_output=True)
    try:
        yield
    finally:
        subprocess.run(["certutil", "-delstore", "Root", tls.thumbprint(ca.cert)], check=True, capture_output=True)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_machine_root_store(machine_root: None, ca: tls.KeyPair, case: Case) -> None:
    from tests import winchain

    verdict = winchain.verify_with_machine_store(_der(case.leaf(ca)), case.server_name)
    _check(verdict, case)
