# WMP Revival

Local metadata server for **Windows Media Player Legacy** (WMP 12) on Windows 11.
WMP still asks `musicmatch-ssl.xboxlive.com` for album information, and today that service
answers with no data. WMP Revival stands in for it, on this PC only, and answers with data
from MusicBrainz (with Discogs as a fallback). No Windows file is modified.

Status: **phases 0-2**; phases 3-4 implemented (`wmprevival serve`), still being tested on WMP.
Cover art and Discogs not yet. Details and the full plan in [CLAUDE.md](CLAUDE.md).

## Requirements

- Windows 11, 32- or 64-bit WMP. WMP Legacy is an optional Windows feature:
  Settings → Apps → Optional features.
- [uv](https://docs.astral.sh/uv/) and Python 3.13
- An **elevated** prompt for `setup` and `teardown` (hosts file and the computer's
  certificate store)

```powershell
uv sync
uv run wmprevival --help
```

## Installing on another PC

1. Install uv: `winget install astral-sh.uv`. uv downloads Python 3.13 by itself.
2. Clone the repository and run `uv sync` inside it.
3. Create `%LOCALAPPDATA%\WMPRevival\config.toml` with a contact for MusicBrainz
   (see [MusicBrainz](#musicbrainz)).
4. Run `uv run wmprevival setup` from an elevated prompt **of the same user who uses WMP**:
   the configuration, the PKI and WMP's options (HKCU) are per-user. If that user is a
   standard account and elevation uses a different account, it will not work as is.
   Never copy the `pki` folder between PCs: every PC creates its own CA.
5. Keep `uv run wmprevival serve` running while you use WMP. It needs no admin rights;
   without it, WMP finds the port closed and simply gets no metadata.
6. In WMP, turn off "Rename music files using rip music settings" and "Rearrange music in
   rip music folder, using rip music settings" (Organize → Options → Library).
   `status` warns you if they are on.

To uninstall: `uv run wmprevival teardown --purge` from an elevated prompt.

## What `setup` does

1. Creates in `%LOCALAPPDATA%\WMPRevival\pki` a local CA **restricted with name constraints**
   to `musicmatch-ssl.xboxlive.com`, and a server certificate for that name. The CA's private
   key signs that certificate and is never saved; the server's key is readable by your user
   only. See [How far the name constraints go](#how-far-the-name-constraints-go).
2. Installs the CA among the "Trusted Root Certification Authorities" (of the computer, or of
   the user with `--store user`).
3. Backs up the hosts file and adds `127.0.0.1` and `::1` for
   `musicmatch-ssl.xboxlive.com`, with the `# wmprevival` marker.

Every step is recorded in `state.json`, so `teardown` can undo even an interrupted `setup`.
`setup --dry-run` shows what it would do without touching anything.

### How far the name constraints go

Measured with the Windows chain engine and its SSL policy, the same checks Schannel clients
use ([tests/test_windows_trust.py](tests/test_windows_trust.py), run in CI also with the CA in
the machine's Root store):

| Certificate signed by the CA | Windows |
| --- | --- |
| SAN `musicmatch-ssl.xboxlive.com` or a subdomain | accepted |
| SAN with another name, e.g. `example.com` | rejected (name constraint) |
| SAN with an IP address | rejected (name constraint) |
| **no SAN**, the name only in the subject CN, any site or IP | **accepted** |

Without a SAN, Windows' SSL policy matches the host name against the CN, and the name
constraints are not applied to the CN. So the constraints alone do not make the CA harmless:
whoever had the CA's private key could sign certificates that Windows programs accept for any
site. That is why `setup` never saves it: the key exists only in memory while `setup` signs
the server certificate, and the CA can sign nothing else afterwards. A `ca.key` left by an
older version is deleted by the next `setup` (`status` points it out).

The server certificate is valid for 825 days and the CA for 30 days more. `status` warns 30
days before the expiry; with an expired certificate `serve` and `record` don't start, and
`setup` creates a new CA and certificate and replaces the old CA in the store.

```powershell
uv run wmprevival setup --dry-run
uv run wmprevival setup          # asks for confirmation
uv run wmprevival status
uv run wmprevival teardown       # removes the CA and the hosts lines; --purge also deletes the PKI
```

> Microsoft Defender sometimes flags hosts file changes that involve Microsoft domains
> (`SettingsModifier:Win32/HostsFileHijack`). If that happens, the line is removed and WMP
> talks to the real server again: `status` shows it.

## Recorder

```powershell
uv run wmprevival record                 # records and answers 404
uv run wmprevival record --forward       # records and forwards to the real Microsoft server
```

Every request becomes a JSON file in `%LOCALAPPDATA%\WMPRevival\captures`, with the **raw**
method, path and query, the headers in the order they arrive, the body (base64 and text)
and, with `--forward`, the real server's response too.

`--forward` resolves the name with a public DNS (1.1.1.1, 9.9.9.9) instead of the hosts file
and connects to the real address, verifying the certificate against the original name. Use it
only in capture sessions: the TOCs and audio fingerprints sent by WMP reach Microsoft.

To turn a capture into a test fixture, removing the GUIDs:

```powershell
uv run wmprevival fixture --drop-upstream-body <capture.json> tests/fixtures/captures/<name>.json
```

## MusicBrainz

Lookups use the public MusicBrainz API (at most one request per second, with a local cache).
It requires a contact for the User-Agent, in `%LOCALAPPDATA%\WMPRevival\config.toml`:

```toml
[musicbrainz]
contact = "address@example.org"
# For a local mirror (musicbrainz-docker), same API:
# base_url = "http://localhost:5000/ws/2"
# min_interval = 0
```

Try it without WMP, with the TOC as WMP writes it:

```powershell
uv run wmprevival mb-lookup "C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A"
```

## Server with MusicBrainz data

```powershell
uv run wmprevival serve            # answers WMP (after setup), records everything in captures
uv run wmprevival serve --no-auto  # no automatic CD lookup: only "Find album info"
```

Full session from an elevated prompt: `scripts\serve.cmd` (setup, serve, teardown).

## Tools

- `tools/wmdb_strings.py`: compares the strings of two copies of the WMP library database
  (proprietary format, not ESE). Work on copies, with WMP closed.

## License

MIT. MusicBrainz data: core data CC0, supplementary data CC BY-NC-SA.
