@echo off
setlocal EnableDelayedExpansion
title Eche - one-tap setup (beginner)
cd /d "%~dp0"

echo.
echo  ECHE - first-time setup
echo  ==========================
echo  Builds a portable Eche.exe ^(double-click app^).
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [NEED] Python was not found on this PC.
  echo.
  echo   PowerShell ^(exact package id^):
  echo     winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
  echo   Or: https://www.python.org/downloads/  ^(check Add python.exe to PATH^)
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [0/3] Python:
python --version
python -c "import sys; print('  exe:', sys.executable)"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating a private Python folder ^(.venv^)...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv
    echo Tip: try Python 3.11 or 3.12 from python.org, not the Microsoft Store stub.
    pause
    exit /b 1
  )
) else (
  echo [1/3] Using existing .venv
)

echo [2/3] Installing libraries ^(this can take several minutes^)...
".venv\Scripts\python.exe" -m pip install -U pip
if errorlevel 1 (
  echo pip upgrade failed
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed - check your internet connection
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import PyInstaller, PyQt6; print('  PyInstaller + PyQt6 OK')"
if errorlevel 1 (
  echo [FATAL] PyInstaller or PyQt6 still missing after pip install
  pause
  exit /b 1
)

echo [3/3] Building the portable app ^(PyInstaller - may take 2-10 minutes^)...
echo       Watch for errors below. A full log is written to build_pyinstaller.log
echo.
set ECHE_NO_PAUSE=1
call "%~dp0BUILD.bat"
set ERR=!errorlevel!

echo.
if !ERR!==0 (
  echo  SUCCESS
  if exist "%~dp0dist\Eche\Eche.exe" (
    echo  App:  %~dp0dist\Eche\Eche.exe
  )
  if exist "%~dp0..\eche\Eche.exe" (
    echo  Or:   %~dp0..\eche\Eche.exe
  )
  echo  Double-click Eche.exe to open the control panel.
  echo  Keep the _internal folder next to Eche.exe.
) else (
  echo  BUILD reported an error code !ERR!
  echo.
  if exist "%~dp0build_pyinstaller.log" (
    echo  -------- last 30 lines of build_pyinstaller.log --------
    powershell -NoProfile -Command "Get-Content -LiteralPath '%~dp0build_pyinstaller.log' -Tail 30 -ErrorAction SilentlyContinue"
    echo  ----------------------------------------------------------
    echo  Full log: %~dp0build_pyinstaller.log
  ) else (
    echo  No build_pyinstaller.log found - scroll up in this window for details.
  )
  echo.
  echo  Common fixes:
  echo    1. Close any running Eche.exe
  echo    2. Delete folders "build" and "dist" then run this again
  echo    3. Prefer Python 3.12: winget install --id Python.Python.3.12 -e --source winget
  echo    4. Temporarily pause real-time antivirus if it quarantines new EXEs
)
echo.
pause
set "EXITCODE=!ERR!"
exit /b !EXITCODE!
