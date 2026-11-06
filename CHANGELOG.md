# Changelog

Tutte le modifiche rilevanti di WMP Revival sono documentate qui.
Formato: [Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/).
Versioni: [CalVer](https://calver.org/) `YYYY.MM.PATCH`.

## [Unreleased]

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
