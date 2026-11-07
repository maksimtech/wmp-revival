@echo off
rem WMP Revival test session: status, setup, server with MusicBrainz data, teardown.
rem Run from a command prompt opened as administrator.
rem Arguments are passed to "wmprevival serve", e.g.:  serve.cmd --no-auto
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set UV_LINK_MODE=copy

net session >nul 2>&1
if errorlevel 1 (
  echo This needs a command prompt opened as administrator.
  exit /b 1
)

echo User: %USERDOMAIN%\%USERNAME%
echo Data: %LOCALAPPDATA%\WMPRevival
echo.

:wmp
tasklist /FI "IMAGENAME eq wmplayer.exe" | find /I "wmplayer.exe" >nul
if not errorlevel 1 (
  echo Windows Media Player is open: close it, then press a key.
  pause >nul
  goto wmp
)

uv run wmprevival status || exit /b 1
echo.
uv run wmprevival setup || exit /b 1
echo.
echo ===================================================================
echo  The server starts now and answers with MusicBrainz data.
echo   1. open Windows Media Player (it looks up known CDs by itself);
echo   2. CD: right-click the album, "Find album info",
echo      pick the edition on the right and press Finish;
echo   3. file: right-click the track, "Find album info".
echo  If WMP rejects the data, the page shows the error and a
echo  /FAI/log line with the code appears here.
echo  To stop: Ctrl+C, then answer N to "Terminate batch job (Y/N)?".
echo ===================================================================
echo.
uv run wmprevival serve %*

echo.
echo With no server running, WMP with the redirect finds the port closed.
choice /C YN /M "Undo the setup now (recommended)"
if errorlevel 2 goto end
uv run wmprevival teardown

:end
echo.
echo Captures in %LOCALAPPDATA%\WMPRevival\captures
endlocal
