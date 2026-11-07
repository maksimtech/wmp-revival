from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from wmprevival import cli, winsys
from wmprevival.paths import HOSTS_MARKER, SERVICE_HOST, Paths
from wmprevival.state import InstallState

runner = CliRunner()


class FakeStore:
    """Simulated root store: records the certutil commands and answers accordingly."""

    def __init__(self) -> None:
        self.roots: set[tuple[str, str]] = set()
        self.calls: list[list[str]] = []
        self.thumb_of: dict[str, str] = {}

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        store = "user" if "-user" in cmd else "machine"
        rc = 0
        if "-addstore" in cmd:
            from wmprevival import tls

            thumb = tls.thumbprint(tls.load_ca_cert(tls.PkiFiles.in_dir(Paths.from_env().pki)))
            self.roots.add((thumb, store))
        elif "-delstore" in cmd:
            self.roots.discard((cmd[-1], store))
        elif "-store" in cmd:
            rc = 0 if (cmd[-1], store) in self.roots else 1
        return subprocess.CompletedProcess(cmd, rc, "", "")


@pytest.fixture
def system(monkeypatch: pytest.MonkeyPatch, home) -> FakeStore:
    fake = FakeStore()
    for name in ("add_root", "remove_root", "has_root"):
        real = getattr(winsys, name)
        monkeypatch.setattr(winsys, name, lambda *a, _r=real: _r(*a, runner=fake))
    monkeypatch.setattr(winsys, "flush_dns", lambda: None)
    monkeypatch.setattr(winsys, "restrict_to_current_user", lambda _p: None)
    monkeypatch.setattr(winsys, "is_admin", lambda: True)
    monkeypatch.setattr(winsys, "wmp_policies", lambda: {})
    monkeypatch.setattr(winsys, "wmp_file_organizing", lambda: {})
    return fake


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and "wmprevival 2026." in result.output


def test_setup_dry_run_touches_nothing(system, home) -> None:
    paths = Paths.from_env()
    before = paths.hosts_file.read_bytes()
    result = runner.invoke(cli.app, ["setup", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "--dry-run" in result.output and SERVICE_HOST in result.output
    assert paths.hosts_file.read_bytes() == before
    assert not home.exists()
    assert system.calls == []


def test_setup_requires_admin(system, monkeypatch) -> None:
    monkeypatch.setattr(winsys, "is_admin", lambda: False)
    result = runner.invoke(cli.app, ["setup", "--yes"])
    assert result.exit_code == 1 and "administrator" in result.output
    assert system.calls == []


def test_setup_then_teardown_round_trip(system, home) -> None:
    paths = Paths.from_env()
    original = paths.hosts_file.read_bytes()

    result = runner.invoke(cli.app, ["setup", "--yes"])
    assert result.exit_code == 0, result.output
    text = paths.hosts_file.read_bytes().decode()
    assert f"127.0.0.1 {SERVICE_HOST}  {HOSTS_MARKER}\r\n" in text
    assert f"::1 {SERVICE_HOST}  {HOSTS_MARKER}\r\n" in text
    state = InstallState.load(paths.state_file)
    assert state.ca_store == "machine" and state.hosts_modified
    assert (state.ca_thumbprint, "machine") in system.roots
    assert len(state.hosts_backups) == 1

    status = runner.invoke(cli.app, ["status", "--port", "1"])
    assert status.exit_code == 0, status.output
    assert "machine" in status.output and HOSTS_MARKER in status.output

    # A second setup reuses the PKI: same CA, no duplicate lines in hosts.
    again = runner.invoke(cli.app, ["setup", "--yes"])
    assert again.exit_code == 0 and "reuse the PKI" in again.output
    assert paths.hosts_file.read_bytes().decode().count(SERVICE_HOST) == 2
    assert InstallState.load(paths.state_file).ca_thumbprint == state.ca_thumbprint

    down = runner.invoke(cli.app, ["teardown", "--yes", "--purge"])
    assert down.exit_code == 0, down.output
    assert paths.hosts_file.read_bytes() == original
    assert system.roots == set()
    assert not paths.pki.exists()
    assert InstallState.load(paths.state_file).ca_thumbprint is None


def test_setup_switching_store_removes_the_old_root(system) -> None:
    assert runner.invoke(cli.app, ["setup", "--yes"]).exit_code == 0
    result = runner.invoke(cli.app, ["setup", "--yes", "--store", "user"])
    assert result.exit_code == 0, result.output
    assert {s for _t, s in system.roots} == {"user"}


def test_teardown_with_nothing_installed(system) -> None:
    result = runner.invoke(cli.app, ["teardown", "--yes"])
    assert result.exit_code == 0 and "Nothing to undo" in result.output


def test_record_without_pki_fails_cleanly(system) -> None:
    result = runner.invoke(cli.app, ["record"])
    assert result.exit_code == 1 and "setup" in result.output


def test_record_refuses_lan_without_flag(system) -> None:
    assert runner.invoke(cli.app, ["setup", "--yes"]).exit_code == 0
    result = runner.invoke(cli.app, ["record", "--listen", "192.168.178.10"])
    assert result.exit_code == 1 and "--allow-lan" in result.output


def test_mb_lookup_requires_a_contact_for_the_public_api(home) -> None:
    result = runner.invoke(cli.app, ["mb-lookup", "2+96+200+300"])
    assert result.exit_code == 1 and "contact" in result.output


def test_mb_lookup_rejects_an_invalid_toc(home) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('[musicbrainz]\ncontact = "x@example.org"\n', encoding="utf-8")
    result = runner.invoke(cli.app, ["mb-lookup", "3+96+200"])
    assert result.exit_code == 1 and "tracks" in result.output


def test_fixture_command(tmp_path) -> None:
    src = tmp_path / "cap.json"
    src.write_text(json.dumps({"client": "::1", "request": {"raw_path": "/x?g=DEADBEEF-1234-4ABC-8DEF-0123456789AB"}}))
    dest = tmp_path / "fixtures" / "x.json"
    result = runner.invoke(cli.app, ["fixture", str(src), str(dest)])
    assert result.exit_code == 0, result.output
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["request"]["raw_path"] == "/x?g=00000000-0000-0000-0000-000000000001"
    assert "1 GUID" in result.output


def test_status_warns_when_wmp_moves_files(system, monkeypatch) -> None:
    monkeypatch.setattr(winsys, "wmp_file_organizing", lambda: dict(winsys.WMP_FILE_ORGANIZING))
    result = runner.invoke(cli.app, ["status", "--port", "1"])
    assert result.exit_code == 0
    assert "AutoRename" in result.output and "AutoOrganize" in result.output


def test_background_printer_never_blocks_the_caller() -> None:
    """Regression 2026-11-06: a selection in the console stopped the server."""
    import threading
    import time

    release = threading.Event()
    printed: list[str] = []

    class FrozenConsole:
        def print(self, *args, **kwargs) -> None:
            release.wait(5)  # like a console in selection mode
            printed.append(args[0])

    printer = cli.BackgroundPrinter(FrozenConsole())  # type: ignore[arg-type]
    start = time.monotonic()
    for i in range(50):
        printer.print(f"line {i}")
    assert time.monotonic() - start < 0.5  # the caller (the server) doesn't wait
    release.set()
    deadline = time.monotonic() + 5
    while len(printed) < 50 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert printed == [f"line {i}" for i in range(50)]  # nothing lost, in order
