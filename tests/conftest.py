from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from wmprevival import tls
from wmprevival.paths import SERVICE_HOST


@pytest.fixture(scope="session")
def ca() -> tls.KeyPair:
    return tls.create_ca(SERVICE_HOST)


@pytest.fixture(scope="session")
def server(ca: tls.KeyPair) -> tls.KeyPair:
    return tls.issue_server_cert(ca, SERVICE_HOST)


@pytest.fixture
def pki(tmp_path: Path, ca: tls.KeyPair, server: tls.KeyPair) -> tls.PkiFiles:
    files = tls.PkiFiles.in_dir(tmp_path / "pki")
    tls.write_pki(files, ca, server, restrict=lambda _p: None)
    return files


@pytest.fixture
def client_ctx(ca: tls.KeyPair) -> ssl.SSLContext:
    """Client that trusts only the test CA, as WMP will after setup."""
    from cryptography.hazmat.primitives import serialization

    ctx = ssl.create_default_context(cadata=ca.cert.public_bytes(serialization.Encoding.PEM).decode())
    return ctx


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """WMPREVIVAL_HOME and a fake hosts file: tests don't touch the system."""
    home = tmp_path / "home"
    hosts_file = tmp_path / "hosts"
    hosts_file.write_bytes(b"# test hosts\r\n127.0.0.1 localhost\r\n")
    monkeypatch.setenv("WMPREVIVAL_HOME", str(home))
    monkeypatch.setenv("WMPREVIVAL_HOSTS", str(hosts_file))
    return home
