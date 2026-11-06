"""Riga di comando: ``setup``, ``teardown``, ``status``, ``record``, ``fixture``."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import io
import json
import shutil
import socket
import sys
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
    help="Server locale di metadati per Windows Media Player Legacy.",
)
console = Console()


class StoreChoice(StrEnum):
    machine = "machine"
    user = "user"


def _version(value: bool) -> None:
    if value:
        console.print(f"wmprevival {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Mostra la versione."),
) -> None:
    # Con l'output rediretto su una console cp1252, simboli come ✓ → ← farebbero fallire
    # la stampa (UnicodeEncodeError) dopo che il lavoro è già stato fatto: meglio un '?'.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]Errore:[/red] {message}")
    return typer.Exit(1)


def _require_admin(what: str) -> None:
    if not winsys.is_admin():
        raise _fail(f"{what} richiede un terminale aperto come amministratore.")


def _expiry(cert_date: dt.datetime) -> str:
    return cert_date.astimezone().strftime("%Y-%m-%d")


@app.command()
def setup(
    store: StoreChoice = typer.Option(StoreChoice.machine, help="Archivio radice: computer o utente corrente."),
    rotate: bool = typer.Option(False, help="Rigenera CA e certificato anche se esistono già."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra cosa farebbe, senza toccare niente."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma."),
) -> None:
    """Crea la CA locale, la installa e reindirizza il servizio di WMP su questo PC."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    state = InstallState.load(paths.state_file)
    create = rotate or not files.all_exist()

    console.print("[bold]Piano[/bold]")
    if create:
        console.print(f"  1. crea CA (name constraints: solo {SERVICE_HOST}) e certificato in {paths.pki}")
    else:
        until = _expiry(tls.load_server_cert(files).not_valid_after_utc)
        console.print(f"  1. riusa la PKI in {paths.pki} (certificato valido fino al {until})")
    console.print(f"  2. installa la CA tra le radici attendibili ({store.value})")
    console.print(f"  3. backup di {paths.hosts_file} in {paths.backups}, poi aggiunge:")
    for line in hosts.add_entries("", SERVICE_HOST).splitlines():
        console.print(f"       {line}")
    console.print("  4. svuota la cache DNS")
    if dry_run:
        console.print("[yellow]--dry-run: nessuna modifica.[/yellow]")
        return

    _require_admin("setup")
    if not yes:
        typer.confirm("Procedo?", abort=True)

    if create:
        ca = tls.create_ca(SERVICE_HOST)
        tls.write_pki(files, ca, tls.issue_server_cert(ca, SERVICE_HOST), winsys.restrict_to_current_user)
        console.print("[green]✓[/green] PKI creata")

    thumb = tls.thumbprint(tls.load_ca_cert(files))
    if state.ca_thumbprint and state.ca_store and (state.ca_thumbprint, state.ca_store) != (thumb, store.value):
        with contextlib.suppress(winsys.SystemCommandError):
            winsys.remove_root(state.ca_thumbprint, state.ca_store)
            console.print(f"[green]✓[/green] tolta la CA precedente {state.ca_thumbprint}")
    winsys.add_root(files.ca_cert, store.value)
    state.ca_thumbprint, state.ca_store = thumb, store.value
    state.save(paths.state_file)
    console.print(f"[green]✓[/green] CA installata ({store.value}), impronta {thumb}")

    text = hosts.read(paths.hosts_file)
    for line in hosts.foreign_entries(text, SERVICE_HOST):
        console.print(f"[yellow]Attenzione:[/yellow] hosts contiene già una riga non nostra: {line}")
    saved = hosts.backup(paths.hosts_file, paths.backups)
    state.hosts_backups.append(str(saved))
    state.save(paths.state_file)
    hosts.write(paths.hosts_file, hosts.add_entries(text, SERVICE_HOST))
    state.hosts_modified = True
    state.installed_at = dt.datetime.now().astimezone().isoformat()
    state.save(paths.state_file)
    console.print(f"[green]✓[/green] hosts aggiornato (backup: {saved})")

    winsys.flush_dns()
    console.print("[green]✓[/green] cache DNS svuotata. Prossimo passo: [bold]wmprevival record[/bold]")


@app.command()
def teardown(
    purge: bool = typer.Option(False, help="Cancella anche CA, certificato e chiavi private."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma."),
) -> None:
    """Annulla setup: toglie le righe di hosts e la CA dall'archivio."""
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
        console.print("Niente da annullare.")
        return
    console.print("[bold]Piano[/bold]")
    for t, s in installed:
        console.print(f"  - toglie la CA {t} dall'archivio {s}")
    for line in marked:
        console.print(f"  - toglie da hosts: {line}")
    if purge:
        console.print(f"  - cancella {paths.pki}")

    _require_admin("teardown")
    if not yes:
        typer.confirm("Procedo?", abort=True)

    if marked:
        saved = hosts.backup(paths.hosts_file, paths.backups)
        state.hosts_backups.append(str(saved))
        hosts.write(paths.hosts_file, hosts.remove_entries(text))
        console.print(f"[green]✓[/green] righe tolte da hosts (backup: {saved})")
    state.hosts_modified = False
    state.save(paths.state_file)

    for t, s in installed:
        winsys.remove_root(t, s)
        console.print(f"[green]✓[/green] CA {t} tolta ({s})")
    state.ca_thumbprint = state.ca_store = state.installed_at = None
    state.save(paths.state_file)

    if purge and paths.pki.exists():
        shutil.rmtree(paths.pki)
        console.print(f"[green]✓[/green] {paths.pki} cancellata")
    winsys.flush_dns()


def _port_open(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


@app.command()
def status(port: int = typer.Option(443, help="Porta del registratore/server.")) -> None:
    """Mostra lo stato di CA, hosts, porta, criteri di WMP e ultima cattura."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    table = Table(show_header=False, box=None)

    if files.all_exist():
        ca = tls.load_ca_cert(files)
        server = tls.load_server_cert(files)
        thumb = tls.thumbprint(ca)
        table.add_row("PKI", f"{paths.pki}  (server valido fino al {_expiry(server.not_valid_after_utc)})")
        stores = [s for s in STORES if winsys.has_root(thumb, s)]
        table.add_row("CA nell'archivio", ", ".join(stores) if stores else "[yellow]no[/yellow]")
    else:
        table.add_row("PKI", "[yellow]assente[/yellow] (wmprevival setup)")

    text = hosts.read(paths.hosts_file)
    marked = hosts.marked_entries(text)
    table.add_row("hosts", "\n".join(marked) if marked else "[yellow]nessuna riga wmprevival[/yellow]")
    for line in hosts.foreign_entries(text, SERVICE_HOST):
        table.add_row("", f"[yellow]riga non nostra:[/yellow] {line}")

    listening = [h for h in ("127.0.0.1", "::1") if _port_open(h, port)]
    table.add_row(f"porta {port}", ", ".join(listening) + " in ascolto" if listening else "libera")

    policies = winsys.wmp_policies()
    table.add_row(
        "criteri WMP",
        ", ".join(f"{k}={v}" for k, v in policies.items()) if policies else "nessun blocco impostato",
    )

    for name, label in winsys.wmp_file_organizing().items():
        table.add_row("[yellow]WMP sposta i file[/yellow]", f"{name}: «{label}» è attiva (Opzioni > Catalogo)")

    captures = sorted(paths.captures.glob("*.json")) if paths.captures.exists() else []
    table.add_row("catture", f"{len(captures)}, ultima: {captures[-1].name}" if captures else "nessuna")
    console.print(table)


def _print_record(record: dict[str, Any], dest: Path) -> None:
    req = record["request"]
    upstream = f" ← server {record['upstream']['status']}" if "upstream" in record else ""
    error = f" [red]({record['upstream_error']})[/red]" if "upstream_error" in record else ""
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    console.print(
        f"[dim]{stamp}[/dim] {req['method']} {req['raw_path']} → {record['response']['status']}{upstream}{error}"
        f"  [dim]{dest.name}[/dim]",
        markup=True,
        highlight=False,
    )


@app.command()
def record(
    port: int = typer.Option(443, help="Porta HTTPS."),
    listen: list[str] = typer.Option(["127.0.0.1", "::1"], help="Indirizzi su cui ascoltare (ripetibile)."),
    allow_lan: bool = typer.Option(False, "--allow-lan", help="Permette indirizzi diversi dal loopback."),
    forward_: bool = typer.Option(
        False, "--forward", help="Inoltra al server Microsoft vero e registra anche la risposta."
    ),
    dns_server: list[str] = typer.Option(
        list(forward.PUBLIC_RESOLVERS), "--dns", help="DNS pubblici per --forward (ripetibile)."
    ),
) -> None:
    """Registra le richieste di WMP in %LOCALAPPDATA%\\WMPRevival\\captures."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    if not files.all_exist():
        raise _fail("PKI assente: esegui prima 'wmprevival setup'.")
    try:
        addresses = recorder.check_listen(listen, allow_lan)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    if not hosts.marked_entries(hosts.read(paths.hosts_file)):
        console.print("[yellow]Attenzione:[/yellow] hosts non reindirizza il servizio: WMP non arriverà qui.")

    def hello(sni: str | None) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        console.print(
            f"[dim]{stamp} TLS ClientHello (SNI {sni or '-'}). Se non segue una richiesta, il client "
            "ha rifiutato il certificato.[/dim]",
            highlight=False,
        )

    ctx = recorder.server_context(files.server_cert, files.server_key, on_hello=hello)

    async def run() -> None:
        fwd: forward.Forwarder | None = None
        if forward_:
            upstream = await forward.resolve_direct(SERVICE_HOST, dns_server)
            via = ", ".join(dns_server)
            console.print(f"[bold]--forward[/bold]: {SERVICE_HOST} → {', '.join(upstream)} (via {via})")
            console.print("[yellow]TOC e impronte audio inviati da WMP arriveranno a Microsoft.[/yellow]")
            fwd = forward.Forwarder(SERVICE_HOST, upstream)
        async with fwd if fwd else contextlib.nullcontext():
            rec = recorder.Recorder(paths.captures, forwarder=fwd, on_record=_print_record)
            runner, started, errors = await recorder.start(rec, addresses, port, ctx)
            for err in errors:
                console.print(f"[yellow]Non in ascolto su {err}[/yellow]")
            console.print(f"In ascolto su {', '.join(f'{h}:{port}' for h in started)}. Ctrl+C per fermare.")
            console.print(f"Catture in {paths.captures}")
            await recorder.serve_forever(runner)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("Fermato.")
    except (OSError, forward.UnsafeUpstreamError) as exc:
        raise _fail(str(exc)) from exc


@app.command()
def serve(
    port: int = typer.Option(443, help="Porta HTTPS."),
    listen: list[str] = typer.Option(["127.0.0.1", "::1"], help="Indirizzi su cui ascoltare (ripetibile)."),
    allow_lan: bool = typer.Option(False, "--allow-lan", help="Permette indirizzi diversi dal loopback."),
    no_auto: bool = typer.Option(
        False, "--no-auto", help="Niente ricerca automatica dei CD: solo 'Trova informazioni album'."
    ),
    rename_files: bool = typer.Option(
        False,
        "--rename-files",
        help="Lascia che WMP rinomini e sposti i file in Artista\\Album dopo 'Trova informazioni album'.",
    ),
) -> None:
    """Risponde a WMP con i dati di MusicBrainz, registrando ogni richiesta e risposta."""
    paths = Paths.from_env()
    files = tls.PkiFiles.in_dir(paths.pki)
    if not files.all_exist():
        raise _fail("PKI assente: esegui prima 'wmprevival setup'.")
    try:
        addresses = recorder.check_listen(listen, allow_lan)
        mb_config = Config.load(paths.config_file).musicbrainz.validate()
    except (ValueError, ConfigError) as exc:
        raise _fail(f"{exc}\n  (configurazione: {paths.config_file})") from exc
    if not hosts.marked_entries(hosts.read(paths.hosts_file)):
        console.print("[yellow]Attenzione:[/yellow] hosts non reindirizza il servizio: WMP non arriverà qui.")
    ctx = recorder.server_context(files.server_cert, files.server_key)

    def show(record_: dict[str, Any], dest: Path) -> None:
        req = record_["request"]
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        extra = ""
        if req["path"].lower() == "/fai/log":
            extra = f"  [bold]{urllib.parse.unquote(req['query_string'])}[/bold]"
        console.print(
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
                    console.print("[yellow]--rename-files:[/yellow] WMP rinominerà e sposterà i file.")
                if no_auto:
                    console.print("[yellow]--no-auto:[/yellow] ricerca automatica dei CD disattivata.")
                rec = recorder.Recorder(paths.captures, on_record=show, responder=responder)
                runner, started, errors = await recorder.start(rec, addresses, port, ctx)
                for err in errors:
                    console.print(f"[yellow]Non in ascolto su {err}[/yellow]")
                console.print(f"Rispondo a WMP su {', '.join(f'{h}:{port}' for h in started)} con MusicBrainz")
                console.print(f"({mb_config.base_url}). Ctrl+C per fermare. Catture in {paths.captures}")
                await recorder.serve_forever(runner)
        finally:
            cache.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("Fermato.")
    except OSError as exc:
        raise _fail(str(exc)) from exc


@app.command("mb-lookup")
def mb_lookup(
    toc_value: str = typer.Argument(..., metavar="TOC", help="TOC nel formato di WMP, es. C+96+5370+...+3AC1A"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignora la cache locale delle risposte."),
) -> None:
    """Cerca un CD su MusicBrainz partendo dal TOC di WMP (Disc ID, poi ricerca per TOC)."""
    paths = Paths.from_env()
    try:
        mb_config = Config.load(paths.config_file).musicbrainz.validate()
        toc = Toc.from_wmp(toc_value)
    except (ConfigError, TocError) as exc:
        raise _fail(f"{exc}\n  (configurazione: {paths.config_file})") from exc

    console.print(f"Disc ID {toc.disc_id}, {toc.track_count} tracce, via {mb_config.base_url}")

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
        console.print(f"[yellow]CD stub (dati anonimi):[/yellow] {stub.get('artist', '')} - {stub.get('title', '')}")
    table = Table("", "esatto", "artista - titolo", "data", "paese", "max Δ", "media Δ", "release")
    for cand in found[:10]:
        delta = f"{cand.max_diff_ms / 1000:.1f} s" if cand.max_diff_ms is not None else "?"
        mean = f"{cand.mean_diff_ms / 1000:.1f} s" if cand.mean_diff_ms is not None else "?"
        table.add_row(
            "[green]✓[/green]" if cand.accepted else "[red]✗[/red]",
            "sì" if cand.exact else "",
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
    verdict = (
        f"[green]scelta:[/green] {best.artist} - {best.title}" if best else "[yellow]nessuna scelta affidabile[/yellow]"
    )
    console.print(f"{verdict}  ({requests} richieste a MusicBrainz)")


@app.command()
def fixture(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, help="File di cattura."),
    dest: Path = typer.Argument(..., dir_okay=False, help="Fixture da scrivere."),
    drop_upstream_body: bool = typer.Option(
        False, "--drop-upstream-body", help="Toglie il corpo della risposta del server vero (materiale Microsoft)."
    ),
) -> None:
    """Copia una cattura come fixture di test, con i GUID sostituiti da segnaposto."""
    data = json.loads(source.read_text(encoding="utf-8"))
    if drop_upstream_body:
        data = capture.drop_upstream_body(data)
    result, count, warnings = capture.anonymize(data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for warning in warnings:
        console.print(f"[yellow]Attenzione:[/yellow] {warning}")
    console.print(f"[green]✓[/green] {dest} ({count} GUID sostituiti)")
