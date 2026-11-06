from __future__ import annotations

import datetime as dt

from wmprevival import hosts
from wmprevival.paths import HOSTS_MARKER, SERVICE_HOST

WINDOWS_HOSTS = (
    "# Copyright (c) 1993-2009 Microsoft Corp.\r\n"
    "#\r\n"
    "192.168.178.75 host.docker.internal\r\n"
    "127.0.0.1 kubernetes.docker.internal\r\n"
    "# End of section\r\n"
)


def test_add_keeps_everything_and_uses_crlf() -> None:
    out = hosts.add_entries(WINDOWS_HOSTS, SERVICE_HOST)
    assert out.startswith(WINDOWS_HOSTS)
    assert out[len(WINDOWS_HOSTS) :] == (
        f"127.0.0.1 {SERVICE_HOST}  {HOSTS_MARKER}\r\n::1 {SERVICE_HOST}  {HOSTS_MARKER}\r\n"
    )


def test_add_is_idempotent() -> None:
    once = hosts.add_entries(WINDOWS_HOSTS, SERVICE_HOST)
    assert hosts.add_entries(once, SERVICE_HOST) == once


def test_add_to_file_without_final_newline() -> None:
    out = hosts.add_entries("127.0.0.1 localhost", SERVICE_HOST)
    assert out.splitlines()[0] == "127.0.0.1 localhost"
    assert len(hosts.marked_entries(out)) == 2


def test_remove_restores_the_original() -> None:
    assert hosts.remove_entries(hosts.add_entries(WINDOWS_HOSTS, SERVICE_HOST)) == WINDOWS_HOSTS


def test_remove_leaves_unmarked_lines_for_the_same_name() -> None:
    text = WINDOWS_HOSTS + f"0.0.0.0 {SERVICE_HOST}\r\n"
    assert hosts.remove_entries(text) == text


def test_foreign_entries_detects_conflicts_but_not_ours_or_comments() -> None:
    text = hosts.add_entries(
        WINDOWS_HOSTS + f"0.0.0.0 {SERVICE_HOST.upper()} # blocco\r\n# 1.2.3.4 {SERVICE_HOST}\r\n", SERVICE_HOST
    )
    assert hosts.foreign_entries(text, SERVICE_HOST) == [f"0.0.0.0 {SERVICE_HOST.upper()} # blocco"]


def test_mapped_names() -> None:
    assert hosts.mapped_names("1.2.3.4  a.example B.example # c") == ("1.2.3.4", ["a.example", "b.example"])
    assert hosts.mapped_names("# 1.2.3.4 a") is None
    assert hosts.mapped_names("   ") is None


def test_read_write_preserve_bytes_and_backup(tmp_path) -> None:
    path = tmp_path / "hosts"
    original = WINDOWS_HOSTS.encode() + b"# byte non UTF-8: \xe8\r\n"
    path.write_bytes(original)
    saved = hosts.backup(path, tmp_path / "bk", now=dt.datetime(2026, 11, 6, 14, 0, 0))
    assert saved.name == "hosts.20261106-140000-000000.bak"
    assert saved.read_bytes() == original

    text = hosts.read(path)
    hosts.write(path, hosts.add_entries(text, SERVICE_HOST))
    hosts.write(path, hosts.remove_entries(hosts.read(path)))
    assert path.read_bytes() == original
