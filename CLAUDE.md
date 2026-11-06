# WMP Revival — server di metadati locale per Windows Media Player Legacy

## Obiettivo

Far tornare a funzionare il recupero automatico delle informazioni album di
**Windows Media Player Legacy** (WMP 12) su Windows 11, senza modificare un solo
file di Windows.

WMP funziona ancora ed è aggiornato da Windows Update. Il servizio online a cui
chiedeva i metadati è ancora raggiungibile ma **non restituisce più dati**
(test zero del 2026-11-06: "Album sconosciuto" con connessione HTTPS riuscita).
Il progetto costruisce un **server locale che
si presenta come quel servizio** e risponde con dati presi da **MusicBrainz**
(fonte principale) e **Discogs** (ripiego).

```text
WMP (x86) ──HTTPS──> musicmatch-ssl.xboxlive.com
                     │  (file hosts → 127.0.0.1)
                     ▼
              traduttore locale  (questo progetto)
                     │
         ┌───────────┼──────────────────┐
         ▼           ▼                  ▼
   mirror MusicBrainz  Discogs API   Cover Art Archive
   (in casa, Docker)   (token)       (copertine)
```

## Regole non negoziabili

1. **Non modificare mai binari o risorse di Windows/WMP** (`wmp.dll`,
   `wmploc.dll.mui`, ecc.). Sono firmati tramite catalogo e Windows Update li
   sovrascrive. Si lavora solo per **interoperabilità**: file hosts, certificato
   locale, server che risponde.
2. **Non mettere binari Microsoft nel repository**, e nemmeno pagine, CSS o
   script del server Microsoft (es. `suggestions.js`, firmato da Microsoft).
   Le fixture si creano con `wmprevival fixture --drop-upstream-body`; i nostri
   HTML/JS si scrivono da zero, rispettando solo l'interfaccia osservata.
3. **Segreti fuori dal codice.** Il token Discogs si legge da variabile
   d'ambiente (`DISCOGS_TOKEN`) o da un file di configurazione escluso da git.
   Mai nei log, mai nelle risposte a WMP.
4. **Niente fonti senza licenza.** Gracenote non si usa (servono accordi
   commerciali). Solo MusicBrainz (dati principali CC0, supplementari
   CC BY-NC-SA, uso personale) e Discogs (API con token personale).
5. **La CA locale va limitata** con *name constraints* al solo
   `musicmatch-ssl.xboxlive.com`. Chiave privata della CA fuori dal repo, con
   permessi ristretti. Il server ascolta **solo su 127.0.0.1** (o sull'IP LAN se
   e quando deciso esplicitamente).
6. **Rispettare i limiti delle API**: MusicBrainz pubblico 1 richiesta/secondo
   con User-Agent identificativo (`WMPRevival/<versione> ( <contatto> )`, contatto
   da configurazione, nessuna email nel codice); Discogs ~60 richieste/minuto
   autenticato. Cache locale obbligatoria.
7. Prima di implementare un formato, **verificarlo su traffico reale catturato**.
   Quello che segue è ricavato da analisi statica e va confermato.
8. **Le pagine HTML si generano solo con un motore di template con escaping
   automatico** (Jinja2 con `autoescape=True`), mai concatenando stringhe. Titoli,
   artisti e ogni altro dato da MusicBrainz/Discogs sono input non fidato. Le
   pagine girano dentro WMP con accesso a `window.external`
   (`IWMPCDDVDWizardExternal`, che scrive nella libreria): **un XSS qui ha
   accesso a WMP**. Quindi: dati destinati a uno `<script>` solo serializzati in
   JSON sicuro per HTML (`|tojson`), niente gestori inline (`onclick=...`) con
   dati dentro, niente script o risorse da altri host, nessun `|safe` su dati
   esterni. Un test deve provare che un titolo con `</script><script>` o
   `"onmouseover=` esce innocuo.

## Ambiente dell'utente

Verificato sul PC di sviluppo il 2026-11-06.

- Windows 11 Pro **26H2, build 26300.9711** (il registro riporta "Windows 10 Pro"
  in `ProductName`, come sempre). I file di WMP restano della serie 12.0.26100.x.
- **WMP in uso: versione a 32 bit** (`C:\Program Files (x86)\Windows Media Player\wmplayer.exe`
  12.0.26100.1, motore `C:\Windows\SysWOW64\wmp.dll` 12.0.26100.9549)
- Versione a 64 bit presente: `System32\wmp.dll` 12.0.26100.9711
- Funzionalità `Media.WindowsMediaPlayer~~~~0.0.12.0` installata.
- Lettore CD: USB (esterno: va collegato per i test, unità F:).
- WSL2 (Ubuntu 24.04), Docker Desktop.
- Python via uv: 3.12, 3.13, 3.14, 3.15. **Si usa 3.13/3.14** (la 3.15 c'è ma
  le librerie compilate, es. lxml, possono non avere ancora le ruote).
- Strumenti: MusicBrainz Picard 3.0.3 (local fork), foobar2000,
  Qobuz, git/gh, openssl (quello di Git, non nel PATH). **EAC non è installato**:
  i Disc ID di riferimento sono quelli di Picard.
- Stile dei progetti dell'utente: CLI con Typer/Rich, pytest con fixture reali,
  ruff, mypy, CalVer `YYYY.MM.PATCH`, Docker, changelog.

## Cosa sappiamo dall'analisi statica

### Indirizzi del servizio

Stanno nella tabella stringhe di `System32\it-IT\wmploc.dll.mui` (12.0.26100.9549).
Il codice di controllo MUI `1b62db44dabe2f2eae74868406bb2566` coincide tra lo
stub a 32 bit, quello a 64 bit e il .mui (verificato anche sul PC di sviluppo, identico
a quello del PC del lavoro): le due versioni usano gli stessi indirizzi.
`SysWOW64\it-IT\wmploc.dll.mui` **non esiste**: il WMP a 32 bit prende le
stringhe dal .mui di System32 (comunque si connette al server, vedi test zero).

```text
https://musicmatch-ssl.xboxlive.com/cdinfo/GetMDRCD.aspx?     ← CD (parametri aggiunti a runtime)
https://musicmatch-ssl.xboxlive.com/FAI/default.aspx           ← "Find Album Info" per i file
```

- Un solo host, tutto in **HTTPS**.
- `redir.metaservices.microsoft.com` **non è più usato** da questa versione.
- Nessun indirizzo dei metadati è scritto in chiaro in `wmp.dll`.

### Il server Microsoft oggi (2026-11-06)

- `musicmatch-ssl.xboxlive.com` risolve ancora (CNAME su Akamai,
  `e87.dspb.akamaiedge.net`, anche IPv6). Certificato `CN=*.xboxlive.com`,
  emesso da `Microsoft TLS G2 ECC CA OCSP 06`, TLS 1.3.
- Con una GET semplice: `FAI/default.aspx` → **200**, `cdinfo/GetMDRCD.aspx?`
  senza parametri → **400**, radice → 404.
- Il DNS di casa a volte va in timeout su questo nome; 1.1.1.1 risponde.

### Rete e certificati in `wmp.dll`

- Importa sia **WinHTTP** (`WinHttpOpen`, `WinHttpSetOption`) sia **WinINet**
  (`InternetOpenW`, `HttpOpenRequestW`, `HttpSendRequestW`).
- Non chiama direttamente `CertGetCertificateChain` né
  `CertVerifyCertificateChainPolicy`: niente pinning evidente, da confermare.

### Database della libreria

- `%LOCALAPPDATA%\Microsoft\Media Player\CurrentDatabase_400.wmdb` e
  `LocalMLS_3.wmdb`. **Non è ESE** (firma `a5 09 b6 f3`): formato proprietario.
  Si studia confrontando le stringhe tra due copie (`tools/wmdb_strings.py`).
- Contiene percorsi e nomi di file personali: le copie restano fuori dal repo.

### Richiesta HTTP (da `wmp.dll`)

- `Content-Type: text/xml` (esiste anche `application/x-www-form-urlencoded` in
  un'altra parte del codice, non necessariamente per i metadati)
- `User-Agent: %s` con prefisso `Windows-Media-Player/` seguito dalla build
  (es. `Windows-Media-Player/12.0.26100.9549`)
- Header `Referer: %s` presente nel codice di download

### Domanda per i CD (DAI)

Modelli trovati in `wmp.dll`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<DAI trackCount="%lu" toc="%s">
  <track number="%ld"><base>%s</base> ... <confirm>%s</confirm></track>
</DAI>
```

- `toc` contiene il TOC del disco. Il formato **salvato nella libreria**
  (`Album.TOC`) è noto dal test zero, vedi sotto; resta da confermare che sia
  identico a quello che viaggia nella richiesta.
- `<base>` e `<confirm>` sono impronte audio per traccia. Il codice contiene
  `No confirmation trace received for this track. Not doing fp confirmation`:
  **se il server non manda impronte di conferma, WMP salta il controllo**.
  Il server può ignorarle.
- Da capire: se il TOC arriva nella query string di `GetMDRCD.aspx?`, nel corpo
  DAI, o in entrambi.

### Domanda per i file (MDQ-CD, usata da FAI)

```xml
<?xml version="1.0"?><METADATA><MDQ-CD></MDQ-CD></METADATA>
```

#### FAI è un flusso HTML, non una chiamata XML (2026-11-06)

`GET https://musicmatch-ssl.xboxlive.com/FAI/default.aspx` senza parametri
restituisce una **pagina ponte HTML**:

1. lo script della pagina chiama `window.external.GetMDQByRequestID("<id>")`, che
   restituisce l'XML MDQ preparato da WMP;
2. mette l'XML in un campo nascosto `mdq` di un form;
3. fa una **POST sulla stessa URL** (`application/x-www-form-urlencoded`).

Nella pagina ricevuta l'id è vuoto perché non avevamo passato parametri: il
server lo inserisce **prendendolo dalla query string** che manda WMP. Il nome del
parametro va catturato. La risposta alla POST sarà presumibilmente la pagina dei
risultati, che restituisce l'MDR a WMP con un altro metodo di `window.external`
(vedi sotto).

#### L'oggetto `window.external`: `IWMPCDDVDWizardExternal`

Dalla type library incorporata in `wmp.dll` (risorsa `TYPELIB`, `WMPLib`
`{6BF52A50-394A-11D3-B153-00C04F79FAA6}` v1.0), **identica** tra x86 e x64.
Interfaccia dispatch `IWMPCDDVDWizardExternal`
`{2D7EF888-1D3C-484A-A906-9F49D99BB344}`, "Not Public. Internal interface used by
Windows Media Player":

| DISPID | Metodo | Ritorno |
| --- | --- | --- |
| 0x2711 | `WriteNames(BSTR bstrTOC, BSTR bstrMetadata)` | void |
| 0x2712 | `ReturnToMainTask()` | void |
| 0x2717 | `WriteNamesEx(WMP_WRITENAMESEX_TYPE type, BSTR bstrTypeId, BSTR bstrMetadata, VARIANT_BOOL fRenameRegroupFiles)` | void |
| 0x2718 | `GetMDQByRequestID(BSTR bstrRequestID)` | BSTR (l'MDQ) |
| 0x271a | `IsMetadataAvailableForEdit()` | VARIANT_BOOL |
| 0x271b | `EditMetadata()` | void |
| 0x2727 | `BuyCD(BSTR bstrTitle, BSTR bstrArtist, BSTR bstrAlbum, BSTR bstrUFID, BSTR bstrWMID)` | void |

Più le proprietà comuni a tutti gli `external` di WMP: `version`,
`appColorLight/Medium/Dark/ButtonHighlight/ButtonShadow/ButtonHoverFace`
(propget), `OnColorChange` (propput).

```text
enum WMP_WRITENAMESEX_TYPE {
  WMP_WRITENAMES_TYPE_CD_BY_TOC        = 0,
  WMP_WRITENAMES_TYPE_CD_BY_CONTENT_ID = 1,
  WMP_WRITENAMES_TYPE_CD_BY_MDQCD      = 2,   // risposta a FAI (MDQ-CD)
  WMP_WRITENAMES_TYPE_DVD_BY_DVDID     = 3,
}
```

Ipotesi da verificare sulle catture:

- la pagina dei risultati FAI chiama
  `window.external.WriteNamesEx(2, <requestID>, <MDR-CD>, <rinomina?>)`;
- `WriteNames(toc, mdr)` / `WriteNamesEx(0, toc, ...)` sono la via del CD quando
  l'utente sceglie a mano ("Trova informazioni album" su un CD);
- `ReturnToMainTask()` chiude la procedura guidata.

Le altre interfacce `external` nella type library (`IWMPExternal`,
`IWMPBaseExternal`, `IWMPOfflineExternal` con `forceOnline()`,
`IWMPDiscoExternal`, `IWMPSubscriptionServiceExternal`) riguardano negozi online e
servizi, non i metadati. Le pagine HTML locali in `wmploc.dll.mui`
(`BLOCKED_ALBUMINFO.HTM`, `OFFLINE_ALBUMINFO.HTM`, ...) non usano i metodi del
wizard.

#### Campi MDQ

Nomi di campi visti accanto al codice MDQ (da confermare sul traffico):
`mdqRequestID`, `flags`, `word`, `text`, `album`, `title`, `filename`,
`artist`, `track`, `trackNumber`, `trackDuration`, `bitrate`, `drmProtected`.
Esiste una modalità `ALBUM` e una `TRACK` (`MDQCD MODE Determined from data`).

### Risposta attesa: MDR-CD 4.0

Modello vuoto che WMP usa internamente (punto di partenza per la risposta):

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
    <!-- una per traccia -->
    <track>
      <WMContentID /><trackTitle /><uniqueFileID /><trackNumber>%d</trackNumber>
      <trackPerformer /><trackComposer /><trackConductor /><period />
    </track>
  </MDR-CD>
</METADATA>
```

Controlli che WMP fa sulla risposta (messaggi d'errore in `wmp.dll` x86):

- `request id tag not found in MDR` → restituire l'`mdqRequestID` ricevuto
- `track request id tag not found in MDR` → ogni traccia ha un `trackRequestID`
  (non presente nel modello vuoto: posizione esatta da verificare)
- `content id tag not found in track tag` → ogni traccia ha un `WMContentID`
  (proposta: MBID della recording)
- `<trackRequestID> value in MDRCD out of range` / `invalid <trackNumber>` →
  valori coerenti con la richiesta, `trackNumber > 0`
- `Album node given but no tracks found` → mai album senza tracce

Esiste anche `MDR-DVD` (fuori ambito per ora) e un modello DVD
`DVDID=%s&VC=%s&VN=%s&DS=%s&TC=%s&%s`.

### Comportamenti di WMP da considerare

- **Throttling e backoff**: contatori `AutoMetadataCurrent500ServerErrorCount`,
  `AutoMetadataCurrent503ServerErrorCount`, `AutoMetadataCurrentNetworkErrorCount`,
  ritardo minimo tra richieste in background (`qwNextAllowedBackgroundTime`).
  Durante lo sviluppo **non rispondere con 5xx** se evitabile: WMP rallenta.
- **Memoria delle richieste**: il database della libreria ha una tabella
  `MetadataRequest` con `CollectionID`, `TOCorDVDID`, `ISCD`, `Pending`,
  `Downloaded`, `Validated`, `MDRDocument`. Album già marcati come non trovati
  potrebbero non essere richiesti di nuovo in automatico: documentare come
  forzarli ("Trova informazioni album" manuale o pulizia cache).
- **Valori di registro** in `HKCU\Software\Microsoft\MediaPlayer\Preferences`
  (verificato). Presenti sul PC di sviluppo: `MetadataRetrieval=3`, `SendUserGUID=0`,
  `AcceptedPrivacyStatement=1`, `SilentAcquisition=1`, `AutoMetadataLastResetTime`,
  i contatori `AutoMetadataCurrent{Download,500ServerError,503ServerError,OtherServerError,NetworkError}Count`.
  Assenti (WMP usa i valori predefiniti nel codice): `AutoMetadataUpdate`,
  `AutoMetadataFuzzyMatch`, `AutoMetadataMaxAllowedTracks`.
  In HKLM esiste `SOFTWARE\Microsoft\MediaPlayer\PREFERENCES` (solo
  `OEMServiceOverride11`, vuoto).
- **Criteri di gruppo** che bloccano tutto se impostati
  (`Software\Policies\Microsoft\WindowsMediaPlayer`):
  `PreventCDDVDMetadataRetrieval`, `PreventMusicFileMetadataRetrieval`.
  Verificarli prima di ogni test.
- Pista da indagare: `fpmdqlog.txt` compare accanto al codice MDQ; forse un log
  di debug attivabile.
- Altre stringhe utili in `wmp.dll`: `Album.TOC='%s'` e `Track.WMContentId="%s"`
  (interrogazioni sulla libreria), `AlbumArtSmall.jpg`, `LargeAlbumArtSize`,
  `SmallAlbumArtSize`, `CPAlbumArtCachePath`. Nel .mui: `Blocked_AlbumInfo.htm`
  (pagina mostrata quando il recupero è bloccato).

## Test zero (2026-11-06, WMP non modificato)

CD audio da 14 tracce nel lettore USB, WMP a 32 bit aperto senza nessuna
modifica al sistema.

- WMP ha aperto una connessione HTTPS verso `2a02:26f0:8d00:594::57`
  (Akamai, cioè il server vero) e l'ha chiusa poco dopo.
- Risultato in WMP: **"Album sconosciuto"**. Contatori di errore in registro
  rimasti a 0, quindi il server ha risposto "senza dati", non con un errore.
- Nel database è comparso il TOC:

  ```text
  E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186
  ```

  **Formato:** numero di tracce, poi l'offset di inizio di ogni traccia, poi il
  lead-out; tutti in esadecimale senza zeri iniziali, separati da `+`. Gli
  offset includono già i 150 settori di pregap (`0x96` = 150 = LBA 0 + 150),
  come gli offset del Disc ID MusicBrainz. Coincide esattamente con il valore
  calcolato dai file `Track*.cda` del disco.
- Disc ID MusicBrainz calcolato dagli stessi offset: `vmDt7K4IGb_crskc3Ul1.rHoKRE-`
  (da confermare con Picard).
- Attenzione ai CD Enhanced: la traccia dati non compare tra i `.cda` e sposta
  il lead-out del Disc ID; da verificare cosa mette WMP nel TOC in quel caso.
- **Altri due CD** (stesso giorno, WMP riaperto): uno da 12 tracce, molto noto, e
  uno da 21. Per entrambi **"Album sconosciuto"**, ma
  `AutoMetadataCurrentDownloadCount` è passato **da 0 a 6** senza errori: il
  server vero risponde "con successo" ma senza dati, anche per album famosi.
  Il formato del TOC è confermato (`C+96+5370+...+3AC1A` identico al calcolo dai
  `.cda`). Per ogni disco una nuova riga in `MetadataRequest` con id
  `{GUID}0001`.
- **Verifica su MusicBrainz** (API pubblica, Disc ID calcolati dal TOC di WMP):

  | CD | Disc ID esatto | Ricerca per TOC (`/ws/2/discid/-?toc=`) |
  | --- | --- | --- |
  | 14 tracce | 404, non collegato | 12 release diverse: rumore |
  | 12 tracce | 404, non collegato | 1 candidata (2 edizioni): Giovanni Allevi, *Joy* (2006, IT), **confermato dall'utente** |
  | 21 tracce | solo **CD stub** "Miniclub Compilation" (Various) | - |

  Conseguenze per le fasi 2-3: il Disc ID esatto da solo non basta; la ricerca
  per TOC serve ma va filtrata con un punteggio sulla distanza delle durate e
  una soglia (sotto: "non trovato" o pagina di scelta); i CD stub sono dati
  anonimi di bassa qualità, da decidere se e come usarli. Collegare i Disc ID
  con Picard ("Aggiungi ID disco") migliora il lookup esatto per tutti.
- Colonne viste nel database (oltre a quelle già elencate):
  `MetadataRequest.RequestID`, `.Locale`, `.Key`, `.TOCorDVDIDExBit`,
  `.TOCorDVDIDExText`; `Album.TOCExBit`, `Album.TOCExText`;
  `Track.MetadataState`, `.MetadataNeedsWork`, `.MetadataProviderRequestState`,
  `.MetadataProviderRequestTime`. Compare anche una stringa nella forma
  `{GUID}0001`, forse un `RequestID` (GUID + contatore): da confrontare con la
  cattura. È un identificativo della libreria dell'utente, quindi va trattato
  come i GUID nelle fixture.

### Altri file analizzati (nessuna logica di metadati)

`wmplayer.exe` (guscio "WMP Skin Host"), `wmpshell.dll` (menu contestuale,
gestore skin), `wmpdxm.dll` (compatibilità WMP 6.4 / NetShow `NSPlay`),
`wmpeffects.dll` (equalizzatore, plugin DSP), `wmpps.dll` (proxy/stub COM),
`wmprph.exe` (anteprima in Esplora risorse), `wmpnetwk.exe` (server DLNA, solo
64 bit; legge i tag dall'indice di Windows Search).

## Prima cattura (2026-11-06, `setup` + `record --forward`)

WMP a 32 bit, CD *Joy* nel lettore, poi "Trova informazioni album" sul CD.
Catture in `%LOCALAPPDATA%\WMPRevival\captures`, fixture (senza materiale
Microsoft) in `tests/fixtures/captures/`.

- **WMP accetta la CA locale**: TLS 1.3 completato, SNI
  `musicmatch-ssl.xboxlive.com`. Nessun pinning (domanda 5 chiusa).
- **Ricerca automatica del CD: `GET`, senza corpo** (il modello DAI non è stato
  usato):

  ```text
  GET /cdinfo/GetMDRCD.aspx?locale=410&geoid=76&version=12.0.26100.9549&userlocale=410&CD=C+96+5370+...+3AC1A
  Accept: */*
  User-Agent: Windows-Media-Player/12.0.26100.9711
  Accept-Encoding: gzip, deflate
  ```

  - `locale` e `userlocale` sono LCID in esadecimale (0x410 = it-IT), `geoid` è
    un GEOID in esadecimale (0x76 = 118 = Italia).
  - `version` è la versione di `SysWOW64\wmp.dll` (9549), mentre lo User-Agent
    porta 9711 (la versione della `wmp.dll` a 64 bit / del sistema): non fidarsi
    dello User-Agent per la versione.
  - `CD=` (maiuscolo) è **esattamente** il TOC salvato in `Album.TOC`, con `+`
    letterali. Mai decodificarlo con `unquote_plus`/`parse_qsl`.
  - All'avvio WMP ha richiesto **tutti e tre** i CD visti in giornata (e due
    volte *Joy*): i dischi "non trovati" vengono richiesti di nuovo.
- **Risposta del server vero oggi** (200, gzip, `Cache-Control: no-cache, no-store`):

  ```xml
  <?xml version="1.0"?>
  <METADATA xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <Backoff><Time>5</Time></Backoff>
    <mdqRequestID>00000000-0000-0000-0000-000000000000</mdqRequestID>
    <ResponseCode>Gracenote provider has been removed</ResponseCode>
  </METADATA>
  ```

  Il servizio era basato su Gracenote, ora rimosso. `<Backoff><Time>` dice a WMP
  quanto aspettare (unità da capire: probabilmente minuti); è un elemento che
  possiamo usare anche noi per regolare i tentativi. WMP conta queste risposte
  come download riusciti (`AutoMetadataCurrentDownloadCount`).
- **"Trova informazioni album" sul CD: pagina HTML**, non XML:

  ```text
  GET /FAI/default.aspx?locale=410&geoid=76&version=12.0.26100.9549&userlocale=410&cd=C+96+...+3AC1A
  User-Agent: Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 10.0; WOW64; Trident/7.0; ...)
  Accept-Language: it-IT
  ```

  `cd=` qui è **minuscolo**. Il motore è MSHTML in **modalità documento IE7**:
  niente `JSON`, `querySelector`, `addEventListener`, `const/let`; solo ES3.
  La pagina carica `style.css` e `suggestions.js` (percorsi relativi, quindi
  `/FAI/style.css` e `/FAI/suggestions.js`, con `Referer`).
- **Struttura della pagina risultati** (oggi vuota, "Nessuna corrispondenza
  trovata"): uno script inline con

  ```js
  var mdrcds = [];   // un documento MDR-CD (stringa XML) per ogni album candidato
  var matches = [];  // per ogni candidato, gli indici delle tracce che corrispondono
  ```

  due colonne ("Elenco di tracce" con righe `tr.track` id `track-N`, "Album" con
  elementi id `album-N` selezionabili), pulsante "Fine" (`finish()`, disabilitato
  finché non si sceglie un album) e "Annulla" (`window.external.ReturnToMainTask()`).
- **Come la pagina restituisce i dati a WMP** (da `suggestions.js`, letto, non
  copiato):

  ```js
  window.external.WriteNamesEx(2, "", EncodeOMI(mdrcds[scelto]), true);
  window.external.ReturnToMainTask();
  ```

  - tipo **2 (`WMP_WRITENAMES_TYPE_CD_BY_MDQCD`) anche per un CD**, `bstrTypeId`
    vuoto, `fRenameRegroupFiles = true`;
  - `EncodeOMI`: nel testo tra `>` e `<`, ogni carattere con codice > 126
    diventa un riferimento numerico `&#N;`. L'MDR passato a WMP è quindi ASCII.
    Il nostro server può produrlo già così (lxml con `encoding="us-ascii"`), e
    la nostra pagina non ha bisogno di ricodificare.
- **"Trova informazioni album" su un file** (seconda e terza sessione, FLAC
  scaricato *Alphaville - Big in Japan*). Attenzione: un brano della libreria
  ancora legato a un CD usa `cd=<toc>` come sopra; per vedere il flusso dei file
  serve un brano non estratto da CD.
  1. `GET /FAI/default.aspx?locale=410&geoid=76&version=...&userlocale=410&requestid=<GUID>`
     (GUID generato da WMP, maiuscolo, **senza graffe**) → pagina ponte: form
     `#bridge` (POST, nessuna action) con campo nascosto `mdq`, e lo script
     `window.external.GetMDQByRequestID("<GUID>")` → valore del campo → `submit()`.
  2. `POST` **sulla stessa URL** (query compresa), `application/x-www-form-urlencoded`,
     `Referer` = URL della GET, corpo `mdq=<MDQ-CD url-encoded>`:

     ```xml
     <METADATA>
         <MDQ-CD>
             <mdqRequestID>GUID</mdqRequestID>
             <track>
                 <title><text>Big in Japan</text><word>Big</word><word>in</word><word>Japan</word></title>
                 <artist><text>Alphaville</text><word>Alphaville</word></artist>
                 <trackNumber>3</trackNumber>
                 <filename>03. Alphaville - Big in Japan.flac</filename>
                 <trace>2533 2122 1934 ... 1088</trace>   <!-- 30 interi: impronta audio -->
                 <trackDuration>286000</trackDuration>      <!-- millisecondi -->
                 <bitrate>807728</bitrate>
                 <album>
                     <title><text>Forever Young</text><word>Forever</word><word>Young</word></title>
                     <artist><text>Alphaville</text><word>Alphaville</word></artist>
                 </album>
                 <trackRequestID>0</trackRequestID>         <!-- indice da 0 della traccia -->
             </track>
         </MDQ-CD>
     </METADATA>
     ```

     Una sola traccia selezionata → un solo `<track>`; con più file selezionati
     ci si aspetta più `<track>` con `trackRequestID` 0, 1, ... (da verificare).
     Righe con CRLF e rientri di 4 spazi. Nessun TOC, nessun GUID utente.
  3. Il server Microsoft risponde **`410 Gone`** senza corpo (`Connection:
     close`): è l'"errore 410" che WMP mostra all'utente. Il contatore
     `AutoMetadataCurrentOtherServerErrorCount` non è salito.
  - Il nostro server risponderà alla POST con la **pagina risultati** (stessa
    struttura `mdrcds`/`matches` della pagina CD) e `WriteNamesEx(2, "", mdr, true)`;
    l'MDR dovrà riportare l'`mdqRequestID` ricevuto e, per ogni traccia, il
    `trackRequestID` ricevuto.
- **Contatori dopo le sessioni**: `AutoMetadataCurrentDownloadCount=13`,
  `NetworkErrorCount=3`. Gli errori di rete vengono dai momenti con il redirect
  attivo e il registratore fermo: fare sempre `teardown` a fine sessione.

## Decisioni prese (2026-11-06)

1. Fase 1: registratore con modalità opzionale `--forward` verso il server
   Microsoft reale (risoluzione DNS diretta, ignorando il file hosts), da usare
   solo nelle sessioni di cattura. Default: solo registrazione.
2. Disc ID di riferimento: quelli di Picard.
3. Mirror MusicBrainz rimandato: si parte con l'API pubblica + cache
   (implementata il 2026-11-06). Un accesso SQL remoto al database di
   MusicBrainz non esiste come servizio pubblico. Se l'API non basta si passa a
   un mirror (`musicbrainz-docker`, stessa API `ws/2`): basta cambiare
   `base_url` e `min_interval` in `config.toml`, il codice non cambia.
4. Repository GitHub **privato**.

## Piano di lavoro

Procedere in quest'ordine. Ogni fase si chiude con test verdi.

### Fase 0 — Infrastruttura TLS e redirect

- Script (PowerShell o Python) che:
  - crea una CA locale con name constraints su `musicmatch-ssl.xboxlive.com`
  - emette il certificato del server per quel nome
  - installa la CA in "Autorità di certificazione radice attendibili" del
    computer (richiede conferma dell'utente)
  - aggiunge/rimuove la riga `127.0.0.1 musicmatch-ssl.xboxlive.com` nel file
    hosts, con backup
- Comando di disinstallazione che annulla tutto (CA, certificato, riga hosts).

### Fase 1 — Registratore (prima di scrivere qualsiasi risposta)

- Server HTTPS su 127.0.0.1:443 che **registra tutto** (metodo, percorso, query,
  header, corpo) in file datati, uno per richiesta.
- Risponde in modo neutro (preferibilmente 404 veloce, non 5xx).
- Con `--forward`: inoltra la richiesta al server vero (IP ottenuto con DNS
  diretto, SNI e verifica del certificato sul nome originale), registra anche la
  risposta e la restituisce a WMP. I dati di WMP (TOC, impronte) vanno quindi a
  Microsoft: solo nelle sessioni di cattura.
- Il corpo delle risposte (anche HTML) viene registrato per intero: base64 dei
  byte esatti, più il testo decodificato con il charset dichiarato. I form
  `application/x-www-form-urlencoded` (la POST della pagina ponte) vengono anche
  scomposti nei campi, così l'MDQ nel campo `mdq` si legge in chiaro.
- Obiettivo: catturare `GetMDRCD.aspx?...` con un CD reale e `FAI/default.aspx`
  con "Trova informazioni album" su un file.
- Le catture diventano **fixture di test** (anonimizzando eventuali GUID utente).
- Se WMP non si connette nonostante CA e hosts: verificare certificate pinning,
  criteri di gruppo, impostazioni privacy di WMP ("Recupera informazioni
  aggiuntive da Internet").

### Fase 2 — Dal TOC di WMP al Disc ID di MusicBrainz

- Decodificare il formato `toc` catturato (ipotesi forte dal test zero, vedi sopra).
- Se il Disc ID non è in MusicBrainz: ripiego su `/ws/2/discid/-?toc=...`
  (ricerca per somiglianza sul TOC).
- Calcolare il Disc ID MusicBrainz (algoritmo libdiscid: SHA-1 su prima traccia,
  ultima traccia, lead-out e 99 offset, Base64 con alfabeto `._-`).
- Test con CD reali dell'utente di cui si conosce già il Disc ID (Picard).

**Stato (2026-11-06): fatta.** `src/wmprevival/toc.py` (TOC di WMP ↔ Disc ID,
parametro `toc` di MusicBrainz, durate delle tracce), verificata sui tre CD reali.

### Fase 3 — Risposta per i CD

**Stato (2026-11-06): ricerca fatta, risposta a WMP da fare.**

- `config.py`: `%LOCALAPPDATA%\WMPRevival\config.toml`, sezione `[musicbrainz]`
  con `contact` (obbligatorio per l'API pubblica: l'alias pubblico
  `github@maksimtech.com`, mai l'indirizzo personale), `base_url`, `min_interval`
  (≥ 1 s per l'API pubblica, imposto dal codice).
- `musicbrainz.py`: client aiohttp, una richiesta alla volta distanziate di
  `min_interval`, ritentativi su 503/429 con `Retry-After`, cache SQLite in
  `%LOCALAPPDATA%\WMPRevival\cache` (trovati 30 giorni, 404 un giorno).
- `matching.py`: Disc ID esatto prima; poi ricerca per TOC con `cdstubs=no`; ogni
  medium con lo stesso numero di tracce viene confrontato traccia per traccia
  sulle durate; accettato se lo scarto massimo è ≤ 3 s (o se il Disc ID è
  collegato a quel medium). Ordinamento deterministico.
- `wmprevival mb-lookup <TOC>` mostra candidati e scelta (2 richieste per CD).
- Risultati reali: *Joy* → Giovanni Allevi, *Joy* (2006, IT), due edizioni
  accettate, scarto costante di ~2 s per traccia (le durate in MusicBrainz non
  sembrano includere la pausa tra le tracce); CD da 14 tracce → dei 12
  candidati restano accettati solo pochi, in cima *Das Spieluhren-Orchester -
  Gute Nacht* (DE, scarto medio 0,3 s), **da confermare con l'utente**.
- Fixture delle risposte di MusicBrainz (CC0) in `tests/fixtures/musicbrainz/`,
  registrate con `tools/record_mb_fixture.py`.
- **Risposte a WMP implementate (2026-11-06), da provare su WMP vero** con
  `scripts\servi.cmd` (`wmprevival serve`, opzione `--no-auto`):
  - `mdr.py`: MDR-CD 4.0 con tutti gli elementi del modello di `wmp.dll`;
    `WMCollectionID` = `{MBID release}`, `WMCollectionGroupID` = `{MBID release group}`,
    `WMContentID` = `{MBID recording}` (GUID maiuscoli tra graffe), `trackRequestID`
    ultimo figlio di `<track>`, `releaseDate` come in MusicBrainz, genere più votato
    del release group, etichetta, compositori dalle opere (`performance` → work →
    `composer`, in mancanza `writer`), direttori (`conductor`). XML in ASCII con
    `&#N;` (equivalente di `EncodeOMI`).
  - `GetMDRCD`: MDR della prima scelta, altrimenti la risposta "nessun dato" con la
    forma di quella Microsoft (`Backoff`, `mdqRequestID`, `ResponseCode`).
  - FAI su CD: fino a 3 edizioni accettate dalla scelta per durate, tutte le tracce.
  - FAI su file: nostra pagina ponte (`requestid` validato come GUID), poi ricerca
    `recording:"…" AND artist:"…" AND release:"…"` (senza album se non trova
    niente), ordinamento: titolo uguale e durata entro 5 s obbligatori, poi album
    uguale, numero di traccia, ufficiale, tipo Album, **Digital Media prima**
    (l'utente ha l'edizione digitale `10f85058` di *Forever Young*), data, MBID;
    fino a 5 edizioni; l'MDR contiene solo le tracce richieste con il loro
    `trackRequestID`.
  - Pagine e JS nostri (`src/wmprevival/web/`), ES3; se `WriteNamesEx` solleva
    un errore la pagina lo mostra e lo manda a `/FAI/log` (finisce nelle catture
    e nella console di `serve`).
  - Prova locale con MusicBrainz vero (curl sulla porta 8443): `GetMDRCD` di *Joy*
    1,3 s, FAI del file 8,3 s la prima volta (1 ricerca + 5 release), poi cache.
- Ipotesi da verificare su WMP: formato dei GUID, posizione di `trackRequestID`,
  `releaseDate` solo anno, `mdqRequestID` a zero per i CD, `WriteNamesEx(2, "", …)`
  anche per il CD, tempi di attesa accettati da WMP.

### Prima prova con il nostro server (2026-11-06, `serve` senza `--no-auto`)

- `GetMDRCD` automatico per *Joy* → nostro MDR. Poi "Trova informazioni album"
  sul CD: la pagina ha ricevuto **`wmid=12C5606B-1B33-4AB3-BEC1-F556972B5F89`**,
  cioè il nostro `WMCollectionID` senza graffe: **WMP ha accettato e salvato
  l'MDR automatico**. Scelta l'edizione 1 → `WriteNamesEx` **senza errori**
  (`/FAI/log?kind=WriteNamesEx-ok`). Formato dei GUID tra graffe e
  `mdqRequestID` a zero per i CD: accettati.
- Il CD da 14 tracce ha ricevuto in automatico *Gute Nacht* (Das
  Spieluhren-Orchester): **da confermare con l'utente**.
- Scoperta: **ricerca automatica dei file della libreria** =
  `POST /cdinfo/GetMDRCD.aspx?locale=…&userlocale=410&requestID=<GUID>` (qui
  `requestID` con le maiuscole), `Content-Type: text/xml`, corpo MDQ-CD con
  **`<album>` a livello di `MDQ-CD`** (non dentro le tracce) e più `<track>`
  dello stesso album con `trackRequestID` non in ordine (0, 3, 1, 2). Fixture
  `tests/fixtures/captures/auto_file_post_mdq.json`. Ora gestita: MDR con le sole
  tracce abbinate e i loro `trackRequestID`, solo se anche l'album corrisponde.
- I titoli dei negozi portano marcatori (`[Explicit]`, `(Clean)`) assenti in
  MusicBrainz: tolti per ricerca e confronti (`[Edit]` no: è un'altra versione).
  L'etichetta speciale `[no label]` di MusicBrainz non si scrive.
- Su Windows il ProactorEventLoop stampava un traceback innocuo quando WMP
  chiude la connessione per primo (`_call_connection_lost` → `shutdown`):
  zittito per i soli reset di connessione (`recorder.quiet_connection_resets`).
- Da guardare in WMP: cosa mostra dopo `WriteNamesEx` (titoli, anno, genere,
  rinomina dei file con `fRenameRegroupFiles = true`), e se i file di
  *Mine All Day* vengono aggiornati dalla ricerca automatica.

### WMP conserva un solo MDR per `WMCollectionID` (bug del 2026-11-06)

Segnalazione dell'utente: nell'album *I'm Not Dead* di P!nk le tracce 1, 2 e 3
sono diventate tutte "Long Way to Happy" (traccia 3).

- Sequenza dalle catture: FAI su 12 file → MDR della release `2EBD0F3C` con
  `trackRequestID 0` = "Long Way to Happy"; poi due ricerche automatiche (*Who
  Knew*, *Stupid Girls*), ognuna con il suo file a `trackRequestID 0`: **risposte
  corrette ma con lo stesso `WMCollectionID`**.
- Nel database della libreria (copia) c'è **un solo** documento MDR per
  `2EBD0F3C`: intestazione con l'`mdqRequestID` dell'ultima ricerca automatica,
  ma **tracce del documento della FAI**. WMP aggiorna l'intestazione, tiene le
  tracce vecchie e applica i `trackRequestID` nuovi, che però contano da 0 in
  ogni richiesta. I documenti stanno in UTF-16, riformattati, nella
  `CurrentDatabase_*.wmdb` (anche spezzati tra pagine).
- La pagina FAI invece **sostituisce** il documento (i *Pink Floyd* rifatti più
  volte sono rimasti giusti): il riuso riguarda la ricerca automatica.
- **Regola**: per le risposte sui file `WMCollectionID` è un UUID v5 di
  (release, `mdqRequestID`), diverso per ogni richiesta
  (`service.file_collection_id`); la release resta in `dataProviderParams`
  (`musicbrainz-release=<MBID>`). Per i CD resta l'MBID della release, perché lì
  `trackRequestID` è sempre l'indice nel TOC e quindi stabile.
- Effetti da verificare: raggruppamento degli album in libreria (WMP sembra
  raggruppare per titolo e artista dell'album) e copertine (`AlbumArt_{ID}_*.jpg`
  sono per `WMCollectionID`: fase 6).
- Riparazione dei tag già rovinati: la ricerca prova fino a 3 file e, per
  ciascuno, il titolo del tag e quello ricavato dal **nome del file**
  (`01. Stupid Girls.flac`), sempre con lo scarto di durata ≤ 5 s; lo stesso
  vale per l'abbinamento, che non dà mai la stessa traccia a due file. Al
  massimo 4 ricerche per richiesta. Fixture `fai_file_pink_rovinati.json`.

- Lookup `/ws/2/discid/<id>?inc=recordings+artist-credits+labels+release-groups+work-rels+artist-rels`
  sull'**API pubblica** con cache (mirror rimandato, indirizzo configurabile).
- Se più release corrispondono: scelta deterministica e documentata (paese,
  data, formato CD).
- Mappatura verso MDR-CD (tabella in `docs/mapping.md`):
  `albumTitle`←release title, `albumArtist`←artist credit, `releaseDate`←date,
  `label`←label, `genre`←genere/tag, `trackTitle`, `trackPerformer`,
  `trackComposer` e `trackConductor` dalle relazioni, `period` (da definire),
  `WMContentID`←recording MBID, `WMCollectionID`←release MBID,
  `dataProvider`="MusicBrainz".
- Test golden: richiesta catturata → risposta attesa.

### Fase 4 — Risposta per i file (FAI)

- **Il server serve HTML, non solo XML** (vedi "FAI è un flusso HTML"):
  - `GET /FAI/default.aspx?<param>=<id>` → **pagina ponte**: chiama
    `window.external.GetMDQByRequestID(id)`, mette l'MDQ nel campo nascosto `mdq`
    e fa la POST sulla stessa URL;
  - `POST /FAI/default.aspx` (form con `mdq`) → **pagina dei risultati**:
    parsing MDQ-CD con `defusedxml`, ricerca su MusicBrainz, risposta a WMP via
    `window.external` (presumibilmente `WriteNamesEx(2, ...)`), eventuale scelta
    tra più candidati, `ReturnToMainTask()`.
- Pagine generate con Jinja2 e autoescape (regola 8), con test anti-XSS.
- Pagine adatte al motore HTML di WMP (MSHTML, **modalità documento IE7**,
  verificato): JavaScript ES3, niente `JSON`, `fetch`, `querySelector`,
  `addEventListener`, niente librerie esterne. Servire anche `/FAI/style.css` e
  `/FAI/suggestions.js` nostri.
- La stessa pagina risultati serve anche per "Trova informazioni album" su un
  CD (`?cd=<toc>`), con `WriteNamesEx(2, "", mdr, true)`: va fatta insieme alla
  fase 3.
- Ricerca su MusicBrainz per artista/album/titolo con corrispondenza per durata
  e numero traccia, sull'API pubblica (gli indici del mirror servirebbero solo
  con il mirror).

### Fase 5 — Ripiego Discogs

- Se MusicBrainz non trova niente: ricerca Discogs, stessa mappatura.
- Coda con rispetto del limite di richieste e cache.

### Fase 6 — Copertine

- Scoprire dalle catture come WMP usa `largeCoverParams` / `smallCoverParams`
  (URL completo? parametri accodati a un indirizzo fisso? altro host?).
- Fonte: Cover Art Archive `.../release/<MBID>/front-500`; Discogs ridimensionato
  con lato lungo **massimo 500 px**, mai ingrandito.
- Solo la frontale va a WMP. Gli altri tipi (back, spine, booklet, medium, tray)
  sono fuori dal perimetro del server.

### Fase 7 — Confezionamento

- Avvio come servizio Windows (o attività pianificata), file di configurazione,
  log a rotazione, comando `status` che mostra CA, hosts, porta, ultimo esito.

### Componente separato (facoltativo): watcher per Picard

- Sorveglia la cartella musica; per i FLAC nuovi lancia Picard con i comandi
  `-e LOAD ... -e CLUSTER -e LOOKUP -e SAVE_MATCHED -e QUIT` (Picard ≥ 2.9),
  così WMP trova i file già taggati.

## Mirror MusicBrainz

- Progetto ufficiale `metabrainz/musicbrainz-docker`.
- Senza indici di ricerca: ~2 thread, 4 GB RAM, ~100 GB. Basta per i CD (Disc ID).
- Con indici: ~16 thread, 16 GB RAM, ~350 GB. Serve per FAI e per Picard.
- Replica automatica con token MetaBrainz (gratuito per uso personale); gli
  indici di ricerca vanno ricostruiti periodicamente.
- Destinazione prevista: prima WSL2 sul PC, poi una macchina Debian dedicata.

## Convenzioni del codice

- Python ≥ 3.13 (sviluppo su 3.13, uv). aiohttp per il server e per il client di
  `--forward` (resolver su misura); httpx ammesso per le API esterne.
- Pacchetto `wmprevival` in `src/`, comando `wmprevival`. Dati locali (PKI,
  catture, stato, backup di hosts) in `%LOCALAPPDATA%\WMPRevival`, mai nel repo.
- Identificatori in inglese; commenti, documenti, messaggi della CLI, changelog
  e commit in italiano.
- XML costruito con una libreria (lxml o ElementTree), **mai concatenando
  stringhe**: escaping corretto di titoli con `&`, `<`, apostrofi tipografici.
- Parsing dell'XML in ingresso con protezioni contro entity expansion
  (`defusedxml`).
- Test con pytest su fixture reali catturate; ruff; mypy; CI su GitHub Actions.
- CHANGELOG e versionamento CalVer, come gli altri progetti dell'autore.
- Licenza MIT.

## Domande aperte (da risolvere nell'ordine)

Chiuse dalla prima cattura: formato della query CD (1), metodo per il CD (2,
GET senza corpo), pinning (5, assente), risposta attuale del server (7), metodo
`window.external` della pagina risultati (9, `WriteNamesEx(2, "", mdr, true)`).

1. Com'è fatta una risposta `GetMDRCD` **con** dati che WMP accetta: radice
   `METADATA` con dentro `MDR-CD`? Quale `mdqRequestID`, dato che la richiesta
   GET non ne porta uno? Da provare con il nostro server, partendo dal modello
   MDR-CD 4.0 e dai messaggi d'errore di `wmp.dll`.
2. Dove va `trackRequestID` nell'MDR (nella richiesta MDQ è l'indice da 0 della
   traccia, dentro `<track>`): presumibilmente dentro ogni `<track>` dell'MDR,
   con lo stesso valore.
3. Come vengono risolte le copertine (`largeCoverParams`/`smallCoverParams`).
4. Unità di `<Backoff><Time>` e se WMP lo rispetta.
5. FAI con **più file** selezionati: un MDQ con più `<track>`?
6. Come forzare il nuovo tentativo sugli album già marcati "non trovato"
   (all'avvio WMP li richiede da solo: forse basta riaprirlo).
