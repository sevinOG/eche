"""Stage an Eche update, then hand the file replace to a process that outlives the app.

Windows will not replace Eche.exe, or the DLLs it has open, while this process
is still running. The handoff script waits for this PID to exit, copies source,
runs BUILD.bat, and starts Eche again.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

GITHUB_OWNER = "sevinOG"
GITHUB_REPO = "eche"
GITHUB_BRANCH = "main"
USER_AGENT = "Eche/2.1.0 (+https://github.com/sevinOG/eche)"

_APP_CANDIDATES = ("eche_source", "echelon_source", "eche-source", "echelon-source")
_SKIP_DIRS = {".venv", "__pycache__", "dist", "build", ".git", "cookies", "logs", "memories", "context"}
_SKIP_FILES = {"settings.json", "secrets.dpapi.json"}


def archive_zip_url(branch: str = GITHUB_BRANCH) -> str:
    return (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
        f"archive/refs/heads/{branch}.zip"
    )


def _looks_like_app_source(dest: Path) -> bool:
    if not (dest / "core").is_dir():
        return False
    if not ((dest / "eche_app.py").is_file() or (dest / "BUILD.bat").is_file()):
        return False
    return (dest / "gui").is_dir() or (dest / "cogs").is_dir() or (dest / "core" / "eche.py").is_file()


def extract_app_source_zip(raw: bytes, dest: str | Path) -> Path:
    """Extract only the app source folder from a GitHub repo zip."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("GitHub archive was empty.")
        root = names[0].split("/")[0] + "/"
        chosen = None
        prefix = None
        for cand in _APP_CANDIDATES:
            trial = f"{root}{cand}/"
            if any(name.startswith(trial) for name in names):
                chosen = cand
                prefix = trial
                break
        if not chosen or not prefix:
            raise RuntimeError(
                "The GitHub archive has no eche_source folder. "
                "Expected core/, gui/, and BUILD.bat."
            )
        if "installer" in chosen:
            raise RuntimeError("Refusing to update from installer source.")
        count = 0
        for name in names:
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            top = rel.replace("\\", "/").split("/")[0]
            if top in _SKIP_DIRS or top in {"eche_installer_source"}:
                continue
            if Path(rel).name in _SKIP_FILES:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
        if count == 0:
            raise RuntimeError("GitHub archive contained no application files.")
    if not _looks_like_app_source(dest):
        raise RuntimeError(
            "Downloaded files do not look like Eche source "
            "(need core/, gui/ or cogs/, and BUILD.bat)."
        )
    return dest


def stage_github_source(dest: str | Path, log=None) -> Path:
    """Download main and extract eche_source into dest. Does not touch the running app."""
    log = log or (lambda _m: None)
    url = archive_zip_url()
    log(f"Downloading {url}")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=180) as resp:
        raw = resp.read()
    log(f"Downloaded {len(raw):,} bytes. Extracting application source…")
    found = extract_app_source_zip(raw, dest)
    log(f"Staged source at {found}")
    return found


def _safe_path(path: str) -> str:
    text = os.path.abspath(path)
    if any(ch in text for ch in ('\n', '\r', '"', '&', '|', '>', '<', '%', '^', '!')):
        raise ValueError(f"Cannot put this path in the update script: {text}")
    return text


def relaunch_command(source_dir: str) -> str:
    """Best exe or launcher to open after BUILD.bat."""
    source = os.path.abspath(source_dir)
    parent = os.path.dirname(source)
    candidates = [
        os.path.join(parent, "eche", "Eche.exe"),
        os.path.join(source, "dist", "Eche", "Eche.exe"),
        os.path.join(source, "Eche.exe"),
        os.path.join(source, "RUN_ECHE.bat"),
    ]
    frozen = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else ""
    if frozen and os.path.basename(frozen).lower().startswith("eche"):
        candidates.insert(0, frozen)
    for path in candidates:
        if os.path.isfile(path):
            return path
    return os.path.join(parent, "eche", "Eche.exe")


def default_source_target(stored: str | None = None) -> str:
    """Folder the handoff should update. Creates nothing."""
    try:
        from core.paths import package_root, source_root
    except Exception:
        package_root = os.getcwd  # type: ignore
        source_root = lambda _s=None: None  # type: ignore
    found = source_root(stored)
    if found:
        return found
    root = package_root()
    sibling = os.path.join(os.path.dirname(os.path.abspath(root)), "eche_source")
    if os.path.isdir(os.path.join(root, "core")) and os.path.isfile(os.path.join(root, "BUILD.bat")):
        return os.path.abspath(root)
    return sibling


def write_handoff_script(
    *,
    pid: int,
    target: str,
    relaunch: str,
    stage: str | None = None,
) -> str:
    """Write a cmd script that waits for pid, applies the update, and starts Eche."""
    target_s = _safe_path(target)
    relaunch_s = _safe_path(relaunch)
    stage_s = _safe_path(stage) if stage else ""
    if stage_s and os.path.normcase(stage_s) == os.path.normcase(target_s):
        stage_s = ""
    fd, path = tempfile.mkstemp(prefix="eche-update-", suffix=".bat")
    os.close(fd)
    skip_dirs = " ".join(_SKIP_DIRS)
    skip_files = " ".join(_SKIP_FILES)
    stage_block = ""
    if stage_s:
        stage_block = f"""
echo Copying source into:
echo   {target_s}
if not exist "{target_s}" mkdir "{target_s}"
robocopy "{stage_s}" "{target_s}" /E /NFL /NDL /NJH /NJS /nc /ns /np /XD {skip_dirs} /XF {skip_files}
if !errorlevel! GEQ 8 (
  echo [Eche] Could not copy the staged source. Robocopy code !errorlevel!.
  pause
  exit /b 1
)
"""
    script = f"""@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Eche update
echo.
echo  Eche closed so this window can replace the running files.
echo  Leave this window open. It will start Eche when the update finishes.
echo.
set "PID={int(pid)}"
echo Waiting for Eche to exit ^(PID !PID!^)...
:wait
tasklist /FI "PID eq !PID!" /NH 2>nul | findstr /C:"!PID!" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
echo Eche has exited.
{stage_block}
if not exist "{target_s}\\BUILD.bat" (
  echo [Eche] BUILD.bat is missing in:
  echo   {target_s}
  pause
  exit /b 1
)
cd /d "{target_s}"
set "ECHE_NO_PAUSE=1"
if not exist ".venv\\Scripts\\python.exe" (
  echo Creating .venv and installing requirements. This can take a few minutes.
  py -3 -m venv .venv
  if errorlevel 1 python -m venv .venv
  if not exist ".venv\\Scripts\\python.exe" (
    echo [Eche] Python was not found. Install Python 3.12 and run this update again.
    pause
    exit /b 1
  )
  ".venv\\Scripts\\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [Eche] pip install failed.
    pause
    exit /b 1
  )
)
echo.
echo Building the portable app. This often takes several minutes.
echo.
call BUILD.bat
set "BUILD_RC=!errorlevel!"
echo.
if not "!BUILD_RC!"=="0" (
  echo [Eche] BUILD.bat exited with code !BUILD_RC!.
  pause
)
if exist "{relaunch_s}" (
  echo Starting {relaunch_s}
  start "" "{relaunch_s}"
) else if exist "{target_s}\\..\\eche\\Eche.exe" (
  start "" "{target_s}\\..\\eche\\Eche.exe"
) else if exist "{target_s}\\dist\\Eche\\Eche.exe" (
  start "" "{target_s}\\dist\\Eche\\Eche.exe"
) else if exist "{target_s}\\RUN_ECHE.bat" (
  start "" "{target_s}\\RUN_ECHE.bat"
) else (
  echo [Eche] Update finished, but no Eche.exe was found to launch.
  pause
)
if not "{stage_s}"=="" rmdir /s /q "{stage_s}" 2>nul
del "%~f0"
exit /b 0
"""
    Path(path).write_text(script, encoding="utf-8")
    return path


def launch_handoff(script_path: str) -> None:
    """Start the script in its own console so it survives this process exiting."""
    if not os.path.isfile(script_path):
        raise FileNotFoundError(script_path)
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "Eche update", "cmd.exe", "/c", script_path],
        creationflags=flags,
        close_fds=True,
    )
