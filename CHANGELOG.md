# Changelog

All notable changes to WMP Revival are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions: [CalVer](https://calver.org/) `YYYY.MM.PATCH`.

## [Unreleased]

### Added

- **Phase 2, from WMP's TOC to the Disc ID** (`toc.py`), verified on the three real CDs.
- **MusicBrainz lookups** through the public API: client limited to one request per second,
  User-Agent with a contact from `config.toml`, retries on 503/429 and an SQLite cache; the base
  URL is configurable, to switch to a local mirror without code changes.
- **Disc selection** (`matching.py`): exact Disc ID, then TOC search with a maximum difference
  of 3 s per track; discards unrelated discs proposed by MusicBrainz.
- **`wmprevival serve`: the server answers WMP with MusicBrainz data.** MDR-CD 4.0 for the
  automatic CD lookup (`GetMDRCD`), results page for "Find album info" on CDs and files
  (bridge page, MDQ parsed with defusedxml), Jinja2 pages with automatic escaping and ES3
  JavaScript written from scratch, anti-XSS tests, never a 5xx. Digital releases are offered
  first for files. `--no-auto` leaves only the manual choices. If WMP rejects the data, the
  page shows the error and reports it to `/FAI/log`.
- `scripts/serve.cmd` for a test session with the server (setup, serve, teardown).
- **Automatic lookup of library files**: `POST /cdinfo/GetMDRCD.aspx` with the MDQ in XML
  (album at query level, several tracks) gets an MDR with the matched tracks and their
  `trackRequestID`; store markers such as `[Explicit]` no longer prevent the comparison.
- `wmprevival mb-lookup <TOC>` shows candidates and choice; `tools/record_mb_fixture.py` records
  MusicBrainz responses as fixtures.

- `record` reports every TLS ClientHello: if no request follows, the client rejected the
  certificate.
- `scripts/capture.cmd` for a full capture session from an elevated prompt.
- `fixture --drop-upstream-body` removes the Microsoft server's pages and scripts from the
  fixtures. First real fixtures in `tests/fixtures/captures/` (three `GetMDRCD`, FAI on a CD
  and FAI on a file: GET with `requestid` and POST with the MDQ-CD), with tests of the observed
  format.
- `CLAUDE.md`: results of the first capture (CD query, "Gracenote provider has been removed"
  response, FAI page in IE7 mode, `WriteNamesEx(2, "", mdr, true)`).

### Changed

- The whole repository (code, comments, CLI and page messages, docs) is now in English. The
  scripts are renamed (`cattura.cmd` → `capture.cmd`, `servi.cmd` → `serve.cmd`) and the test
  fixtures have English names.

### Fixed

- **The "Find album info" page of a CD no longer writes to other files.** It called
  `WriteNamesEx(2, "", …)` like Microsoft's page, and WMP applied the data to the previous
  request (the FLAC of *Arrival* became *Panic* from *Joy*). It now targets the CD by TOC
  (`CD_BY_TOC`).
- A file carrying another album's data in the WMP library ("suspect" tag: the file name
  `NN. Artist - Title` says otherwise) is found again from its file name; in that case the
  automatic lookup writes nothing.
- Pages and scripts are loaded at startup: a server left running during an update no longer
  serves pages incompatible with its code.
- **WMP no longer renames or moves files**: `fRenameRegroupFiles` is false; `serve --rename-files`
  turns it back on for those who want files rearranged into `Artist\Album`.
- **Different files of the same album no longer receive another track's data.** WMP keeps a
  single MDR document per `WMCollectionID` and, in the automatic lookup, reused the tracks of a
  previous document with the `trackRequestID`s of the new request (three P!nk files all became
  "Long Way to Happy"). Every response for files now has its own `WMCollectionID`; the release
  stays in `dataProviderParams`.
- Albums whose tag repeats the artist (WMP sends "ABBA, Abba") are found: the lookup also tries
  the collapsed form and the album artist.
- Already damaged tags are repaired: if the tag's title does not fit the duration, the lookup
  and the matching use the title taken from the file name, trying up to three files.
- `serve` and `record` no longer print an asyncio traceback when WMP closes the connection
  first (ProactorEventLoop on Windows); other errors stay visible.
- MusicBrainz's special `[no label]` label no longer ends up in the data for WMP.
- The CLI no longer stops with `UnicodeEncodeError` when output goes to a cp1252 console:
  characters that cannot be represented become `?`.
- **A selection in the server's console window no longer freezes the server.** With QuickEdit,
  Windows blocks console writes while text is selected, and WMP's lookups hung; `serve` and
  `record` now print from a background thread.
- `status` warns when WMP's "Rename music files…" or "Rearrange music…" options are on, since
  they make WMP rename and move files according to its library.

## [2026.11.0] - 2026-11-06

### Added

- **Phase 0, TLS infrastructure and redirect.** `wmprevival setup` creates a local CA with
  name constraints on `musicmatch-ssl.xboxlive.com` (IP addresses excluded), issues the
  server certificate, installs the CA in the root store (computer or user) and adds the
  `127.0.0.1`/`::1` lines to the hosts file after backing it up. `teardown` undoes everything
  from `state.json`, even after an interrupted `setup`. `status` shows the CA, hosts, port and
  latest capture. `setup --dry-run` touches nothing.
- **Phase 1, recorder.** `wmprevival record` listens over HTTPS on `127.0.0.1` and `::1` only
  (other addresses only with `--allow-lan`), saves every request to a timestamped JSON file
  (raw query, headers in order, body as base64 and text) and answers 404 with no body.
  With `--forward` it forwards to the real Microsoft server, resolved through public DNS to
  bypass the hosts file, verifies the certificate against the original name, records the
  response and returns it to WMP. Local forwarding errors become 404, never 5xx.
- Captures keep the **raw** query (`+` stays `+`, as in WMP's TOC), decode body text with the
  declared charset (including the real server's HTML pages, even when compressed) and split
  `application/x-www-form-urlencoded` forms, so the MDQ sent by FAI's bridge page in the `mdq`
  field is readable in plain text.
- `wmprevival fixture` turns a capture into a fixture, replacing GUIDs with stable
  placeholders.
- `tools/wmdb_strings.py` to compare the strings of two copies of the WMP library database.
- Test zero documented in `CLAUDE.md`: format of the TOC saved by WMP
  (`track count+offsets…+lead-out` in hexadecimal, offsets including the 150 pregap sectors).
- Documented in `CLAUDE.md`: FAI's HTML flow (bridge page with
  `window.external.GetMDQByRequestID` and POST of the `mdq` field), the
  `IWMPCDDVDWizardExternal` interface recovered from the `wmp.dll` type library (`WriteNamesEx`,
  `WriteNames`, `GetMDQByRequestID`, enum `WMP_WRITENAMESEX_TYPE`) and the rule that HTML pages
  are generated only with auto-escaping templates.
