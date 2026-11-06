# Changelog

Tutte le modifiche rilevanti di WMP Revival sono documentate qui.
Formato: [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/).
Versioni: [CalVer](https://calver.org/) `YYYY.MM.PATCH`.

## [Unreleased]

### Aggiunto

- **Fase 2, dal TOC di WMP al Disc ID** (`toc.py`), verificata sui tre CD reali.
- **Ricerca su MusicBrainz** con l'API pubblica: client con limite di una richiesta al secondo,
  User-Agent con contatto da `config.toml`, ritentativi su 503/429 e cache SQLite; l'indirizzo
  base è configurabile per passare a un mirror locale senza cambiare codice.
- **Scelta del disco** (`matching.py`): Disc ID esatto, poi ricerca per TOC con scarto massimo
  di 3 s traccia per traccia; scarta i dischi estranei proposti da MusicBrainz.
- **`wmprevival serve`: il server risponde a WMP con i dati di MusicBrainz.** MDR-CD 4.0 per
  la ricerca automatica dei CD (`GetMDRCD`), pagina dei risultati per "Trova informazioni
  album" su CD e su file (pagina ponte, MDQ letto con defusedxml), pagine Jinja2 con escaping
  automatico e JavaScript ES3 scritti da zero, test anti-XSS, mai un 5xx. Le edizioni digitali
  vengono proposte per prime per i file. `--no-auto` lascia solo le scelte manuali. Se WMP
  rifiuta i dati, la pagina mostra l'errore e lo segnala a `/FAI/log`.
- `scripts/servi.cmd` per una sessione di prova con il server (setup, serve, teardown).
- **Ricerca automatica dei file della libreria**: `POST /cdinfo/GetMDRCD.aspx` con l'MDQ in XML
  (album a livello di query, più tracce) riceve un MDR con le tracce abbinate e i loro
  `trackRequestID`; i marcatori dei negozi come `[Explicit]` non impediscono più il confronto.
- `wmprevival mb-lookup <TOC>` mostra candidati e scelta; `tools/record_mb_fixture.py` registra
  risposte di MusicBrainz come fixture.

- `record` segnala ogni ClientHello TLS: se non segue una richiesta, il client ha
  rifiutato il certificato.
- `scripts/cattura.cmd` per una sessione di cattura completa da un prompt amministratore.
- `fixture --drop-upstream-body` toglie le pagine e gli script del server Microsoft dalle
  fixture. Prime fixture reali in `tests/fixtures/captures/` (tre `GetMDRCD`, FAI su CD e FAI su
  file: GET con `requestid` e POST con l'MDQ-CD), con i test del formato osservato.
- `CLAUDE.md`: risultati della prima cattura (query del CD, risposta "Gracenote provider has
  been removed", pagina FAI in modalità IE7, `WriteNamesEx(2, "", mdr, true)`).

### Corretto

- **File diversi dello stesso album non ricevono più i dati di un'altra traccia.** WMP conserva
  un solo documento MDR per `WMCollectionID` e, nella ricerca automatica, riusava le tracce di
  un documento precedente con i `trackRequestID` della richiesta nuova (tre file di P!nk diventati
  tutti "Long Way to Happy"). Ora ogni risposta sui file ha un `WMCollectionID` proprio; la
  release resta in `dataProviderParams`.
- I tag già rovinati si riparano: se il titolo del tag non è compatibile con la durata, la
  ricerca e l'abbinamento usano il titolo ricavato dal nome del file, provando fino a tre file.
- `serve` e `record` non stampano più un traceback di asyncio quando WMP chiude la connessione
  per primo (ProactorEventLoop su Windows); gli altri errori restano visibili.
- L'etichetta speciale `[no label]` di MusicBrainz non finisce più nei dati per WMP.
- La CLI non si interrompe più con `UnicodeEncodeError` quando l'output va su una console
  cp1252: i simboli non rappresentabili diventano `?`.

## [2026.11.0] - 2026-11-06

### Aggiunto

- **Fase 0, infrastruttura TLS e redirect.** `wmprevival setup` crea una CA locale con
  name constraints su `musicmatch-ssl.xboxlive.com` (indirizzi IP esclusi), emette il
  certificato server, installa la CA nell'archivio radice (computer o utente) e aggiunge le
  righe `127.0.0.1`/`::1` al file hosts dopo averne fatto un backup. `teardown` annulla
  tutto partendo da `state.json`, anche dopo un `setup` interrotto. `status` mostra CA,
  hosts, porta e ultima cattura. `setup --dry-run` non tocca niente.
- **Fase 1, registratore.** `wmprevival record` ascolta in HTTPS solo su `127.0.0.1` e
  `::1` (altri indirizzi solo con `--allow-lan`), salva ogni richiesta in un file JSON
  datato (query grezza, header in ordine, corpo base64 e testo) e risponde 404 senza corpo.
  Con `--forward` inoltra al server Microsoft vero, risolto con DNS pubblico per scavalcare
  il file hosts, verifica il certificato sul nome originale, registra la risposta e la
  restituisce a WMP. Gli errori locali di inoltro diventano 404, mai 5xx.
- Le catture conservano la query **grezza** (`+` resta `+`, come nel TOC di WMP), decodificano
  il testo dei corpi con il charset dichiarato (anche le pagine HTML del server vero, pure se
  compresse) e scompongono i form `application/x-www-form-urlencoded`, così l'MDQ inviato
  dalla pagina ponte di FAI nel campo `mdq` si legge in chiaro.
- `wmprevival fixture` trasforma una cattura in fixture sostituendo i GUID con segnaposto
  stabili.
- `tools/wmdb_strings.py` per confrontare le stringhe di due copie del database della
  libreria di WMP.
- Test zero documentato in `CLAUDE.md`: formato del TOC salvato da WMP
  (`numero tracce+offset…+lead-out` in esadecimale, offset con i 150 settori di pregap).
- Documentati in `CLAUDE.md` il flusso HTML di FAI (pagina ponte con
  `window.external.GetMDQByRequestID` e POST del campo `mdq`), l'interfaccia
  `IWMPCDDVDWizardExternal` ricavata dalla type library di `wmp.dll` (`WriteNamesEx`,
  `WriteNames`, `GetMDQByRequestID`, enum `WMP_WRITENAMESEX_TYPE`) e la regola sulle pagine
  HTML generate solo con template ad escaping automatico.
