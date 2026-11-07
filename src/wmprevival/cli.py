"""Command line: ``setup``, ``teardown``, ``status``, ``record``, ``fixture``."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import io
import json
import queue
import shutil
import socket
import sys
import threading
import urllib.parse
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from wmprevival import __version__, capture, forward, hosts, matching, recorder, tls, winsys
from wmprevival.config import Config, ConfigError
from wmprevival.musicbrainz import MusicBrainzClient, MusicBrainzError, ResponseCache
from wmprevival.paths import SERVICE_HOST, Paths
from wmprevival.server import MetadataResponder
from wmprevival.service import MetadataService
from wmprevival.state import STORES, InstallState
from wmprevival.toc import Toc, TocError

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local metadata server for Windows Media Player Legacy.",
)
console = Console()


class BackgroundPrinter:
    """Prints to the console from a separate thread, never blocking the caller.

    On Windows, selecting text in the window (QuickEdit) suspends whoever writes to that
    console for as long as the selection lasts: on 2026-11-06 the server stopped halfway
    through a search after the user had copied a log line. Lines are now queued and the
    server keeps answering WMP.
    """

    def __init__(self, target: Console) -> None:
        self._target = target
        self._queue: queue.SimpleQueue[tuple[tuple[Any, ...], dict[str, Any]]] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="console", daemon=True)
        self._thread.start()

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._queue.put((args, kwargs))

    def _run(self) -> None:
        while True:
            args, kwargs = self._queue.get()
            with contextlib.suppress(Exception):
                self._target.print(*args, **kwargs)


_background: BackgroundPrinter | None = None


def background_print(*args: Any, **kwargs: Any) -> None:
    global _background
    if _background is None:
        _background = BackgroundPrinter(console)
    _background.print(*args, **kwargs)


class StoreChoice(StrEnum):
    machine = "machine"
    user = "user"


def _version(value: bool) -> None:
    if value:
        console.print(f"wmprevival {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show the version."),
) -> None:
    # With output redirected to a cp1252 console, symbols like ✓ → ← would make printing
    # fail (UnicodeEncodeError) after the work is already done: a '?' is better.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]Error:[/red] {message}")
    return typer.Exit(1)


def _require_admin(what: str) -> None:
    if not winsys.is_admin():
        raise _fail(f"{what} needs a terminal opened as administrator.")


def _expiry(cert_date: dt.datetime) -> str:
    return cert_date.astimezone().strftime("%Y-%m-%d")


@app.command()
def setup(
    store: StoreChoice = typer.Option(StoreChoice.machine, help="Root store: machine or current user."),
    rotate: bool = typer.Option(False, help="Regenerate the CA and certificate even if they already exist."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what it would do, without touching anything."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
) -> None:
    """Create the local CA, install it and redirect the WMP service to this PC."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    state = InstallState.load(paths.state_file)
    create = rotate or not files.all_exist()

    console.print("[bold]Plan[/bold]")
    if create:
        console.print(f"  1. create the CA (name constraints: {SERVICE_HOST} only) and certificate in {paths.pki}")
    else:
        until = _expiry(tls.load_server_cert(files).not_valid_after_utc)
        console.print(f"  1. reuse the PKI in {paths.pki} (certificate valid until {until})")
    console.print(f"  2. install the CA among the trusted roots ({store.value})")
    console.print(f"  3. back up {paths.hosts_file} to {paths.backups}, then add:")
    for line in hosts.add_entries("", SERVICE_HOST).splitlines():
        console.print(f"       {line}")
    console.print("  4. flush the DNS cache")
    if dry_run:
        console.print("[yellow]--dry-run: no changes.[/yellow]")
        return

    _require_admin("setup")
    if not yes:
        typer.confirm("Proceed?", abort=True)

    if create:
        ca = tls.create_ca(SERVICE_HOST)
        tls.write_pki(files, ca, tls.issue_server_cert(ca, SERVICE_HOST), winsys.restrict_to_current_user)
        console.print("[green]✓[/green] PKI created")

    thumb = tls.thumbprint(tls.load_ca_cert(files))
    if state.ca_thumbprint and state.ca_store and (state.ca_thumbprint, state.ca_store) != (thumb, store.value):
        with contextlib.suppress(winsys.SystemCommandError):
            winsys.remove_root(state.ca_thumbprint, state.ca_store)
            console.print(f"[green]✓[/green] removed the previous CA {state.ca_thumbprint}")
    winsys.add_root(files.ca_cert, store.value)
    state.ca_thumbprint, state.ca_store = thumb, store.value
    state.save(paths.state_file)
    console.print(f"[green]✓[/green] CA installed ({store.value}), thumbprint {thumb}")

    text = hosts.read(paths.hosts_file)
    for line in hosts.foreign_entries(text, SERVICE_HOST):
        console.print(f"[yellow]Warning:[/yellow] hosts already has a line that isn't ours: {line}")
    saved = hosts.backup(paths.hosts_file, paths.backups)
    state.hosts_backups.append(str(saved))
    state.save(paths.state_file)
    hosts.write(paths.hosts_file, hosts.add_entries(text, SERVICE_HOST))
    state.hosts_modified = True
    state.installed_at = dt.datetime.now().astimezone().isoformat()
    state.save(paths.state_file)
    console.print(f"[green]✓[/green] hosts updated (backup: {saved})")

    winsys.flush_dns()
    console.print("[green]✓[/green] DNS cache flushed. Next step: [bold]wmprevival record[/bold]")


@app.command()
def teardown(
    purge: bool = typer.Option(False, help="Also delete the CA, certificate and private keys."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
) -> None:
    """Undo setup: remove the hosts lines and the CA from the store."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    state = InstallState.load(paths.state_file)

    thumbs = {t for t in [state.ca_thumbprint] if t}
    if files.ca_cert.exists():
        thumbs.add(tls.thumbprint(tls.load_ca_cert(files)))
    installed = [(t, s) for t in sorted(thumbs) for s in STORES if winsys.has_root(t, s)]
    text = hosts.read(paths.hosts_file)
    marked = hosts.marked_entries(text)

    if not installed and not marked and not (purge and paths.pki.exists()):
        console.print("Nothing to undo.")
        return
    console.print("[bold]Plan[/bold]")
    for t, s in installed:
        console.print(f"  - remove the CA {t} from the {s} store")
    for line in marked:
        console.print(f"  - remove from hosts: {line}")
    if purge:
        console.print(f"  - delete {paths.pki}")

    _require_admin("teardown")
    if not yes:
        typer.confirm("Proceed?", abort=True)

    if marked:
        saved = hosts.backup(paths.hosts_file, paths.backups)
        state.hosts_backups.append(str(saved))
        hosts.write(paths.hosts_file, hosts.remove_entries(text))
        console.print(f"[green]✓[/green] lines removed from hosts (backup: {saved})")
    state.hosts_modified = False
    state.save(paths.state_file)

    for t, s in installed:
        winsys.remove_root(t, s)
        console.print(f"[green]✓[/green] CA {t} removed ({s})")
    state.ca_thumbprint = state.ca_store = state.installed_at = None
    state.save(paths.state_file)

    if purge and paths.pki.exists():
        shutil.rmtree(paths.pki)
        console.print(f"[green]✓[/green] {paths.pki} deleted")
    winsys.flush_dns()


def _port_open(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


@app.command()
def status(port: int = typer.Option(443, help="Recorder/server port.")) -> None:
    """Show the state of the CA, hosts, port, WMP policies and latest capture."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    table = Table(show_header=False, box=None)

    if files.all_exist():
        ca = tls.load_ca_cert(files)
        server = tls.load_server_cert(files)
        thumb = tls.thumbprint(ca)
        table.add_row("PKI", f"{paths.pki}  (server valid until {_expiry(server.not_valid_after_utc)})")
        stores = [s for s in STORES if winsys.has_root(thumb, s)]
        table.add_row("CA in store", ", ".join(stores) if stores else "[yellow]no[/yellow]")
    else:
        table.add_row("PKI", "[yellow]missing[/yellow] (wmprevival setup)")

    text = hosts.read(paths.hosts_file)
    marked = hosts.marked_entries(text)
    table.add_row("hosts", "\n".join(marked) if marked else "[yellow]no wmprevival lines[/yellow]")
    for line in hosts.foreign_entries(text, SERVICE_HOST):
        table.add_row("", f"[yellow]line not ours:[/yellow] {line}")

    listening = [h for h in ("127.0.0.1", "::1") if _port_open(h, port)]
    table.add_row(f"port {port}", ", ".join(listening) + " listening" if listening else "free")

    policies = winsys.wmp_policies()
    table.add_row(
        "WMP policies",
        ", ".join(f"{k}={v}" for k, v in policies.items()) if policies else "no block set",
    )

    for name, label in winsys.wmp_file_organizing().items():
        table.add_row("[yellow]WMP moves files[/yellow]", f"{name}: “{label}” is on (Options > Library)")

    captures = sorted(paths.captures.glob("*.json")) if paths.captures.exists() else []
    table.add_row("captures", f"{len(captures)}, latest: {captures[-1].name}" if captures else "none")
    console.print(table)


def _print_record(record: dict[str, Any], dest: Path) -> None:
    req = record["request"]
    upstream = f" ← server {record['upstream']['status']}" if "upstream" in record else ""
    error = f" [red]({record['upstream_error']})[/red]" if "upstream_error" in record else ""
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    background_print(
        f"[dim]{stamp}[/dim] {req['method']} {req['raw_path']} → {record['response']['status']}{upstream}{error}"
        f"  [dim]{dest.name}[/dim]",
        markup=True,
        highlight=False,
    )


@app.command()
def record(
    port: int = typer.Option(443, help="HTTPS port."),
    listen: list[str] = typer.Option(["127.0.0.1", "::1"], help="Addresses to listen on (repeatable)."),
    allow_lan: bool = typer.Option(False, "--allow-lan", help="Allow addresses other than loopback."),
    forward_: bool = typer.Option(
        False, "--forward", help="Forward to the real Microsoft server and record its response too."
    ),
    dns_server: list[str] = typer.Option(
        list(forward.PUBLIC_RESOLVERS), "--dns", help="Public DNS servers for --forward (repeatable)."
    ),
) -> None:
    """Record WMP's requests in %LOCALAPPDATA%\\WMPRevival\\captures."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    if not files.all_exist():
        raise _fail("PKI missing: run 'wmprevival setup' first.")
    try:
        addresses = recorder.check_listen(listen, allow_lan)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    if not hosts.marked_entries(hosts.read(paths.hosts_file)):
        console.print("[yellow]Warning:[/yellow] hosts doesn't redirect the service: WMP won't get here.")

    def hello(sni: str | None) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        background_print(
            f"[dim]{stamp} TLS ClientHello (SNI {sni or '-'}). If no request follows, the client "
            "rejected the certificate.[/dim]",
            highlight=False,
        )

    ctx = recorder.server_context(files.server_cert, files.server_key, on_hello=hello)

    async def run() -> None:
        fwd: forward.Forwarder | None = None
        if forward_:
            upstream = await forward.resolve_direct(SERVICE_HOST, dns_server)
            via = ", ".join(dns_server)
            console.print(f"[bold]--forward[/bold]: {SERVICE_HOST} → {', '.join(upstream)} (via {via})")
            console.print("[yellow]TOCs and audio fingerprints sent by WMP will reach Microsoft.[/yellow]")
            fwd = forward.Forwarder(SERVICE_HOST, upstream)
        async with fwd if fwd else contextlib.nullcontext():
            rec = recorder.Recorder(paths.captures, forwarder=fwd, on_record=_print_record)
            runner, started, errors = await recorder.start(rec, addresses, port, ctx)
            for err in errors:
                console.print(f"[yellow]Not listening on {err}[/yellow]")
            console.print(f"Listening on {', '.join(f'{h}:{port}' for h in started)}. Ctrl+C to stop.")
            console.print(f"Captures in {paths.captures}")
            await recorder.serve_forever(runner)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("Stopped.")
    except (OSError, forward.UnsafeUpstreamError) as exc:
        raise _fail(str(exc)) from exc


@app.command()
def serve(
    port: int = typer.Option(443, help="HTTPS port."),
    listen: list[str] = typer.Option(["127.0.0.1", "::1"], help="Addresses to listen on (repeatable)."),
    allow_lan: bool = typer.Option(False, "--allow-lan", help="Allow addresses other than loopback."),
    no_auto: bool = typer.Option(False, "--no-auto", help="No automatic CD lookup: only 'Find album info'."),
    rename_files: bool = typer.Option(
        False,
        "--rename-files",
        help="Let WMP rename and move files into Artist\\Album after 'Find album info'.",
    ),
) -> None:
    """Answer WMP with MusicBrainz data, recording every request and response."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    if not files.all_exist():
        raise _fail("PKI missing: run 'wmprevival setup' first.")
    try:
        addresses = recorder.check_listen(listen, allow_lan)
        mb_config = Config.load(paths.config_file).musicbrainz.validate()
    except (ValueError, ConfigError) as exc:
        raise _fail(f"{exc}\n  (configuration: {paths.config_file})") from exc
    if not hosts.marked_entries(hosts.read(paths.hosts_file)):
        console.print("[yellow]Warning:[/yellow] hosts doesn't redirect the service: WMP won't get here.")
    ctx = recorder.server_context(files.server_cert, files.server_key)

    def show(record_: dict[str, Any], dest: Path) -> None:
        req = record_["request"]
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        extra = ""
        if req["path"].lower() == "/fai/log":
            extra = f"  [bold]{urllib.parse.unquote(req['query_string'])}[/bold]"
        background_print(
            f"[dim]{stamp}[/dim] {req['method']} {req['path']} → {record_['response']['status']}{extra}"
            f"  [dim]{dest.name}[/dim]",
            highlight=False,
        )

    async def run() -> None:
        cache = ResponseCache(paths.mb_cache)
        try:
            async with MusicBrainzClient(mb_config, cache) as client:
                responder = MetadataResponder(MetadataService(client), auto=not no_auto, rename_files=rename_files)
                if rename_files:
                    console.print("[yellow]--rename-files:[/yellow] WMP will rename and move files.")
                if no_auto:
                    console.print("[yellow]--no-auto:[/yellow] automatic CD lookup disabled.")
                rec = recorder.Recorder(paths.captures, on_record=show, responder=responder)
                runner, started, errors = await recorder.start(rec, addresses, port, ctx)
                for err in errors:
                    console.print(f"[yellow]Not listening on {err}[/yellow]")
                console.print(f"Answering WMP on {', '.join(f'{h}:{port}' for h in started)} with MusicBrainz")
                console.print(f"({mb_config.base_url}). Ctrl+C to stop. Captures in {paths.captures}")
                await recorder.serve_forever(runner)
        finally:
            cache.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("Stopped.")
    except OSError as exc:
        raise _fail(str(exc)) from exc


@app.command("mb-lookup")
def mb_lookup(
    toc_value: str = typer.Argument(..., metavar="TOC", help="TOC in WMP's format, e.g. C+96+5370+...+3AC1A"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the local response cache."),
) -> None:
    """Look up a CD on MusicBrainz from WMP's TOC (Disc ID, then TOC search)."""
    paths = Paths.from_env()
    try:
        mb_config = Config.load(paths.config_file).musicbrainz.validate()
        toc = Toc.from_wmp(toc_value)
    except (ConfigError, TocError) as exc:
        raise _fail(f"{exc}\n  (configuration: {paths.config_file})") from exc

    console.print(f"Disc ID {toc.disc_id}, {toc.track_count} tracks, via {mb_config.base_url}")

    async def run() -> tuple[list[matching.Candidate], dict[str, Any] | None, int]:
        cache = None if no_cache else ResponseCache(paths.mb_cache)
        try:
            async with MusicBrainzClient(mb_config, cache) as client:
                exact = await client.lookup_discid(toc.disc_id)
                stub = exact if matching.is_cd_stub(exact) else None
                releases = [] if exact is None or stub else list(exact.get("releases", []))
                found = matching.candidates(toc, releases)
                if not any(c.exact for c in found):
                    found = matching.candidates(toc, releases + await client.search_toc(toc))
                return found, stub, client.requests_made
        finally:
            if cache is not None:
                cache.close()

    try:
        found, stub, requests = asyncio.run(run())
    except (MusicBrainzError, OSError) as exc:
        raise _fail(str(exc)) from exc

    if stub:
        console.print(f"[yellow]CD stub (anonymous data):[/yellow] {stub.get('artist', '')} - {stub.get('title', '')}")
    table = Table("", "exact", "artist - title", "date", "country", "max Δ", "mean Δ", "release")
    for cand in found[:10]:
        delta = f"{cand.max_diff_ms / 1000:.1f} s" if cand.max_diff_ms is not None else "?"
        mean = f"{cand.mean_diff_ms / 1000:.1f} s" if cand.mean_diff_ms is not None else "?"
        table.add_row(
            "[green]✓[/green]" if cand.accepted else "[red]✗[/red]",
            "yes" if cand.exact else "",
            f"{cand.artist} - {cand.title}",
            cand.date,
            cand.country,
            delta,
            mean,
            cand.release_id,
        )
    if found:
        console.print(table)
    best = next((c for c in found if c.accepted), None)
    verdict = f"[green]chosen:[/green] {best.artist} - {best.title}" if best else "[yellow]no reliable choice[/yellow]"
    console.print(f"{verdict}  ({requests} requests to MusicBrainz)")


@app.command()
def fixture(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, help="Capture file."),
    dest: Path = typer.Argument(..., dir_okay=False, help="Fixture to write."),
    drop_upstream_body: bool = typer.Option(
        False, "--drop-upstream-body", help="Drop the real server's response body (Microsoft material)."
    ),
) -> None:
    """Copy a capture as a test fixture, with GUIDs replaced by placeholders."""
    data = json.loads(source.read_text(encoding="utf-8"))
    if drop_upstream_body:
        data = capture.drop_upstream_body(data)
    result, count, warnings = capture.anonymize(data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for warning in warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    console.print(f"[green]✓[/green] {dest} ({count} GUIDs replaced)")
