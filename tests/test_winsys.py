from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from wmprevival import winsys


class FakeRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), self.returncode, stdout="", stderr="fake error")


def test_add_root_machine_and_user() -> None:
    run = FakeRunner()
    winsys.add_root(Path("C:/x/ca.cer"), "machine", run)
    winsys.add_root(Path("C:/x/ca.cer"), "user", run)
    assert run.calls == [
        ["certutil", "-addstore", "-f", "Root", str(Path("C:/x/ca.cer"))],
        ["certutil", "-user", "-addstore", "-f", "Root", str(Path("C:/x/ca.cer"))],
    ]


def test_remove_and_has_root() -> None:
    run = FakeRunner()
    winsys.remove_root("ABCD", "user", run)
    assert winsys.has_root("ABCD", "machine", run)
    assert run.calls == [
        ["certutil", "-user", "-delstore", "Root", "ABCD"],
        ["certutil", "-store", "Root", "ABCD"],
    ]


def test_failures_raise_with_the_command() -> None:
    with pytest.raises(winsys.SystemCommandError, match="certutil -addstore"):
        winsys.add_root(Path("ca.cer"), "machine", FakeRunner(returncode=5))
    assert not winsys.has_root("ABCD", "machine", FakeRunner(returncode=1))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_restrict_uses_icacls_for_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setenv("USERDOMAIN", "WORKSTATION")
    run = FakeRunner()
    winsys.restrict_to_current_user(Path("C:/x/ca.key"), run)
    assert run.calls == [["icacls", str(Path("C:/x/ca.key")), "/inheritance:r", "/grant:r", "WORKSTATION\\alice:F"]]
