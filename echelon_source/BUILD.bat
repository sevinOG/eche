@echo off
setlocal EnableDelayedExpansion
title Echelon - Build portable app
REM Source-only tree → clean portable ../echelon/ (no Python source in portable)
REM Always: python -m PyInstaller  (never pyinstaller.exe)

set "SCRIPT_DIR=%~dp0"
REM If this bat was copied into portable echelon\, jump to real source tree
if exist "%SCRIPT_DIR%install.json" if exist "%SCRIPT_DIR%..\echelon_source\BUILD.bat" (
  if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    echo [INFO] This BUILD.bat is inside portable echelon\ — redirecting to echelon_source\
    call "%SCRIPT_DIR%..\echelon_source\BUILD.bat" %*
    exit /b !errorlevel!
  )
)
if /I "%~nx0"=="BUILD.bat" if exist "%SCRIPT_DIR%..\echelon_source\BUILD.bat" (
  if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" if exist "%SCRIPT_DIR%..\echelon_source\.venv\Scripts\python.exe" (
    if not exist "%SCRIPT_DIR%build_exe.spec" (
      echo [INFO] Redirecting build to sibling echelon_source\
      call "%SCRIPT_DIR%..\echelon_source\BUILD.bat" %*
      exit /b !errorlevel!
    )
  )
)

set "WORKSPACE=%SCRIPT_DIR%.."
set "PORTABLE=%WORKSPACE%\echelon"
set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PY%" if exist "%SCRIPT_DIR%..\echelon_source\.venv\Scripts\python.exe" (
  set "PY=%SCRIPT_DIR%..\echelon_source\.venv\Scripts\python.exe"
)
set "BUILD_OUT=%SCRIPT_DIR%dist\Echelon"
set "NOPAUSE=%ECHELON_NO_PAUSE%"

echo.
echo  ========================================
echo   ECHELON SOURCE → PORTABLE APP
echo  ========================================
echo   From: %SCRIPT_DIR%
echo   To:   %PORTABLE%
echo   Python: %PY%
echo.

if not exist "%SCRIPT_DIR%build_exe.spec" (
  echo [FATAL] Not a source tree — missing build_exe.spec
  echo   Run BUILD.bat from echelon_source\  (not from portable echelon\)
  if not defined NOPAUSE pause
  exit /b 1
)

if not exist "%PY%" (
  echo [FATAL] Missing virtualenv at:
  echo   %SCRIPT_DIR%.venv\Scripts\python.exe
  echo.
  echo   Open echelon_source and run:
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo     BUILD.bat
  if not defined NOPAUSE pause
  exit /b 1
)

echo [1/5] Dependencies...
"%PY%" -m pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 echo [WARN] pip had issues — continuing
"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [INFO] Installing PyInstaller...
  "%PY%" -m pip install -q pyinstaller
)

echo [2/5] Freezing with: python -m PyInstaller
pushd "%SCRIPT_DIR%"
"%PY%" -m PyInstaller --noconfirm --clean build_exe.spec
set "ERR=!errorlevel!"
popd
if not "!ERR!"=="0" (
  echo [FATAL] PyInstaller failed ^(!ERR!^)
  if not defined NOPAUSE pause
  exit /b !ERR!
)

if not exist "%BUILD_OUT%\Echelon.exe" (
  echo [FATAL] Missing %BUILD_OUT%\Echelon.exe
  if not defined NOPAUSE pause
  exit /b 1
)

REM Stage brand icons next to onedir exe as well as under assets/
if exist "%SCRIPT_DIR%assets\icon.png" (
  if not exist "%BUILD_OUT%\assets" mkdir "%BUILD_OUT%\assets"
  copy /y "%SCRIPT_DIR%assets\icon.png" "%BUILD_OUT%\assets\icon.png" >nul
  copy /y "%SCRIPT_DIR%assets\icon.png" "%BUILD_OUT%\icon.png" >nul
  if exist "%BUILD_OUT%\_internal" (
    if not exist "%BUILD_OUT%\_internal\assets" mkdir "%BUILD_OUT%\_internal\assets"
    copy /y "%SCRIPT_DIR%assets\icon.png" "%BUILD_OUT%\_internal\assets\icon.png" >nul
  )
)
if exist "%SCRIPT_DIR%assets\icon.ico" (
  if not exist "%BUILD_OUT%\assets" mkdir "%BUILD_OUT%\assets"
  copy /y "%SCRIPT_DIR%assets\icon.ico" "%BUILD_OUT%\assets\icon.ico" >nul
  copy /y "%SCRIPT_DIR%assets\icon.ico" "%BUILD_OUT%\icon.ico" >nul
  if exist "%BUILD_OUT%\_internal" (
    if not exist "%BUILD_OUT%\_internal\assets" mkdir "%BUILD_OUT%\_internal\assets"
    copy /y "%SCRIPT_DIR%assets\icon.ico" "%BUILD_OUT%\_internal\assets\icon.ico" >nul
  )
)

echo [3/5] Scrubbing source leftovers from portable tree...
if not exist "%PORTABLE%" mkdir "%PORTABLE%"

REM Remove any accidental source / build junk (keep user data folders)
for %%N in (
  cogs core gui dist build __pycache__ .venv
  BUILD.bat package_portable.bat build_exe.spec echelon_app.py
  rthook_echelon.py requirements.txt README.md Uninstall.exe
  __init__.py .echelon_install_manifest.json .echelon_launch_path
) do (
  if exist "%PORTABLE%\%%N" (
    rmdir /s /q "%PORTABLE%\%%N" 2>nul
    del /f /q "%PORTABLE%\%%N" 2>nul
  )
)

echo [4/5] Publishing freeze → portable (Echelon.exe + _internal)...
if exist "%PORTABLE%\Echelon.exe" del /f /q "%PORTABLE%\Echelon.exe"
if exist "%PORTABLE%\_internal" rmdir /s /q "%PORTABLE%\_internal"

robocopy "%BUILD_OUT%" "%PORTABLE%" /E /NFL /NDL /NJH /NJS /nc /ns /np /XF Uninstall.exe
if errorlevel 8 (
  echo [FATAL] robocopy failed
  if not defined NOPAUSE pause
  exit /b 1
)

REM User-data dirs (never wipe config/cookies)
for %%D in (assets config cookies logs context memories) do (
  if not exist "%PORTABLE%\%%D" mkdir "%PORTABLE%\%%D"
)

REM Force brand icons at portable root + assets + _internal
if exist "%SCRIPT_DIR%assets\icon.png" (
  copy /y "%SCRIPT_DIR%assets\icon.png" "%PORTABLE%\assets\icon.png" >nul
  copy /y "%SCRIPT_DIR%assets\icon.png" "%PORTABLE%\icon.png" >nul
  if exist "%PORTABLE%\_internal" (
    if not exist "%PORTABLE%\_internal\assets" mkdir "%PORTABLE%\_internal\assets"
    copy /y "%SCRIPT_DIR%assets\icon.png" "%PORTABLE%\_internal\assets\icon.png" >nul
  )
)
if exist "%SCRIPT_DIR%assets\icon.ico" (
  copy /y "%SCRIPT_DIR%assets\icon.ico" "%PORTABLE%\assets\icon.ico" >nul
  copy /y "%SCRIPT_DIR%assets\icon.ico" "%PORTABLE%\icon.ico" >nul
  if exist "%PORTABLE%\_internal" (
    if not exist "%PORTABLE%\_internal\assets" mkdir "%PORTABLE%\_internal\assets"
    copy /y "%SCRIPT_DIR%assets\icon.ico" "%PORTABLE%\_internal\assets\icon.ico" >nul
  )
)
if exist "%SCRIPT_DIR%VERSION" copy /y "%SCRIPT_DIR%VERSION" "%PORTABLE%\VERSION" >nul

> "%PORTABLE%\install.json" (
  echo {
  echo   "kind": "portable_app",
  echo   "version": "1.1.1",
  echo   "built_from": "echelon_source"
  echo }
)

> "%PORTABLE%\README.txt" (
  echo ECHELON portable app — no Python source here.
  echo Double-click Echelon.exe. Copy this whole folder to USB.
  echo Source lives in sibling echelon_source\. Built by BUILD.bat.
)

REM Final guard: refuse if source folders reappeared
if exist "%PORTABLE%\core" if exist "%PORTABLE%\gui" if not exist "%PORTABLE%\Echelon.exe" (
  echo [WARN] Portable looks like source without exe — something went wrong
)

if not exist "%PORTABLE%\Echelon.exe" (
  echo [FATAL] Portable missing Echelon.exe after publish
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%PORTABLE%\assets\icon.png" (
  echo [WARN] assets\icon.png missing in portable — window icon may be blank
) else (
  echo [OK] assets\icon.png present
)

echo [5/5] Done.
echo.
echo   Portable: %PORTABLE%\Echelon.exe
echo   Icons:    %PORTABLE%\assets\icon.png
echo.
if not defined NOPAUSE pause
endlocal
exit /b 0
