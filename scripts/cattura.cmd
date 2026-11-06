@echo off
rem Sessione di cattura WMP Revival: status, setup, registratore con --forward, teardown.
rem Da lanciare in un prompt dei comandi aperto come amministratore.
rem --forward: TOC e impronte audio inviati da WMP arrivano anche a Microsoft.
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set UV_LINK_MODE=copy

net session >nul 2>&1
if errorlevel 1 (
  echo Serve un prompt dei comandi aperto come amministratore.
  exit /b 1
)

echo Utente: %USERDOMAIN%\%USERNAME%
echo Dati:   %LOCALAPPDATA%\WMPRevival
echo Deve essere il tuo utente, non un altro account amministratore.
echo.

:wmp
tasklist /FI "IMAGENAME eq wmplayer.exe" | find /I "wmplayer.exe" >nul
if not errorlevel 1 (
  echo Windows Media Player e' aperto: chiudilo, poi premi un tasto.
  pause >nul
  goto wmp
)

uv run wmprevival status || exit /b 1
echo.
uv run wmprevival setup || exit /b 1
echo.
echo ===================================================================
echo  Il registratore parte adesso. Poi, in quest'ordine:
echo   1. apri Windows Media Player e inserisci il CD (Joy);
echo   2. aspetta che WMP finisca, poi tasto destro sull'album:
echo      "Trova informazioni album";
echo   3. nella libreria, tasto destro su un file musicale:
echo      "Trova informazioni album".
echo  Per fermare: Ctrl+C, poi rispondi N a "Terminare il processo batch".
echo ===================================================================
echo.
uv run wmprevival record --forward

echo.
echo Senza registratore attivo, WMP con il redirect trova la porta chiusa.
choice /C SN /M "Annullo il setup adesso (consigliato se non catturi altro)"
if errorlevel 2 goto fine
uv run wmprevival teardown

:fine
echo.
echo Catture in %LOCALAPPDATA%\WMPRevival\captures
endlocal
