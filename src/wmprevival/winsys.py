"""Windows system operations: certificate store, permissions, DNS, privileges.

All of them go through an injectable ``Runner``, so tests check the commands without
running them. certutil's messages are localized: only the exit code is looked at.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from wmprevival.state import Store

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


class SystemCommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], result: subprocess.CompletedProcess[str]) -> None:
        detail = (result.stderr or result.stdout or "").strip()
        super().__init__(f"{' '.join(cmd)} -> exit code {result.returncode}: {detail}")


def _checked(runner: Runner, cmd: Sequence[str]) -> None:
    result = runner(cmd)
    if result.returncode != 0:
        raise SystemCommandError(cmd, result)


def _store_args(store: Store) -> list[str]:
    return ["-user"] if store == "user" else []


def add_root(cert: Path, store: Store, runner: Runner = run) -> None:
    """Installs the CA among the trusted roots. With ``user`` Windows asks for confirmation on screen."""
    _checked(runner, ["certutil", *_store_args(store), "-addstore", "-f", "Root", str(cert)])


def remove_root(thumbprint: str, store: Store, runner: Runner = run) -> None:
    _checked(runner, ["certutil", *_store_args(store), "-delstore", "Root", thumbprint])


def has_root(thumbprint: str, store: Store, runner: Runner = run) -> bool:
    return runner(["certutil", *_store_args(store), "-store", "Root", thumbprint]).returncode == 0


def restrict_to_current_user(path: Path, runner: Runner = run) -> None:
    """Removes inherited permissions and leaves full control to the current user only."""
    if sys.platform != "win32":
        path.chmod(0o600)
        return
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    who = f"{domain}\\{user}" if domain else user
    _checked(runner, ["icacls", str(path), "/inheritance:r", "/grant:r", f"{who}:F"])


def flush_dns(runner: Runner = run) -> None:
    runner(["ipconfig", "/flushdns"])


WMP_POLICY_KEY = r"Software\Policies\Microsoft\WindowsMediaPlayer"
WMP_POLICY_VALUES = ("PreventCDDVDMetadataRetrieval", "PreventMusicFileMetadataRetrieval")


def wmp_policies() -> dict[str, object]:
    """Group policies that block metadata retrieval, if set (HKLM and HKCU)."""
    if sys.platform != "win32":
        return {}
    import winreg

    found: dict[str, object] = {}
    for hive_name, hive in (("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)):
        try:
            key = winreg.OpenKey(hive, WMP_POLICY_KEY)
        except OSError:
            continue
        with key:
            for name in WMP_POLICY_VALUES:
                with contextlib.suppress(OSError):
                    found[f"{hive_name}\\{name}"] = winreg.QueryValueEx(key, name)[0]
    return found


WMP_PREFERENCES_KEY = r"Software\Microsoft\MediaPlayer\Preferences"
# WMP library options that rename and move files according to the library data
# (seen on 2026-11-06: a file moved by hand was put back where the library said).
WMP_FILE_ORGANIZING = {
    "AutoRename": "Rename music files using rip music settings",
    "AutoOrganize": "Rearrange music in rip music folder, using rip music settings",
}


def wmp_file_organizing() -> dict[str, str]:
    """Active options that make WMP rename or move files (HKCU)."""
    if sys.platform != "win32":
        return {}
    import winreg

    active: dict[str, str] = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WMP_PREFERENCES_KEY)
    except OSError:
        return active
    with key:
        for name, label in WMP_FILE_ORGANIZING.items():
            with contextlib.suppress(OSError):
                if winreg.QueryValueEx(key, name)[0]:
                    active[name] = label
    return active


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
