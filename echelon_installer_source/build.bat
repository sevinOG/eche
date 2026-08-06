@echo off
setlocal EnableDelayedExpansion
title Echelon Installer - Build
REM Always use:  python.exe -m PyInstaller
REM Never call pyinstaller.exe directly — Windows launcher stubs break when the
REM venv folder is renamed (they hardcode the old python path).

set "SCRIPT_DIR=%~dp0"
set "WORKSPACE=%SCRIPT_DIR%.."
set "OUT_EXE=%SCRIPT_DIR%dist\Echelon-Installer.exe"
set "SPEC=%SCRIPT_DIR%build.spec"

echo.
echo  ========================================
echo   ECHELON INSTALLER - BUILD
echo  ========================================
echo.

REM --- Find Python (prefer echelon_source\.venv) ---
set "PY="
if exist "%WORKSPACE%\echelon_source\.venv\Scripts\python.exe" (
  set "PY=%WORKSPACE%\echelon_source\.venv\Scripts\python.exe"
  echo [INFO] Python: echelon_source\.venv
) else if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
  echo [INFO] Python: echelon_installer\.venv
) else (
  where python >nul 2>&1
  if !errorlevel!==0 (
    set "PY=python"
    echo [INFO] Python: system PATH
  )
)

if not defined PY (
  echo [FATAL] No Python found.
  echo   1. Open echelon_source
  echo   2. python -m venv .venv
  echo   3. .venv\Scripts\pip install -r requirements.txt
  echo   4. .venv\Scripts\pip install pyinstaller PyQt6
  pause
  exit /b 1
)

echo [INFO] Using: %PY%

REM --- Ensure PyInstaller is importable (do NOT use pyinstaller.exe) ---
"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [INFO] Installing PyInstaller into this Python...
  "%PY%" -m pip install -q pyinstaller
  if errorlevel 1 (
    echo [FATAL] Could not install PyInstaller
    pause
    exit /b 1
  )
)

REM --- Ensure PyQt6 for the installer GUI freeze ---
"%PY%" -c "import PyQt6" 2>nul
if errorlevel 1 (
  echo [INFO] Installing PyQt6...
  "%PY%" -m pip install -q PyQt6
)

if not exist "%SPEC%" (
  echo [FATAL] Missing build.spec
  pause
  exit /b 1
)

if exist "%SCRIPT_DIR%build" (
  echo [CLEAN] old build\
  rmdir /s /q "%SCRIPT_DIR%build"
)
if exist "%SCRIPT_DIR%dist" (
  echo [CLEAN] old dist\
  rmdir /s /q "%SCRIPT_DIR%dist"
)

echo.
echo [BUILD] python -m PyInstaller ...
pushd "%SCRIPT_DIR%"
"%PY%" -m PyInstaller --noconfirm --clean build.spec
set "ERR=!errorlevel!"
popd
if not "!ERR!"=="0" (
  echo.
  echo [FAIL] PyInstaller failed with error !ERR!
  pause
  exit /b !ERR!
)

if not exist "%OUT_EXE%" (
  echo [FAIL] Expected output missing: %OUT_EXE%
  pause
  exit /b 1
)

for %%A in ("%OUT_EXE%") do set SIZE=%%~zA
set /a SIZE_MB=%SIZE% / 1024 / 1024

if not exist "%SCRIPT_DIR%final" mkdir "%SCRIPT_DIR%final"
copy /y "%OUT_EXE%" "%SCRIPT_DIR%final\Echelon-Installer.exe" >nul
echo 1.1.1 > "%SCRIPT_DIR%final\VERSION"
echo Echelon Installer - run Echelon-Installer.exe > "%SCRIPT_DIR%final\README.txt"

echo.
echo  ========================================
echo   BUILD SUCCESS
echo  ========================================
echo   %OUT_EXE%
echo   Size: ~%SIZE_MB% MB
echo   Also: final\Echelon-Installer.exe
echo.
echo   Next: double-click install.bat  (or the exe above)
echo.
pause
endlocal
