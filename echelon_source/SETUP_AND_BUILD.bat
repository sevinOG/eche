@echo off
setlocal
title Echelon - one-tap setup (beginner)
cd /d "%~dp0"

echo.
echo  ECHELON — first-time setup
echo  ==========================
echo  For people who have never used GitHub or AI tools.
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [NEED] Python was not found on this PC.
  echo.
  echo   1. Open https://www.python.org/downloads/
  echo   2. Install Python 3.11 or newer
  echo   3. CHECK THE BOX: "Add python.exe to PATH"
  echo   4. Close this window, open it again, run SETUP_AND_BUILD.bat
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating a private Python folder ^(.venv^)...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
)

echo [2/3] Installing libraries ^(this can take several minutes^)...
".venv\Scripts\python.exe" -m pip install -U pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed — check your internet connection
  pause
  exit /b 1
)

echo [3/3] Building the portable app...
set ECHELON_NO_PAUSE=1
call BUILD.bat
set ERR=%errorlevel%

echo.
if %ERR%==0 (
  echo  SUCCESS
  echo  Look for:  dist\Echelon\Echelon.exe
  echo  Or:        ..\echelon\Echelon.exe
  echo  Double-click Echelon.exe to open the control panel.
) else (
  echo  BUILD reported an error code %ERR%
  echo  Scroll up for details, or open an Issue on GitHub.
)
echo.
pause
endlocal
exit /b %ERR%
