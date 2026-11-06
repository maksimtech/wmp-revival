# WMP Revival

Server locale di metadati per **Windows Media Player Legacy** (WMP 12) su Windows 11.
WMP chiede ancora le informazioni sugli album a `musicmatch-ssl.xboxlive.com`, che oggi
risponde senza dati. WMP Revival si presenta al suo posto, solo su questo PC, e risponderà
con dati di MusicBrainz (e Discogs come ripiego). Nessun file di Windows viene modificato.

Stato: **fasi 0-2**; fasi 3-4 implementate (`wmprevival serve`), da collaudare su WMP. Copertine e
Discogs non ancora. Dettagli e piano completo in [CLAUDE.md](CLAUDE.md).

## Requisiti

- Windows 11, WMP a 32 o 64 bit
- [uv](https://docs.astral.sh/uv/) e Python 3.13
- Un prompt **da amministratore** per `setup` e `teardown` (file hosts e archivio
  dei certificati del computer)

```powershell
uv sync
uv run wmprevival --help
```

## Cosa fa `setup`

1. Crea in `%LOCALAPPDATA%\WMPRevival\pki` una CA locale **limitata con name constraints**
   a `musicmatch-ssl.xboxlive.com` (non può firmare certificati validi per altri nomi né per
   indirizzi IP), e un certificato server per quel nome. Le chiavi private hanno permessi
   ristretti al tuo utente.
2. Installa la CA tra le "Autorità di certificazione radice attendibili" (del computer, o
   dell'utente con `--store user`).
3. Fa un backup del file hosts e aggiunge `127.0.0.1` e `::1` per
   `musicmatch-ssl.xboxlive.com`, con il marcatore `# wmprevival`.

Ogni passo viene annotato in `state.json`, così `teardown` può annullare anche un `setup`
interrotto. `setup --dry-run` mostra cosa farebbe senza toccare niente.

```powershell
uv run wmprevival setup --dry-run
uv run wmprevival setup          # chiede conferma
uv run wmprevival status
uv run wmprevival teardown       # toglie CA e righe di hosts; --purge cancella anche la PKI
```

> Microsoft Defender a volte segnala le modifiche al file hosts che riguardano domini
> Microsoft (`SettingsModifier:Win32/HostsFileHijack`). Se succede, la riga viene tolta
> e WMP torna a parlare con il server vero: `status` lo mostra.

## Registratore

```powershell
uv run wmprevival record                 # registra e risponde 404
uv run wmprevival record --forward       # registra e inoltra al server Microsoft vero
```

Ogni richiesta diventa un file JSON in `%LOCALAPPDATA%\WMPRevival\captures`, con metodo,
percorso e query **grezzi**, header nell'ordine in cui arrivano, corpo (base64 e testo) e,
con `--forward`, anche la risposta del server vero.

`--forward` risolve il nome con un DNS pubblico (1.1.1.1, 9.9.9.9) invece del file hosts e
si collega all'indirizzo vero con verifica del certificato sul nome originale. Usarlo solo
nelle sessioni di cattura: TOC e impronte audio inviati da WMP arrivano a Microsoft.

Per trasformare una cattura in fixture di test, togliendo i GUID:

```powershell
uv run wmprevival fixture --drop-upstream-body <cattura.json> tests/fixtures/captures/<nome>.json
```

## MusicBrainz

La ricerca usa l'API pubblica di MusicBrainz (al massimo una richiesta al secondo, con cache
locale). Serve un contatto per lo User-Agent, in `%LOCALAPPDATA%\WMPRevival\config.toml`:

```toml
[musicbrainz]
contact = "indirizzo@esempio.it"
# Per un mirror locale (musicbrainz-docker), stessa API:
# base_url = "http://localhost:5000/ws/2"
# min_interval = 0
```

Prova senza WMP, con il TOC come lo scrive WMP:

```powershell
uv run wmprevival mb-lookup "C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A"
```

## Server con i dati di MusicBrainz

```powershell
uv run wmprevival serve            # risponde a WMP (dopo setup), registra tutto in captures
uv run wmprevival serve --no-auto  # niente ricerca automatica dei CD: solo "Trova informazioni album"
```

Sessione completa da un prompt amministratore: `scripts\servi.cmd` (setup, serve, teardown).

## Strumenti

- `tools/wmdb_strings.py`: confronta le stringhe di due copie del database della libreria
  di WMP (formato proprietario, non ESE). Lavorare su copie, con WMP chiuso.

## Licenza

MIT. Dati MusicBrainz: principali CC0, supplementari CC BY-NC-SA.
