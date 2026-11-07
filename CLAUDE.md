# WMP Revival — local metadata server for Windows Media Player Legacy

## Goal

Bring back automatic album info retrieval in **Windows Media Player Legacy**
(WMP 12) on Windows 11, without modifying a single Windows file.

WMP still works and is updated by Windows Update. The online service it used to
query for metadata is still reachable but **no longer returns any data**
(test zero, 2026-11-06: "Unknown album" over a successful HTTPS connection).
The project builds a **local server that
poses as that service** and answers with data from **MusicBrainz**
(main source) and **Discogs** (fallback).

```text
WMP (x86) ──HTTPS──> musicmatch-ssl.xboxlive.com
                     │  (hosts file → 127.0.0.1)
                     ▼
              local translator  (this project)
                     │
         ┌───────────┼──────────────────┐
         ▼           ▼                  ▼
   MusicBrainz mirror  Discogs API   Cover Art Archive
   (at home, Docker)   (token)       (cover art)
```

## Non-negotiable rules

1. **Never modify Windows/WMP binaries or resources** (`wmp.dll`,
   `wmploc.dll.mui`, etc.). They are catalog-signed and Windows Update
   overwrites them. Work only through **interoperability**: hosts file, local
   certificate, a server that answers.
2. **No Microsoft binaries in the repository**, and no pages, CSS or scripts
   from the Microsoft server either (e.g. `suggestions.js`, signed by Microsoft).
   Fixtures are made with `wmprevival fixture --drop-upstream-body`; our
   HTML/JS is written from scratch, following only the observed interface.
3. **Secrets stay out of the code.** The Discogs token is read from an
   environment variable (`DISCOGS_TOKEN`) or from a config file excluded from
   git. Never in logs, never in responses to WMP.
4. **No unlicensed sources.** Gracenote is not used (it requires commercial
   agreements). Only MusicBrainz (core data CC0, supplementary data
   CC BY-NC-SA, personal use) and Discogs (API with a personal token).
5. **The local CA must be restricted** with *name constraints* to
   `musicmatch-ssl.xboxlive.com` only. The CA private key stays outside the
   repo, with restricted permissions. The server listens **only on 127.0.0.1**
   (or on the LAN IP if and when explicitly decided).
6. **Respect API limits**: public MusicBrainz 1 request/second with an
   identifying User-Agent (`WMPRevival/<version> ( <contact> )`, contact from
   configuration, no email in the code); Discogs ~60 requests/minute
   authenticated. A local cache is mandatory.
7. Before implementing a format, **verify it on real captured traffic**.
   What follows comes from static analysis and must be confirmed.
8. **HTML pages are generated only with a template engine with automatic
   escaping** (Jinja2 with `autoescape=True`), never by concatenating strings.
   Titles, artists and any other data from MusicBrainz/Discogs are untrusted
   input. The pages run inside WMP with access to `window.external`
   (`IWMPCDDVDWizardExternal`, which writes to the library): **an XSS here has
   access to WMP**. So: data meant for a `<script>` only serialized as
   HTML-safe JSON (`|tojson`), no inline handlers (`onclick=...`) with data in
   them, no scripts or resources from other hosts, no `|safe` on external
   data. A test must prove that a title with `</script><script>` or
   `"onmouseover=` comes out harmless.

## User environment

Verified on the development PC on 2026-11-06.

- Windows 11 Pro **26H2, build 26300.9711** (the registry says "Windows 10 Pro"
  in `ProductName`, as always). The WMP files stay in the 12.0.26100.x series.
- **WMP in use: the 32-bit version** (`C:\Program Files (x86)\Windows Media Player\wmplayer.exe`
  12.0.26100.1, engine `C:\Windows\SysWOW64\wmp.dll` 12.0.26100.9549)
- 64-bit version present: `System32\wmp.dll` 12.0.26100.9711
- Feature `Media.WindowsMediaPlayer~~~~0.0.12.0` installed.
- CD drive: USB (external: must be plugged in for tests, drive F:).
- WSL2 (Ubuntu 24.04), Docker Desktop.
- Python via uv: 3.12, 3.13, 3.14, 3.15. **We use 3.13/3.14** (3.15 is there but
  compiled libraries, e.g. lxml, may not have wheels yet).
- Tools: MusicBrainz Picard 3.0.3 (local fork), foobar2000,
  Qobuz, git/gh, openssl (the one from Git, not on PATH). **EAC is not installed**:
  the reference Disc IDs are Picard's.
- The user's project style: CLI with Typer/Rich, pytest with real fixtures,
  ruff, mypy, CalVer `YYYY.MM.PATCH`, Docker, changelog.

## What we know from static analysis

### Service addresses

They live in the string table of `System32\it-IT\wmploc.dll.mui` (12.0.26100.9549).
The MUI checksum `1b62db44dabe2f2eae74868406bb2566` is the same across the
32-bit stub, the 64-bit one and the .mui (also verified on the development PC,
identical to the one on the work PC): both versions use the same addresses.
`SysWOW64\it-IT\wmploc.dll.mui` **does not exist**: the 32-bit WMP takes its
strings from the System32 .mui (it connects to the server anyway, see test zero).

```text
https://musicmatch-ssl.xboxlive.com/cdinfo/GetMDRCD.aspx?     ← CD (parameters appended at runtime)
https://musicmatch-ssl.xboxlive.com/FAI/default.aspx           ← "Find Album Info" for files
```

- A single host, all over **HTTPS**.
- `redir.metaservices.microsoft.com` is **no longer used** by this version.
- No metadata address is written in clear text in `wmp.dll`.

### The Microsoft server today (2026-11-06)

- `musicmatch-ssl.xboxlive.com` still resolves (CNAME to Akamai,
  `e87.dspb.akamaiedge.net`, IPv6 too). Certificate `CN=*.xboxlive.com`,
  issued by `Microsoft TLS G2 ECC CA OCSP 06`, TLS 1.3.
- With a plain GET: `FAI/default.aspx` → **200**, `cdinfo/GetMDRCD.aspx?`
  without parameters → **400**, root → 404.
- The home DNS sometimes times out on this name; 1.1.1.1 answers.

### Networking and certificates in `wmp.dll`

- It imports both **WinHTTP** (`WinHttpOpen`, `WinHttpSetOption`) and **WinINet**
  (`InternetOpenW`, `HttpOpenRequestW`, `HttpSendRequestW`).
- It does not call `CertGetCertificateChain` or
  `CertVerifyCertificateChainPolicy` directly: no obvious pinning, to be confirmed.

### Library database

- `%LOCALAPPDATA%\Microsoft\Media Player\CurrentDatabase_400.wmdb` and
  `LocalMLS_3.wmdb`. **Not ESE** (signature `a5 09 b6 f3`): proprietary format.
  Studied by comparing the strings of two copies (`tools/wmdb_strings.py`).
- Contains personal paths and file names: copies stay out of the repo.

### HTTP request (from `wmp.dll`)

- `Content-Type: text/xml` (`application/x-www-form-urlencoded` also exists in
  another part of the code, not necessarily for metadata)
- `User-Agent: %s` with prefix `Windows-Media-Player/` followed by the build
  (e.g. `Windows-Media-Player/12.0.26100.9549`)
- `Referer: %s` header present in the download code

### Query for CDs (DAI)

Templates found in `wmp.dll`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<DAI trackCount="%lu" toc="%s">
  <track number="%ld"><base>%s</base> ... <confirm>%s</confirm></track>
</DAI>
```

- `toc` holds the disc TOC. The format **stored in the library**
  (`Album.TOC`) is known from test zero, see below; it remains to be confirmed
  that it is identical to what travels in the request.
- `<base>` and `<confirm>` are per-track audio fingerprints. The code contains
  `No confirmation trace received for this track. Not doing fp confirmation`:
  **if the server sends no confirmation fingerprints, WMP skips the check**.
  The server can ignore them.
- To find out: whether the TOC arrives in the `GetMDRCD.aspx?` query string, in
  the DAI body, or both.

### Query for files (MDQ-CD, used by FAI)

```xml
<?xml version="1.0"?><METADATA><MDQ-CD></MDQ-CD></METADATA>
```

#### FAI is an HTML flow, not an XML call (2026-11-06)

`GET https://musicmatch-ssl.xboxlive.com/FAI/default.aspx` without parameters
returns an **HTML bridge page**:

1. the page script calls `window.external.GetMDQByRequestID("<id>")`, which
   returns the MDQ XML prepared by WMP;
2. it puts the XML into a hidden form field `mdq`;
3. it does a **POST to the same URL** (`application/x-www-form-urlencoded`).

In the page we received the id is empty because we passed no parameters: the
server fills it in **from the query string** sent by WMP. The parameter name
must be captured. The response to the POST will presumably be the results page,
which hands the MDR back to WMP through another `window.external` method
(see below).

#### The `window.external` object: `IWMPCDDVDWizardExternal`

From the type library embedded in `wmp.dll` (`TYPELIB` resource, `WMPLib`
`{6BF52A50-394A-11D3-B153-00C04F79FAA6}` v1.0), **identical** between x86 and x64.
Dispatch interface `IWMPCDDVDWizardExternal`
`{2D7EF888-1D3C-484A-A906-9F49D99BB344}`, "Not Public. Internal interface used by
Windows Media Player":

| DISPID | Method | Returns |
| --- | --- | --- |
| 0x2711 | `WriteNames(BSTR bstrTOC, BSTR bstrMetadata)` | void |
| 0x2712 | `ReturnToMainTask()` | void |
| 0x2717 | `WriteNamesEx(WMP_WRITENAMESEX_TYPE type, BSTR bstrTypeId, BSTR bstrMetadata, VARIANT_BOOL fRenameRegroupFiles)` | void |
| 0x2718 | `GetMDQByRequestID(BSTR bstrRequestID)` | BSTR (the MDQ) |
| 0x271a | `IsMetadataAvailableForEdit()` | VARIANT_BOOL |
| 0x271b | `EditMetadata()` | void |
| 0x2727 | `BuyCD(BSTR bstrTitle, BSTR bstrArtist, BSTR bstrAlbum, BSTR bstrUFID, BSTR bstrWMID)` | void |

Plus the properties shared by every WMP `external`: `version`,
`appColorLight/Medium/Dark/ButtonHighlight/ButtonShadow/ButtonHoverFace`
(propget), `OnColorChange` (propput).

```text
enum WMP_WRITENAMESEX_TYPE {
  WMP_WRITENAMES_TYPE_CD_BY_TOC        = 0,
  WMP_WRITENAMES_TYPE_CD_BY_CONTENT_ID = 1,
  WMP_WRITENAMES_TYPE_CD_BY_MDQCD      = 2,   // response to FAI (MDQ-CD)
  WMP_WRITENAMES_TYPE_DVD_BY_DVDID     = 3,
}
```

Hypotheses to verify against captures:

- the FAI results page calls
  `window.external.WriteNamesEx(2, <requestID>, <MDR-CD>, <rename?>)`;
- `WriteNames(toc, mdr)` / `WriteNamesEx(0, toc, ...)` are the CD path when
  the user picks manually ("Find album info" on a CD);
- `ReturnToMainTask()` closes the wizard.

The other `external` interfaces in the type library (`IWMPExternal`,
`IWMPBaseExternal`, `IWMPOfflineExternal` with `forceOnline()`,
`IWMPDiscoExternal`, `IWMPSubscriptionServiceExternal`) are about online stores
and services, not metadata. The local HTML pages in `wmploc.dll.mui`
(`BLOCKED_ALBUMINFO.HTM`, `OFFLINE_ALBUMINFO.HTM`, ...) do not use the wizard
methods.

#### MDQ fields

Field names seen next to the MDQ code (to be confirmed on traffic):
`mdqRequestID`, `flags`, `word`, `text`, `album`, `title`, `filename`,
`artist`, `track`, `trackNumber`, `trackDuration`, `bitrate`, `drmProtected`.
There is an `ALBUM` mode and a `TRACK` mode (`MDQCD MODE Determined from data`).

### Expected response: MDR-CD 4.0

Empty template WMP uses internally (starting point for the response):

```xml
<?xml version="1.0"?>
<METADATA xmlns:sql="urn:schemas-microsoft-com:xml-sql">
  <MDR-CD>
    <version>4.0</version>
    <WMCollectionID /><WMCollectionGroupID /><mdqRequestID /><uniqueFileID />
    <albumTitle /><albumArtist /><releaseDate /><label /><genre />
    <providerStyle /><publisherRating /><communityRating />
    <buyParams /><largeCoverParams /><smallCoverParams /><moreInfoParams />
    <dataProvider /><dataProviderParams /><dataProviderLogo /><validated />
    <!-- one per track -->
    <track>
      <WMContentID /><trackTitle /><uniqueFileID /><trackNumber>%d</trackNumber>
      <trackPerformer /><trackComposer /><trackConductor /><period />
    </track>
  </MDR-CD>
</METADATA>
```

Checks WMP runs on the response (error messages in x86 `wmp.dll`):

- `request id tag not found in MDR` → return the `mdqRequestID` received
- `track request id tag not found in MDR` → every track has a `trackRequestID`
  (not in the empty template: exact position to be verified)
- `content id tag not found in track tag` → every track has a `WMContentID`
  (proposal: the recording MBID)
- `<trackRequestID> value in MDRCD out of range` / `invalid <trackNumber>` →
  values consistent with the request, `trackNumber > 0`
- `Album node given but no tracks found` → never an album without tracks

There is also `MDR-DVD` (out of scope for now) and a DVD template
`DVDID=%s&VC=%s&VN=%s&DS=%s&TC=%s&%s`.

### WMP behaviours to keep in mind

- **Throttling and backoff**: counters `AutoMetadataCurrent500ServerErrorCount`,
  `AutoMetadataCurrent503ServerErrorCount`, `AutoMetadataCurrentNetworkErrorCount`,
  minimum delay between background requests (`qwNextAllowedBackgroundTime`).
  During development **do not answer with 5xx** if avoidable: WMP slows down.
- **Request memory**: the library database has a `MetadataRequest` table with
  `CollectionID`, `TOCorDVDID`, `ISCD`, `Pending`, `Downloaded`, `Validated`,
  `MDRDocument`. Albums already marked as not found might not be requested
  again automatically: document how to force them (manual "Find album info" or
  clearing the cache).
- **Registry values** in `HKCU\Software\Microsoft\MediaPlayer\Preferences`
  (verified). Present on the development PC: `MetadataRetrieval=3`, `SendUserGUID=0`,
  `AcceptedPrivacyStatement=1`, `SilentAcquisition=1`, `AutoMetadataLastResetTime`,
  the counters `AutoMetadataCurrent{Download,500ServerError,503ServerError,OtherServerError,NetworkError}Count`.
  Absent (WMP uses the defaults in its code): `AutoMetadataUpdate`,
  `AutoMetadataFuzzyMatch`, `AutoMetadataMaxAllowedTracks`.
  HKLM has `SOFTWARE\Microsoft\MediaPlayer\PREFERENCES` (only
  `OEMServiceOverride11`, empty).
- **Group policies** that block everything when set
  (`Software\Policies\Microsoft\WindowsMediaPlayer`):
  `PreventCDDVDMetadataRetrieval`, `PreventMusicFileMetadataRetrieval`.
  Check them before every test.
- Lead to follow: `fpmdqlog.txt` appears next to the MDQ code; maybe a debug log
  that can be enabled.
- Other useful strings in `wmp.dll`: `Album.TOC='%s'` and `Track.WMContentId="%s"`
  (library queries), `AlbumArtSmall.jpg`, `LargeAlbumArtSize`,
  `SmallAlbumArtSize`, `CPAlbumArtCachePath`. In the .mui: `Blocked_AlbumInfo.htm`
  (page shown when retrieval is blocked).

## Test zero (2026-11-06, unmodified WMP)

14-track audio CD in the USB drive, 32-bit WMP opened with no change at all
to the system.

- WMP opened an HTTPS connection to `2a02:26f0:8d00:594::57`
  (Akamai, i.e. the real server) and closed it shortly after.
- Result in WMP: **"Unknown album"**. The error counters in the registry stayed
  at 0, so the server answered "no data", not with an error.
- The TOC appeared in the database:

  ```text
  E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186
  ```

  **Format:** number of tracks, then the start offset of each track, then the
  lead-out; all in hexadecimal without leading zeros, separated by `+`. The
  offsets already include the 150 pregap sectors (`0x96` = 150 = LBA 0 + 150),
  like the MusicBrainz Disc ID offsets. It matches exactly the value computed
  from the disc's `Track*.cda` files.
- MusicBrainz Disc ID computed from the same offsets: `vmDt7K4IGb_crskc3Ul1.rHoKRE-`
  (to be confirmed with Picard).
- Watch out for Enhanced CDs: the data track does not show up among the `.cda`
  files and shifts the Disc ID lead-out; check what WMP puts in the TOC then.
- **Two more CDs** (same day, WMP reopened): a very well-known 12-track one and
  a 21-track one. Both **"Unknown album"**, but
  `AutoMetadataCurrentDownloadCount` went **from 0 to 6** without errors: the
  real server answers "successfully" but with no data, even for famous albums.
  TOC format confirmed (`C+96+5370+...+3AC1A` identical to the computation from
  the `.cda` files). For each disc a new `MetadataRequest` row with id
  `{GUID}0001`.
- **Check on MusicBrainz** (public API, Disc IDs computed from the WMP TOC):

  | CD | Exact Disc ID | TOC search (`/ws/2/discid/-?toc=`) |
  | --- | --- | --- |
  | 14 tracks | 404, not attached | 12 different releases: noise |
  | 12 tracks | 404, not attached | 1 candidate (2 editions): Giovanni Allevi, *Joy* (2006, IT), **confirmed by the user** |
  | 21 tracks | only a **CD stub** "Miniclub Compilation" (Various) | - |

  Consequences for phases 2-3: the exact Disc ID alone is not enough; the TOC
  search is needed but must be filtered with a score on the duration distance
  and a threshold (below it: "not found" or a choice page); CD stubs are
  anonymous low-quality data, to decide whether and how to use them. Attaching
  the Disc IDs with Picard ("Submit disc ID") improves the exact lookup for
  everyone.
- Columns seen in the database (besides those already listed):
  `MetadataRequest.RequestID`, `.Locale`, `.Key`, `.TOCorDVDIDExBit`,
  `.TOCorDVDIDExText`; `Album.TOCExBit`, `Album.TOCExText`;
  `Track.MetadataState`, `.MetadataNeedsWork`, `.MetadataProviderRequestState`,
  `.MetadataProviderRequestTime`. A string of the form `{GUID}0001` also shows
  up, maybe a `RequestID` (GUID + counter): to compare with the capture. It is
  an identifier of the user's library, so treat it like the GUIDs in fixtures.

### Other files analysed (no metadata logic)

`wmplayer.exe` ("WMP Skin Host" shell), `wmpshell.dll` (context menu, skin
handler), `wmpdxm.dll` (WMP 6.4 / NetShow `NSPlay` compatibility),
`wmpeffects.dll` (equalizer, DSP plugins), `wmpps.dll` (COM proxy/stub),
`wmprph.exe` (File Explorer preview), `wmpnetwk.exe` (DLNA server, 64-bit
only; reads tags from the Windows Search index).

## First capture (2026-11-06, `setup` + `record --forward`)

32-bit WMP, *Joy* CD in the drive, then "Find album info" on the CD.
Captures in `%LOCALAPPDATA%\WMPRevival\captures`, fixtures (without Microsoft
material) in `tests/fixtures/captures/`.

- **WMP accepts the local CA**: TLS 1.3 completed, SNI
  `musicmatch-ssl.xboxlive.com`. No pinning (question 5 closed).
- **Automatic CD lookup: `GET`, no body** (the DAI template was not used):

  ```text
  GET /cdinfo/GetMDRCD.aspx?locale=410&geoid=76&version=12.0.26100.9549&userlocale=410&CD=C+96+5370+...+3AC1A
  Accept: */*
  User-Agent: Windows-Media-Player/12.0.26100.9711
  Accept-Encoding: gzip, deflate
  ```

  - `locale` and `userlocale` are hexadecimal LCIDs (0x410 = it-IT), `geoid` is
    a hexadecimal GEOID (0x76 = 118 = Italy).
  - `version` is the version of `SysWOW64\wmp.dll` (9549), while the User-Agent
    carries 9711 (the version of the 64-bit `wmp.dll` / of the system): do not
    trust the User-Agent for the version.
  - `CD=` (upper case) is **exactly** the TOC stored in `Album.TOC`, with literal
    `+`. Never decode it with `unquote_plus`/`parse_qsl`.
  - At startup WMP requested **all three** CDs seen that day (and *Joy*
    twice): "not found" discs get requested again.
- **The real server's response today** (200, gzip, `Cache-Control: no-cache, no-store`):

  ```xml
  <?xml version="1.0"?>
  <METADATA xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Backoff><Time>5</Time></Backoff>
    <mdqRequestID>00000000-0000-0000-0000-000000000000</mdqRequestID>
    <ResponseCode>Gracenote provider has been removed</ResponseCode>
  </METADATA>
  ```

  The service was based on Gracenote, now removed. `<Backoff><Time>` tells WMP
  how long to wait (unit unknown: probably minutes); we can use this element
  too to regulate retries. WMP counts these responses as successful downloads
  (`AutoMetadataCurrentDownloadCount`).
- **"Find album info" on the CD: an HTML page**, not XML:

  ```text
  GET /FAI/default.aspx?locale=410&geoid=76&version=12.0.26100.9549&userlocale=410&cd=C+96+...+3AC1A
  User-Agent: Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 10.0; WOW64; Trident/7.0; ...)
  Accept-Language: it-IT
  ```

  `cd=` here is **lower case**. The engine is MSHTML in **IE7 document mode**:
  no `JSON`, `querySelector`, `addEventListener`, `const/let`; ES3 only.
  The page loads `style.css` and `suggestions.js` (relative paths, hence
  `/FAI/style.css` and `/FAI/suggestions.js`, with `Referer`).
- **Results page structure** (empty today, "No matches found"): an inline
  script with

  ```js
  var mdrcds = [];   // one MDR-CD document (XML string) per candidate album
  var matches = [];  // for each candidate, the indexes of the matching tracks
  ```

  two columns ("Track list" with `tr.track` rows, id `track-N`, "Album" with
  selectable elements, id `album-N`), a "Finish" button (`finish()`, disabled
  until an album is chosen) and "Cancel" (`window.external.ReturnToMainTask()`).
- **How the page hands the data back to WMP** (from `suggestions.js`, read, not
  copied):

  ```js
  window.external.WriteNamesEx(2, "", EncodeOMI(mdrcds[chosen]), true);
  window.external.ReturnToMainTask();
  ```

  - type **2 (`WMP_WRITENAMES_TYPE_CD_BY_MDQCD`) even for a CD**, empty
    `bstrTypeId`, `fRenameRegroupFiles = true`;
  - `EncodeOMI`: in the text between `>` and `<`, every character with a code
    > 126 becomes a numeric reference `&#N;`. The MDR passed to WMP is
    therefore ASCII. Our server can produce it that way already (lxml with
    `encoding="us-ascii"`), and our page needs no re-encoding.
- **"Find album info" on a file** (second and third sessions, downloaded FLAC
  *Alphaville - Big in Japan*). Note: a library track still tied to a CD uses
  `cd=<toc>` as above; to see the file flow you need a track that was not ripped
  from a CD.
  1. `GET /FAI/default.aspx?locale=410&geoid=76&version=...&userlocale=410&requestid=<GUID>`
     (GUID generated by WMP, upper case, **without braces**) → bridge page: form
     `#bridge` (POST, no action) with hidden field `mdq`, and the script
     `window.external.GetMDQByRequestID("<GUID>")` → field value → `submit()`.
  2. `POST` **to the same URL** (query included), `application/x-www-form-urlencoded`,
     `Referer` = the GET URL, body `mdq=<url-encoded MDQ-CD>`:

     ```xml
     <METADATA>
         <MDQ-CD>
             <mdqRequestID>GUID</mdqRequestID>
             <track>
                 <title><text>Big in Japan</text><word>Big</word><word>in</word><word>Japan</word></title>
                 <artist><text>Alphaville</text><word>Alphaville</word></artist>
                 <trackNumber>3</trackNumber>
                 <filename>03. Alphaville - Big in Japan.flac</filename>
                 <trace>2533 2122 1934 ... 1088</trace>   <!-- 30 integers: audio fingerprint -->
                 <trackDuration>286000</trackDuration>      <!-- milliseconds -->
                 <bitrate>807728</bitrate>
                 <album>
                     <title><text>Forever Young</text><word>Forever</word><word>Young</word></title>
                     <artist><text>Alphaville</text><word>Alphaville</word></artist>
                 </album>
                 <trackRequestID>0</trackRequestID>         <!-- 0-based track index -->
             </track>
         </MDQ-CD>
     </METADATA>
     ```

     A single selected track → a single `<track>`; with several selected files
     we expect several `<track>` with `trackRequestID` 0, 1, ... (to verify).
     CRLF lines and 4-space indentation. No TOC, no user GUID.
  3. The Microsoft server answers **`410 Gone`** with no body (`Connection:
     close`): this is the "410 error" WMP shows the user. The counter
     `AutoMetadataCurrentOtherServerErrorCount` did not go up.
  - Our server will answer the POST with the **results page** (same
    `mdrcds`/`matches` structure as the CD page) and `WriteNamesEx(2, "", mdr, true)`;
    the MDR must carry the `mdqRequestID` received and, for each track, the
    `trackRequestID` received.
- **Counters after the sessions**: `AutoMetadataCurrentDownloadCount=13`,
  `NetworkErrorCount=3`. The network errors come from the times when the
  redirect was active and the recorder stopped: always run `teardown` at the
  end of a session.

## Decisions taken (2026-11-06)

1. Phase 1: recorder with an optional `--forward` mode towards the real
   Microsoft server (direct DNS resolution, bypassing the hosts file), to be
   used only in capture sessions. Default: record only.
2. Reference Disc IDs: Picard's.
3. MusicBrainz mirror postponed: we start with the public API + cache
   (implemented on 2026-11-06). Remote SQL access to the MusicBrainz database
   does not exist as a public service. If the API is not enough we move to a
   mirror (`musicbrainz-docker`, same `ws/2` API): just change `base_url` and
   `min_interval` in `config.toml`, the code does not change.
4. GitHub repository, **private** at first; made **public** on 2026-11-07.

## Work plan

Proceed in this order. Every phase ends with green tests.

### Phase 0 — TLS infrastructure and redirect

- A script (PowerShell or Python) that:
  - creates a local CA with name constraints on `musicmatch-ssl.xboxlive.com`
  - issues the server certificate for that name
  - installs the CA in the computer's "Trusted Root Certification
    Authorities" (requires user confirmation)
  - adds/removes the line `127.0.0.1 musicmatch-ssl.xboxlive.com` in the hosts
    file, with a backup
- An uninstall command that undoes everything (CA, certificate, hosts line).

### Phase 1 — Recorder (before writing any response)

- HTTPS server on 127.0.0.1:443 that **records everything** (method, path, query,
  headers, body) in dated files, one per request.
- Answers neutrally (preferably a fast 404, not 5xx).
- With `--forward`: forwards the request to the real server (IP obtained with
  direct DNS, SNI and certificate verification on the original name), also
  records the response and returns it to WMP. WMP's data (TOC, fingerprints)
  therefore goes to Microsoft: capture sessions only.
- Response bodies (HTML included) are recorded in full: base64 of the exact
  bytes, plus the text decoded with the declared charset.
  `application/x-www-form-urlencoded` forms (the bridge page POST) are also
  split into fields, so the MDQ in the `mdq` field is readable in clear text.
- Goal: capture `GetMDRCD.aspx?...` with a real CD and `FAI/default.aspx`
  with "Find album info" on a file.
- Captures become **test fixtures** (anonymising any user GUIDs).
- If WMP does not connect despite CA and hosts: check certificate pinning,
  group policies, WMP privacy settings ("Retrieve additional information from
  the Internet").

### Phase 2 — From the WMP TOC to the MusicBrainz Disc ID

- Decode the captured `toc` format (strong hypothesis from test zero, see above).
- If the Disc ID is not in MusicBrainz: fall back to `/ws/2/discid/-?toc=...`
  (similarity search on the TOC).
- Compute the MusicBrainz Disc ID (libdiscid algorithm: SHA-1 over first track,
  last track, lead-out and 99 offsets, Base64 with the `._-` alphabet).
- Test with the user's real CDs whose Disc ID is already known (Picard).

**Status (2026-11-06): done.** `src/wmprevival/toc.py` (WMP TOC ↔ Disc ID,
MusicBrainz `toc` parameter, track durations), verified on the three real CDs.

### Phase 3 — Response for CDs

**Status (2026-11-06): lookup done, response to WMP to do.**

- `config.py`: `%LOCALAPPDATA%\WMPRevival\config.toml`, section `[musicbrainz]`
  with `contact` (mandatory for the public API: the public alias
  `github@maksimtech.com`, never the personal address), `base_url`, `min_interval`
  (≥ 1 s for the public API, enforced by the code).
- `musicbrainz.py`: aiohttp client, one request at a time spaced by
  `min_interval`, retries on 503/429 with `Retry-After`, SQLite cache in
  `%LOCALAPPDATA%\WMPRevival\cache` (hits 30 days, 404 one day).
- `matching.py`: exact Disc ID first; then TOC search with `cdstubs=no`; every
  medium with the same number of tracks is compared track by track on
  durations; accepted if the maximum gap is ≤ 3 s (or if the Disc ID is
  attached to that medium). Deterministic ordering.
- `wmprevival mb-lookup <TOC>` shows candidates and choice (2 requests per CD).
- Real results: *Joy* → Giovanni Allevi, *Joy* (2006, IT), two editions
  accepted, constant gap of ~2 s per track (MusicBrainz durations do not seem
  to include the pause between tracks); 14-track CD → of the 12 candidates only
  a few remain accepted, with *Das Spieluhren-Orchester - Gute Nacht* on top
  (DE, mean gap 0.3 s), **to be confirmed with the user**.
- MusicBrainz response fixtures (CC0) in `tests/fixtures/musicbrainz/`,
  recorded with `tools/record_mb_fixture.py`.
- **Responses to WMP implemented (2026-11-06), to be tried on real WMP** with
  `scripts\serve.cmd` (`wmprevival serve`, option `--no-auto`):
  - `mdr.py`: MDR-CD 4.0 with every element of the `wmp.dll` template;
    `WMCollectionID` = `{release MBID}`, `WMCollectionGroupID` = `{release group MBID}`,
    `WMContentID` = `{recording MBID}` (upper-case GUIDs in braces), `trackRequestID`
    last child of `<track>`, `releaseDate` as in MusicBrainz, top-voted genre
    of the release group, label, composers from the works (`performance` → work →
    `composer`, falling back to `writer`), conductors (`conductor`). XML in ASCII
    with `&#N;` (equivalent of `EncodeOMI`).
  - `GetMDRCD`: MDR of the first choice, otherwise the "no data" response shaped
    like Microsoft's (`Backoff`, `mdqRequestID`, `ResponseCode`).
  - FAI on a CD: up to 3 editions accepted by the duration-based choice, all tracks.
  - FAI on files: our own bridge page (`requestid` validated as a GUID), then the
    search `recording:"…" AND artist:"…" AND release:"…"` (without the album if
    it finds nothing), ordering: same title and duration within 5 s mandatory,
    then same album, track number, official, Album type, **Digital Media first**
    (the user has the digital edition `10f85058` of *Forever Young*), date, MBID;
    up to 5 editions; the MDR contains only the requested tracks with their
    `trackRequestID`.
  - Our own pages and JS (`src/wmprevival/web/`), ES3; if `WriteNamesEx` raises
    an error the page shows it and sends it to `/FAI/log` (it ends up in the
    captures and in the `serve` console).
  - Local test with real MusicBrainz (curl on port 8443): `GetMDRCD` for *Joy*
    1.3 s, FAI on the file 8.3 s the first time (1 search + 5 releases), then cache.
- Hypotheses to verify on WMP: GUID format, position of `trackRequestID`,
  year-only `releaseDate`, zero `mdqRequestID` for CDs, `WriteNamesEx(2, "", …)`
  for the CD too, waiting times WMP accepts.

### First run with our server (2026-11-06, `serve` without `--no-auto`)

- Automatic `GetMDRCD` for *Joy* → our MDR. Then "Find album info"
  on the CD: the page received **`wmid=12C5606B-1B33-4AB3-BEC1-F556972B5F89`**,
  i.e. our `WMCollectionID` without braces: **WMP accepted and stored the
  automatic MDR**. Picked edition 1 → `WriteNamesEx` **without errors**
  (`/FAI/log?kind=WriteNamesEx-ok`). GUIDs in braces and a zero
  `mdqRequestID` for CDs: accepted.
- The 14-track CD automatically received *Gute Nacht* (Das
  Spieluhren-Orchester): **to be confirmed with the user**.
- Finding: **automatic lookup of library files** =
  `POST /cdinfo/GetMDRCD.aspx?locale=…&userlocale=410&requestID=<GUID>` (here
  `requestID` with capitals), `Content-Type: text/xml`, MDQ-CD body with
  **`<album>` at the `MDQ-CD` level** (not inside the tracks) and several
  `<track>` from the same album with `trackRequestID` out of order (0, 3, 1, 2).
  Fixture `tests/fixtures/captures/auto_file_post_mdq.json`. Now handled: MDR
  with only the matched tracks and their `trackRequestID`, only if the album
  matches too.
- Store titles carry markers (`[Explicit]`, `(Clean)`) absent from
  MusicBrainz: stripped for searching and comparing (`[Edit]` is not: it is a
  different version). The special MusicBrainz label `[no label]` is not written.
- On Windows the ProactorEventLoop printed a harmless traceback when WMP
  closes the connection first (`_call_connection_lost` → `shutdown`):
  silenced for connection resets only (`recorder.quiet_connection_resets`).
- To check in WMP: what it shows after `WriteNamesEx` (titles, year, genre,
  file renaming with `fRenameRegroupFiles = true`), and whether the
  *Mine All Day* files get updated by the automatic lookup.

### WMP keeps a single MDR per `WMCollectionID` (bug of 2026-11-06)

User report: in P!nk's album *I'm Not Dead* tracks 1, 2 and 3 all became
"Long Way to Happy" (track 3).

- Sequence from the captures: FAI on 12 files → MDR of release `2EBD0F3C` with
  `trackRequestID 0` = "Long Way to Happy"; then two automatic lookups (*Who
  Knew*, *Stupid Girls*), each with its file at `trackRequestID 0`: **correct
  responses but with the same `WMCollectionID`**.
- The library database (copy) holds **a single** MDR document for
  `2EBD0F3C`: header with the `mdqRequestID` of the last automatic lookup,
  but **tracks from the FAI document**. WMP updates the header, keeps the old
  tracks and applies the new `trackRequestID`s, which however count from 0 in
  every request. The documents are stored in UTF-16, reformatted, in
  `CurrentDatabase_*.wmdb` (even split across pages).
- The FAI page instead **replaces** the document (the *Pink Floyd* albums
  redone several times stayed correct): the reuse concerns the automatic lookup.
- **Rule**: for file responses `WMCollectionID` is a UUID v5 of
  (release, `mdqRequestID`), different for every request
  (`service.file_collection_id`); the release stays in `dataProviderParams`
  (`musicbrainz-release=<MBID>`). For CDs it stays the release MBID, because
  there `trackRequestID` is always the TOC index and therefore stable.
- Effects to verify: grouping of albums in the library (WMP seems to group by
  album title and artist) and cover art (`AlbumArt_{ID}_*.jpg` are per
  `WMCollectionID`: phase 6).
- Repairing already damaged tags: the lookup tries up to 3 files and, for
  each, the tag title and the one derived from the **file name**
  (`01. Stupid Girls.flac`), always with the duration gap ≤ 5 s; the same goes
  for matching, which never gives the same track to two files. At most 4
  searches per request. Fixture `fai_file_pink_damaged.json`.

### Multi-valued artists (*Arrival*, ABBA, 2026-11-06)

- WMP joins the multiple values of the artist tag with `, `: the *Arrival*
  FLACs arrived as `ABBA, Abba`, and `artist:"ABBA, Abba"` on MusicBrainz gives
  no results.
- `service.artist_candidates`: track artist, its collapsed form (only if all
  the parts separated by `,` `;` `/` are the same name: *Earth, Wind & Fire*
  stays intact), album artist; never "Various Artists". The searches try the
  artist × title combinations, at most 5 per request.
- Edition chosen: `f121c662` (2012-03-30), **confirmed by the user**.
- **Verified on WMP by the user (2026-11-06)**: after restarting `serve`,
  *Arrival* is found and applied correctly.

### `WriteNamesEx(2, "")` from the CD page hits the last MDQ request (2026-11-06)

- Sequence: FAI on the *Arrival* file (choice applied), then FAI on the *Joy*
  CD in the drive (`cd=<toc>&wmid=12C5606B…`) with the page calling, like the
  Microsoft one, `WriteNamesEx(2, "", mdr, true)`. WMP applied the *Joy* MDR to
  the **previous MDQ request**: the *Arrival* file became *Panic* and, because
  of `fRenameRegroupFiles = true`, was **renamed and moved** to
  `Music\Giovanni Allevi\Joy\01 Panic.flac` (FLAC tags untouched: WMP does not
  write FLAC tags). In the same folder WMP created `Folder.jpg`,
  `AlbumArtSmall.jpg` and `AlbumArt_{WMCollectionID}_Large/Small.jpg`.
- With `true` WMP also **reorganised the whole Music folder** into
  `Artist\Album\NN Title.flac` for the albums involved (16:26-16:28).
- **Rules**: the CD page uses `WriteNamesEx(0 = CD_BY_TOC, "<WMP TOC>", …)`;
  the file page `(2, "", …)` (the current request is the page's own);
  `fRenameRegroupFiles` is **false** unless `serve --rename-files`.
- To verify: that `CD_BY_TOC` with the TOC in WMP's format is accepted
  (the page reports any error to `/FAI/log`).
- Restore done with the user: FLAC moved to
  `Music\ABBA\Arrival\10. ABBA, Abba - Arrival.flac` (SHA-256 verified),
  removed the images generated by WMP and the empty folders; the rest of the
  reorganisation left as is, by the user's choice.
- **WMP does not re-read the tags of a moved file**: the library keeps "Panic" /
  Giovanni Allevi / Joy, with the new file name. The server now recognises the
  **suspect tag** (`service.tag_is_suspect`: the file name `NN. Artist -
  Title` points to another artist and another title) and searches from the file
  name first, without the tag's album; in that case the automatic lookup
  **writes nothing** (the user decides from the page). Fixture
  `fai_file_abba_as_panic.json`.
- **The file went back to `Giovanni Allevi\Joy\01 Panic.flac` by itself** (16:38,
  with the images recreated): `HKCU\…\MediaPlayer\Preferences` shows
  `AutoRename = 1` and `AutoOrganize = 1` (Options > Library:
  "Rename music files using rip music settings" and "Rearrange music in rip
  music folder, using rip music settings"), absent at the start of the session
  along with `FlushMetadataToFiles = 1` and `AutoMetadataUpdate = 0`. With
  these options WMP renames and moves files according to **its library**,
  regardless of `fRenameRegroupFiles`. `wmprevival status` now reports them.
  To fix wrong data in the library: turn off the two options, delete the entry
  **from the library only**, put the file back in its place with WMP closed,
  reopen WMP (it re-reads the file tags).
- Pages and scripts are loaded once at startup (`StrictUndefined`, no
  reloading): a server started before an update served the new page with the
  old code (`Object of type Undefined is not JSON serializable`).

- Lookup `/ws/2/discid/<id>?inc=recordings+artist-credits+labels+release-groups+work-rels+artist-rels`
  on the **public API** with cache (mirror postponed, configurable address).
- If several releases match: deterministic, documented choice (country,
  date, CD format).
- Mapping to MDR-CD (table in `docs/mapping.md`):
  `albumTitle`←release title, `albumArtist`←artist credit, `releaseDate`←date,
  `label`←label, `genre`←genre/tag, `trackTitle`, `trackPerformer`,
  `trackComposer` and `trackConductor` from the relationships, `period` (to define),
  `WMContentID`←recording MBID, `WMCollectionID`←release MBID,
  `dataProvider`="MusicBrainz".
- Golden tests: captured request → expected response.

### Phase 4 — Response for files (FAI)

- **The server serves HTML, not just XML** (see "FAI is an HTML flow"):
  - `GET /FAI/default.aspx?<param>=<id>` → **bridge page**: calls
    `window.external.GetMDQByRequestID(id)`, puts the MDQ in the hidden field
    `mdq` and POSTs to the same URL;
  - `POST /FAI/default.aspx` (form with `mdq`) → **results page**:
    MDQ-CD parsing with `defusedxml`, MusicBrainz search, response to WMP via
    `window.external` (presumably `WriteNamesEx(2, ...)`), optional choice
    among several candidates, `ReturnToMainTask()`.
- Pages generated with Jinja2 and autoescape (rule 8), with anti-XSS tests.
- Pages suited to WMP's HTML engine (MSHTML, **IE7 document mode**,
  verified): ES3 JavaScript, no `JSON`, `fetch`, `querySelector`,
  `addEventListener`, no external libraries. Also serve our own
  `/FAI/style.css` and `/FAI/suggestions.js`.
- The same results page also serves "Find album info" on a CD
  (`?cd=<toc>`), with `WriteNamesEx(2, "", mdr, true)`: to be done together with
  phase 3.
- MusicBrainz search by artist/album/title with matching on duration and
  track number, on the public API (the mirror's indexes would only matter
  with the mirror).

### Phase 5 — Discogs fallback

- If MusicBrainz finds nothing: Discogs search, same mapping.
- Queue respecting the request limit, plus cache.

### Phase 6 — Cover art

- Find out from captures how WMP uses `largeCoverParams` / `smallCoverParams`
  (full URL? parameters appended to a fixed address? another host?).
- Source: Cover Art Archive `.../release/<MBID>/front-500`; Discogs resized
  to a long side of **at most 500 px**, never upscaled.
- Only the front cover goes to WMP. The other types (back, spine, booklet,
  medium, tray) are outside the server's scope.

### Phase 7 — Packaging

- Run as a Windows service (or scheduled task), config file, rotating logs,
  a `status` command showing CA, hosts, port, last outcome.

### Separate component (optional): watcher for Picard

- Watches the music folder; for new FLACs it launches Picard with the commands
  `-e LOAD ... -e CLUSTER -e LOOKUP -e SAVE_MATCHED -e QUIT` (Picard ≥ 2.9),
  so WMP finds files already tagged.

## MusicBrainz mirror

- Official project `metabrainz/musicbrainz-docker`.
- Without search indexes: ~2 threads, 4 GB RAM, ~100 GB. Enough for CDs (Disc ID).
- With indexes: ~16 threads, 16 GB RAM, ~350 GB. Needed for FAI and for Picard.
- Automatic replication with a MetaBrainz token (free for personal use); the
  search indexes must be rebuilt periodically.
- Planned target: first WSL2 on the PC, then a dedicated Debian machine.

## Code conventions

- Python ≥ 3.13 (development on 3.13, uv). aiohttp for the server and for the
  `--forward` client (custom resolver); httpx allowed for external APIs.
- Package `wmprevival` in `src/`, command `wmprevival`. Local data (PKI,
  captures, state, hosts backups) in `%LOCALAPPDATA%\WMPRevival`, never in the repo.
- Everything in the repo is in English: identifiers, comments, docs, CLI and
  page messages, changelog and commit messages.
- The maintainer prefers to be addressed in Italian in conversation.
- XML built with a library (lxml or ElementTree), **never by concatenating
  strings**: correct escaping of titles with `&`, `<`, typographic apostrophes.
- Incoming XML parsed with protection against entity expansion
  (`defusedxml`).
- Tests with pytest on real captured fixtures; ruff; mypy; CI on GitHub Actions.
- CHANGELOG and CalVer versioning, like the author's other projects.
- MIT licence.

## Open questions (to resolve in order)

Closed by the first capture: CD query format (1), method for the CD (2,
GET without body), pinning (5, none), current server response (7), the
results page's `window.external` method (9, `WriteNamesEx(2, "", mdr, true)`).

1. What does a `GetMDRCD` response **with** data that WMP accepts look like:
   a `METADATA` root with `MDR-CD` inside? Which `mdqRequestID`, given that the
   GET request does not carry one? To try with our server, starting from the
   MDR-CD 4.0 template and the `wmp.dll` error messages.
2. Where `trackRequestID` goes in the MDR (in the MDQ request it is the 0-based
   track index, inside `<track>`): presumably inside every `<track>` of the
   MDR, with the same value.
3. How cover art is resolved (`largeCoverParams`/`smallCoverParams`).
4. Unit of `<Backoff><Time>` and whether WMP honours it.
5. FAI with **several files** selected: one MDQ with several `<track>`?
6. How to force a new attempt on albums already marked "not found"
   (WMP requests them by itself at startup: reopening it may be enough).
