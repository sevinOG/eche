@echo off
setlocal
cd /d "%~dp0"
title Eche

rem Prefer a frozen portable build if present
if exist "Eche.exe" (
  start "" "Eche.exe"
  exit /b 0
)
if exist "dist\Eche\Eche.exe" (
  start "" "dist\Eche\Eche.exe"
  exit /b 0
)
if exist "dist\Eche.exe" (
  start "" "dist\Eche.exe"
  exit /b 0
)

rem Source mode: run the GUI with Python
if exist ".venv\Scripts\python.exe" (
  echo Refreshing dependencies...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo pip install failed — check internet / requirements.txt
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" "eche_app.py"
  if errorlevel 1 pause
  exit /b %errorlevel%
)

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Python is required to run Eche from source.
  echo  Option A: Install Python 3.11+ from python.org
  echo            ^(check Add python.exe to PATH^)
  echo  Option B: Double-click SETUP_AND_BUILD.bat once
  echo            to install deps and build Eche.exe
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

echo Creating private .venv and installing libraries...
python -m venv .venv
if errorlevel 1 ( echo venv failed & pause & exit /b 1 )
".venv\Scripts\python.exe" -m pip install -U pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed — check internet
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "eche_app.py"
if errorlevel 1 pause
exit /b %errorlevel%