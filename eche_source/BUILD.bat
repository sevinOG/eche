@echo off
setlocal EnableDelayedExpansion
title Eche - Build portable app
REM Source tree -> portable ../eche/ (onedir: Eche.exe + _internal)
REM Always: python -m PyInstaller  (never pyinstaller.exe)

set "SCRIPT_DIR=%~dp0"
REM If this bat was copied into portable eche\, jump to real source tree
if exist "%SCRIPT_DIR%install.json" if exist "%SCRIPT_DIR%..\eche_source\BUILD.bat" (
  if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    echo [INFO] This BUILD.bat is inside portable eche\ - redirecting to eche_source\
    call "%SCRIPT_DIR%..\eche_source\BUILD.bat" %*
    exit /b !errorlevel!
  )
)
if /I "%~nx0"=="BUILD.bat" if exist "%SCRIPT_DIR%..\eche_source\BUILD.bat" (
  if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" if exist "%SCRIPT_DIR%..\eche_source\.venv\Scripts\python.exe" (
    if not exist "%SCRIPT_DIR%build_exe.spec" (
      echo [INFO] Redirecting build to sibling eche_source\
      call "%SCRIPT_DIR%..\eche_source\BUILD.bat" %*
      exit /b !errorlevel!
    )
  )
)

set "WORKSPACE=%SCRIPT_DIR%.."
set "PORTABLE=%WORKSPACE%\eche"
set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PY%" if exist "%SCRIPT_DIR%..\eche_source\.venv\Scripts\python.exe" (
  set "PY=%SCRIPT_DIR%..\eche_source\.venv\Scripts\python.exe"
)
set "BUILD_OUT=%SCRIPT_DIR%dist\Eche"
set "LOG=%SCRIPT_DIR%build_pyinstaller.log"
set "NOPAUSE=%ECHE_NO_PAUSE%"

echo.
echo  ========================================
echo   ECHE SOURCE -^> PORTABLE APP
echo  ========================================
echo   From: %SCRIPT_DIR%
echo   To:   %PORTABLE%
echo   Python: %PY%
echo   Log:  %LOG%
echo.

if not exist "%SCRIPT_DIR%build_exe.spec" (
  echo [FATAL] Not a source tree - missing build_exe.spec
  echo   Run BUILD.bat from eche_source\  ^(not from portable eche\^)
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%SCRIPT_DIR%eche_app.py" (
  echo [FATAL] Missing eche_app.py - wrong folder?
  if not defined NOPAUSE pause
  exit /b 1
)
if not exist "%SCRIPT_DIR%rthook_eche.py" (
  echo [FATAL] Missing rthook_eche.py
  if not defined NOPAUSE pause
  exit /b 1
)

if not exist "%PY%" (
  echo [FATAL] Missing virtualenv at:
  echo   %SCRIPT_DIR%.venv\Scripts\python.exe
  echo.
  echo   Run SETUP_AND_BUILD.bat first, or:
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo     BUILD.bat
  if not defined NOPAUSE pause
  exit /b 1
)

echo [1/5] Dependencies...
"%PY%" -m pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 echo [WARN] pip had issues - continuing
"%PY%" -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)" 2>nul
if errorlevel 1 (
  echo [INFO] Installing PyInstaller...
  "%PY%" -m pip install -q "pyinstaller>=6.0"
)
"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [FATAL] PyInstaller still not importable after install.
  if not defined NOPAUSE pause
  exit /b 1
)
"%PY%" -c "import PyQt6" 2>nul
if errorlevel 1 (
  echo [FATAL] PyQt6 missing - pip install -r requirements.txt failed or incomplete.
  if not defined NOPAUSE pause
  exit /b 1
)

REM Runtime folders are gitignored - create them so PyInstaller datas= do not fail
for %%D in (context cookies logs memories) do (
  if not exist "%SCRIPT_DIR%%%D" mkdir "%SCRIPT_DIR%%%D"
  if not exist "%SCRIPT_DIR%%%D\.gitkeep" type nul > "%SCRIPT_DIR%%%D\.gitkeep"
)

echo [2/5] Freezing with: python -m PyInstaller
echo        Full log: %LOG%
echo        ^(this can take several minutes - output streams below^)
pushd "%SCRIPT_DIR%"
if exist "%LOG%" del /f /q "%LOG%" >nul 2>&1
REM Stream to console + log (PowerShell Tee-Object). Fall back to log-only.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue'; & '%PY:\=\\%' -u -m PyInstaller --noconfirm --clean build_exe.spec 2>&1 | Tee-Object -FilePath '%LOG:\=\\%'; $code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }; exit $code"
set "ERR=!errorlevel!"
if not exist "%LOG%" (
  echo [WARN] Tee failed - retrying PyInstaller with log only...
  "%PY%" -u -m PyInstaller --noconfirm --clean build_exe.spec > "%LOG%" 2>&1
  set "ERR=!errorlevel!"
)
popd

if not "!ERR!"=="0" (
  echo.
  echo [FATAL] PyInstaller failed ^(exit !ERR!^)
  echo -------- last 40 lines of log --------
  powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 40 -ErrorAction SilentlyContinue"
  echo ----------------------------------------
  echo Full log saved: %LOG%
  echo Common fixes:
  echo   - Close any running Eche.exe / antivirus lock
  echo   - Delete build\ and dist\ then re-run
  echo   - Use Python 3.11 or 3.12 ^(not store-only sandbox^)
  echo   - Set ECHE_DEBUG=1 is unrelated; re-run SETUP_AND_BUILD.bat
  if not defined NOPAUSE pause
  exit /b !ERR!
)

if not exist "%BUILD_OUT%\Eche.exe" (
  echo [FATAL] PyInstaller reported success but missing:
  echo   %BUILD_OUT%\Eche.exe
  echo -------- last 40 lines of log --------
  powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 40 -ErrorAction SilentlyContinue"
  echo Full log: %LOG%
  if not defined NOPAUSE pause
  exit /b 1
)
echo [OK] Freeze: %BUILD_OUT%\Eche.exe

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
if not exist "%PORTABLE%" (
  mkdir "%PORTABLE%" 2>nul
)
if not exist "%PORTABLE%" (
  echo [WARN] Cannot create %PORTABLE% - will use dist\Eche only
  set "PORTABLE="
)

if defined PORTABLE (
  for %%N in (
    cogs core gui dist build __pycache__ .venv
    BUILD.bat package_portable.bat build_exe.spec eche_app.py
    rthook_eche.py requirements.txt README.md Uninstall.exe
    __init__.py .eche_install_manifest.json .eche_launch_path
  ) do (
    if exist "%PORTABLE%\%%N" (
      rmdir /s /q "%PORTABLE%\%%N" 2>nul
      del /f /q "%PORTABLE%\%%N" 2>nul
    )
  )

  echo [4/5] Publishing freeze -^> portable ^(Eche.exe + _internal^)...
  if exist "%PORTABLE%\Eche.exe" (
    del /f /q "%PORTABLE%\Eche.exe" 2>nul
    if exist "%PORTABLE%\Eche.exe" (
      echo [WARN] Could not delete old Eche.exe - is it running? Close it and retry.
    )
  )
  if exist "%PORTABLE%\_internal" rmdir /s /q "%PORTABLE%\_internal" 2>nul

  robocopy "%BUILD_OUT%" "%PORTABLE%" /E /NFL /NDL /NJH /NJS /nc /ns /np /XF Uninstall.exe
  set "RC=!errorlevel!"
  if !RC! GEQ 8 (
    echo [WARN] robocopy exit !RC! - portable publish incomplete
    echo        You can still run: %BUILD_OUT%\Eche.exe
  ) else (
    for %%D in (assets config cookies logs context memories) do (
      if not exist "%PORTABLE%\%%D" mkdir "%PORTABLE%\%%D"
    )
    if exist "%SCRIPT_DIR%assets\icon.png" (
      copy /y "%SCRIPT_DIR%assets\icon.png" "%PORTABLE%\assets\icon.png" >nul
      copy /y "%SCRIPT_DIR%assets\icon.png" "%PORTABLE%\icon.png" >nul
    )
    if exist "%SCRIPT_DIR%assets\icon.ico" (
      copy /y "%SCRIPT_DIR%assets\icon.ico" "%PORTABLE%\assets\icon.ico" >nul
      copy /y "%SCRIPT_DIR%assets\icon.ico" "%PORTABLE%\icon.ico" >nul
    )
    if exist "%SCRIPT_DIR%VERSION" copy /y "%SCRIPT_DIR%VERSION" "%PORTABLE%\VERSION" >nul
    > "%PORTABLE%\install.json" (
      echo {
      echo   "kind": "portable_app",
      echo   "version": "1.3.0",
      echo   "built_from": "eche_source"
      echo }
    )
    > "%PORTABLE%\README.txt" (
      echo ECHE portable app - no Python source here.
      echo Double-click Eche.exe. Copy this whole folder to USB.
      echo Source lives in sibling eche_source\. Built by BUILD.bat.
    )
  )
) else (
  echo [4/5] Skipping sibling portable publish ^(folder not creatable^)
)

echo [5/5] Done.
echo.
if exist "%BUILD_OUT%\Eche.exe" (
  echo   Freeze OK:  %BUILD_OUT%\Eche.exe
)
if defined PORTABLE if exist "%PORTABLE%\Eche.exe" (
  echo   Portable:   %PORTABLE%\Eche.exe
  if exist "%PORTABLE%\assets\icon.png" echo   Icons:      OK
) else (
  echo   Portable sibling missing - use dist\Eche\ above
)
echo   Log:        %LOG%
echo.

REM Success if freeze exists (portable publish is best-effort)
if not exist "%BUILD_OUT%\Eche.exe" (
  echo [FATAL] No Eche.exe produced
  if not defined NOPAUSE pause
  exit /b 1
)

if not defined NOPAUSE pause
endlocal
exit /b 0
