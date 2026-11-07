@echo off
rem WMP Revival capture session: status, setup, recorder with --forward, teardown.
rem Run from a command prompt opened as administrator.
rem --forward: TOCs and audio fingerprints sent by WMP also reach Microsoft.
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
echo It must be your own user, not another administrator account.
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
echo  The recorder starts now. Then, in this order:
echo   1. open Windows Media Player and insert the CD (Joy);
echo   2. wait for WMP to finish, then right-click the album:
echo      "Find album info";
echo   3. in the library, right-click a music file:
echo      "Find album info".
echo  To stop: Ctrl+C, then answer N to "Terminate batch job (Y/N)?".
echo ===================================================================
echo.
uv run wmprevival record --forward

echo.
echo With no recorder running, WMP with the redirect finds the port closed.
choice /C YN /M "Undo the setup now (recommended if you aren't capturing anything else)"
if errorlevel 2 goto end
uv run wmprevival teardown

:end
echo.
echo Captures in %LOCALAPPDATA%\WMPRevival\captures
endlocal
