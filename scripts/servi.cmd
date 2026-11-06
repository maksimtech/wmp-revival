@echo off
rem Sessione di prova WMP Revival: status, setup, server con i dati di MusicBrainz, teardown.
rem Da lanciare in un prompt dei comandi aperto come amministratore.
rem Argomenti passati a "wmprevival serve", es.:  servi.cmd --no-auto
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
echo  Il server parte adesso e risponde con i dati di MusicBrainz.
echo   1. apri Windows Media Player (cerca da solo i CD gia' visti);
echo   2. CD: tasto destro sull'album, "Trova informazioni album",
echo      scegli l'edizione a destra e premi Fine;
echo   3. file: tasto destro sul brano, "Trova informazioni album".
echo  Se WMP rifiuta i dati, la pagina mostra l'errore e qui compare
echo  una riga /FAI/log con il codice.
echo  Per fermare: Ctrl+C, poi rispondi N a "Terminare il processo batch".
echo ===================================================================
echo.
uv run wmprevival serve %*

echo.
echo Senza server attivo, WMP con il redirect trova la porta chiusa.
choice /C SN /M "Annullo il setup adesso (consigliato)"
if errorlevel 2 goto fine
uv run wmprevival teardown

:fine
echo.
echo Catture in %LOCALAPPDATA%\WMPRevival\captures
endlocal
