@echo off
setlocal
title Eche Installer - Launch
set "SCRIPT_DIR=%~dp0"
set "EXE=%SCRIPT_DIR%dist\Eche-Installer.exe"
set "FINAL=%SCRIPT_DIR%final\Eche-Installer.exe"

echo.
echo  Eche Installer
echo  -----------------
echo.

if not exist "%EXE%" if exist "%FINAL%" set "EXE=%FINAL%"

if not exist "%EXE%" (
  echo Installer not built yet. Building now...
  echo.
  call "%SCRIPT_DIR%build.bat"
  if errorlevel 1 exit /b 1
  set "EXE=%SCRIPT_DIR%dist\Eche-Installer.exe"
)

if not exist "%EXE%" (
  echo [FATAL] Still no installer exe. See build.bat output.
  pause
  exit /b 1
)

echo Launching: %EXE%
start "" "%EXE%"
endlocal
