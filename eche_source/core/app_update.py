"""Stage an Eche update, then hand the file replace to a process that outlives the app.

Windows will not replace Eche.exe, or the DLLs it has open, while this process
is still running. The handoff script waits for this PID to exit, copies source,
runs BUILD.bat, and starts Eche again.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

GITHUB_OWNER = "sevinOG"
GITHUB_REPO = "eche"
GITHUB_BRANCH = "main"
USER_AGENT = "Eche/2.1.0 (+https://github.com/sevinOG/eche)"

_APP_CANDIDATES = ("eche_source", "echelon_source", "eche-source", "echelon-source")
_SKIP_DIRS = {".venv", "__pycache__", "dist", "build", ".git", "cookies", "logs", "memories", "context"}
_SKIP_FILES = {"settings.json", "secrets.dpapi.json"}

# Written into eche_source by the installer and by every GitHub update, so the
# next update pulls the same branch instead of silently switching to main.
INSTALL_INFO_FILE = ".eche_github_install.json"
UPDATE_BRANCH_KEY = "update_branch"
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Only these trees are considered program code. Never inspect or delete
# anything outside them, and never touch user data even if it appears here.
_CODE_DIRS = ("core", "cogs", "gui")
_PROTECTED_DIR_NAMES = {
    "config", "data", "memories", "memory", "logs", "cookies", "context",
    ".venv", "venv", "__pycache__",
}
_PROTECTED_FILE_NAMES = {
    "settings.json", "secrets.dpapi.json", ".env", ".env.local",
}
_CODE_SUFFIXES = {".py", ".pyc", ".pyo", ".pyd", ".pyw"}

# Only these files, which Eche itself shipped and later removed, may be
# deleted from an existing install. User-dropped cogs (cogs/utils, cogs/games
# extras, and anything else not on this list) are never deleted.
REMOVED_PROGRAM_FILES = (
    "cogs/flash.py",
    "tests/test_video_approval_off.py",
)


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        pass
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return False
    return False


def _norm_key(path: Path | str) -> str:
    """Case-folded path with forward slashes, so Windows tests and mixed seps match."""
    return os.path.normcase(os.path.normpath(str(path))).replace("\\", "/")


def _path_is_inside(path: Path, root: Path) -> bool:
    """True if path.resolve() is the root or a file under it (normcase)."""
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    left = _norm_key(resolved)
    right = _norm_key(base)
    if left == right:
        return True
    return left.startswith(right + "/")


def _is_protected(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = {part.lower() for part in rel.parts[:-1]}
    if parts & {name.lower() for name in _PROTECTED_DIR_NAMES}:
        return True
    if path.name in _PROTECTED_FILE_NAMES or path.name.startswith(".env"):
        return True
    return False


def iter_shipped_code_files(root: str | Path) -> set[str]:
    """Relative posix paths of code files the release ships under core/cogs/gui."""
    root = Path(root)
    found: set[str] = set()
    for folder in _CODE_DIRS:
        base = root / folder
        if not base.is_dir() or _is_link(base):
            continue
        for path in base.rglob("*"):
            if _is_link(path):
                continue
            if any(_is_link(parent) for parent in path.parents if _path_is_inside(parent, base) or parent == base):
                continue
            if not path.is_file():
                continue
            if not _path_is_inside(path, base):
                continue
            if path.suffix.lower() not in _CODE_SUFFIXES:
                continue
            if _is_protected(path, root):
                continue
            found.add(path.relative_to(root).as_posix())
    return found



STALE_INSTALL_MANIFEST = ".eche_install_manifest.json"


def _delete_stale_install_manifest(target: str | Path) -> bool:
    """Remove the leftover installer manifest from an existing install."""
    path = Path(target) / STALE_INSTALL_MANIFEST
    try:
        if not path.is_file() or _is_link(path):
            return False
        path.unlink()
        return True
    except OSError:
        return False


def purge_removed_program_files(stage: str | Path, target: str | Path) -> list[str]:
    # Not robocopy /PURGE: that would also delete user files. Only the
    # explicit REMOVED_PROGRAM_FILES manifest is ever deleted.
    """Delete program files Eche itself shipped and later removed.

    Never deletes user-dropped cogs or any file not on the explicit
    removed-files list. Config, .env, data, memory, logs, and other user
    files are never touched. `stage` is the new release tree.
    """
    stage = Path(stage)
    target = Path(target)
    if not stage.is_dir() or not target.is_dir():
        raise ValueError("stage and target must be directories")
    try:
        if stage.resolve() == target.resolve():
            return []
    except Exception:
        if os.path.normcase(str(stage)) == os.path.normcase(str(target)):
            return []
    _delete_stale_install_manifest(target)
    keep = iter_shipped_code_files(stage)
    if not keep:
        raise RuntimeError("New release manifest was empty; refusing to purge.")
    keep_norm = {os.path.normcase(item) for item in keep}
    tombstone_norm = {os.path.normcase(item) for item in REMOVED_PROGRAM_FILES}
    removed: list[str] = []
    warned: list[str] = []
    # Walk only the explicit tombstone names, plus warn about other extras.
    present = iter_shipped_code_files(target)
    for rel in sorted(present):
        rel_norm = os.path.normcase(rel)
        path = target / rel
        if _is_protected(path, target) or _is_link(path):
            continue
        if not _path_is_inside(path, target):
            continue
        top = rel.split("/")[0]
        if top not in _CODE_DIRS:
            continue
        code_root = target / top
        if not _path_is_inside(path, code_root):
            continue
        if rel_norm in tombstone_norm:
            path.unlink()
            removed.append(rel)
            continue
        if rel_norm not in keep_norm:
            warned.append(rel)
    if warned:
        sample = ", ".join(warned[:8])
        extra = f" (and {len(warned) - 8} more)" if len(warned) > 8 else ""
        print(
            f"[update] Leaving {len(warned)} file(s) not in the new release "
            f"(not on the removed-files list): {sample}{extra}",
            flush=True,
        )
    try:
        from core.audit import safe_activity
        safe_activity("updater", action="purge", outcome="success", removed=len(removed))
    except Exception:
        pass
    return removed


def clean_branch(branch: str | None) -> str:
    """A safe git branch name, or "" if it is blank or not a plain branch name."""
    text = str(branch or "").strip()
    if not text or not _BRANCH_RE.match(text):
        return ""
    if ".." in text or "//" in text or text.endswith(("/", ".", ".lock")) or "@{" in text:
        return ""
    return text


def read_install_info(target: str | Path | None) -> dict:
    """Branch and commit recorded in eche_source at install/update time. {} if none."""
    if not target:
        return {}
    path = Path(target) / INSTALL_INFO_FILE
    try:
        if not path.is_file() or _is_link(path):
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    branch = clean_branch(data.get("branch"))
    out["branch"] = branch
    commit = str(data.get("commit") or "").strip().lower()
    out["commit"] = commit if _SHA_RE.match(commit) else ""
    return out


def write_install_info(dest: str | Path, branch: str, commit: str | None = None) -> Path:
    """Record which branch (and commit, when known) this source tree came from."""
    branch = clean_branch(branch) or GITHUB_BRANCH
    commit = str(commit or "").strip().lower()
    info = {
        "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
        "branch": branch,
        "commit": commit if _SHA_RE.match(commit) else "",
        "subdir": "eche_source",
        "product": "eche_app_source",
        "url": f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}",
    }
    path = Path(dest) / INSTALL_INFO_FILE
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_update_branch(setting: str | None = None, target: str | Path | None = None) -> str:
    """Branch the GitHub update pulls.

    1. The update_branch setting, when it is a valid branch name.
    2. The branch this install came from (INSTALL_INFO_FILE in the target).
    3. main.
    """
    chosen = clean_branch(setting)
    if chosen:
        return chosen
    installed = read_install_info(target).get("branch") or ""
    return installed or GITHUB_BRANCH


def archive_zip_url(branch: str = GITHUB_BRANCH) -> str:
    branch = clean_branch(branch)
    if not branch:
        raise ValueError("Update branch is not a valid git branch name.")
    return (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
        f"archive/refs/heads/{quote(branch, safe='/')}.zip"
    )


def _archive_commit(zf: zipfile.ZipFile) -> str:
    """GitHub stores the commit SHA as the archive's zip comment."""
    try:
        text = zf.comment.decode("ascii", "ignore").strip().lower()
    except Exception:
        return ""
    return text if _SHA_RE.match(text) else ""


def _looks_like_app_source(dest: Path) -> bool:
    if not (dest / "core").is_dir():
        return False
    if not ((dest / "eche_app.py").is_file() or (dest / "BUILD.bat").is_file()):
        return False
    return (dest / "gui").is_dir() or (dest / "cogs").is_dir() or (dest / "core" / "eche.py").is_file()


def extract_app_source_zip(raw: bytes, dest: str | Path, branch: str | None = None) -> Path:
    """Extract only the app source folder from a GitHub repo zip.

    When branch is given, records it (and the archive's commit) in
    INSTALL_INFO_FILE so the copy into eche_source carries it along.
    """
    dest = Path(dest)
    commit = ""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("GitHub archive was empty.")
        commit = _archive_commit(zf)
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
    if branch:
        write_install_info(dest, branch, commit)
    return dest


def stage_github_source(dest: str | Path, log=None, branch: str | None = None) -> Path:
    """Download a branch (main by default) and extract eche_source into dest.

    Does not touch the running app.
    """
    log = log or (lambda _m: None)
    branch = clean_branch(branch) or GITHUB_BRANCH
    url = archive_zip_url(branch)
    log(f"Downloading {url}")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=180) as resp:
        raw = resp.read()
    log(f"Downloaded {len(raw):,} bytes. Extracting application source…")
    found = extract_app_source_zip(raw, dest, branch=branch)
    info = read_install_info(found)
    if info.get("commit"):
        log(f"Branch {branch} at commit {info['commit'][:12]}")
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


_PURGE_HELPER = """\
# Eche update: remove program files the new release no longer ships.
# Never fails the update: if the new release has no purge step, fall back to
# the purge step from the copy that was installed before this update, or skip
# the cleanup with a warning. Data, settings, and secrets are never touched.
import importlib.util
import sys

STAGE = {stage!r}
TARGET = {target!r}
FALLBACK = {fallback!r}


def _from_stage():
    sys.path.insert(0, STAGE)
    try:
        from core.app_update import purge_removed_program_files as fn
        return fn
    except Exception as exc:
        print("[update] The new release has no purge step (" + type(exc).__name__ + ").", flush=True)
        return None
    finally:
        try:
            sys.path.remove(STAGE)
        except ValueError:
            pass


def _from_fallback():
    if not FALLBACK:
        return None
    try:
        spec = importlib.util.spec_from_file_location("eche_installed_app_update", FALLBACK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "purge_removed_program_files", None)
    except Exception as exc:
        print("[update] The installed copy's purge step could not load (" + type(exc).__name__ + ").", flush=True)
        return None


def main():
    fn = _from_stage()
    if fn is None:
        fn = _from_fallback()
        if fn is not None:
            print("[update] Using the purge step from the previously installed copy.", flush=True)
    if fn is None:
        print("[update] WARNING: skipping stale-file cleanup. The build continues; "
              "your Data folder, settings, and secrets were not touched.", flush=True)
        return 0
    try:
        removed = fn(STAGE, TARGET) or []
        print("[update] Removed " + str(len(removed)) + " stale program file(s).", flush=True)
    except Exception as exc:
        print("[update] WARNING: stale-file cleanup failed (" + type(exc).__name__ + ": "
              + str(exc)[:200] + "). The build continues.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def _save_installed_updater(target: str) -> str:
    """Copy the installed core/app_update.py aside before the copy overwrites it."""
    src = Path(target) / "core" / "app_update.py"
    try:
        if not src.is_file() or _is_link(src):
            return ""
        fd, dst = tempfile.mkstemp(prefix="eche-update-prev-", suffix=".py")
        os.close(fd)
        shutil.copyfile(src, dst)
        return _safe_path(dst)
    except Exception:
        return ""


def _write_purge_helper(stage: str, target: str, fallback: str) -> str:
    fd, path = tempfile.mkstemp(prefix="eche-update-purge-", suffix=".py")
    os.close(fd)
    Path(path).write_text(
        _PURGE_HELPER.format(stage=stage, target=target, fallback=fallback),
        encoding="utf-8",
    )
    return _safe_path(path)


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
    # Never let the cleanup step delete (or the copy land on) the Data folder.
    try:
        from core.data_folder import guard_paths
        from core.paths import is_within
        for guarded in guard_paths():
            if stage_s and (is_within(guarded, stage_s) or is_within(stage_s, guarded)):
                stage_s = ""
            if is_within(guarded, target_s):
                raise ValueError(
                    f"The Data folder {guarded} is inside the update target {target_s}. "
                    "Move it outside the app folder first."
                )
    except ImportError:
        pass
    helper_s = ""
    fallback_s = ""
    if stage_s:
        fallback_s = _save_installed_updater(target_s)
        helper_s = _write_purge_helper(stage_s, target_s, fallback_s)
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
echo Removing program files the new release no longer ships...
REM The helper runs purge_removed_program_files from the new release, falls back to
REM the previously installed copy, and otherwise skips with a warning. It never stops the build.
set "PURGE_DONE="
if exist "{target_s}\\.venv\\Scripts\\python.exe" (
  "{target_s}\\.venv\\Scripts\\python.exe" "{helper_s}"
  if not errorlevel 1 set "PURGE_DONE=1"
)
if not defined PURGE_DONE (
  py -3 "{helper_s}"
  if not errorlevel 1 set "PURGE_DONE=1"
)
if not defined PURGE_DONE (
  python "{helper_s}"
  if not errorlevel 1 set "PURGE_DONE=1"
)
if not defined PURGE_DONE (
  echo [Eche] WARNING: Python could not run the stale-file cleanup. Skipping it and building anyway.
  echo [Eche] Your Data folder, settings, and secrets were not touched.
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
  ".venv\\Scripts\\python.exe" -m pip install --require-hashes -r requirements.txt
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
if not "{helper_s}"=="" del "{helper_s}" 2>nul
if not "{fallback_s}"=="" del "{fallback_s}" 2>nul
del "%~f0"
exit /b 0
"""
    Path(path).write_text(script, encoding="utf-8")
    try:
        from core.audit import safe_activity
        safe_activity("updater", action="write-handoff", outcome="success")
    except Exception:
        pass
    return path


def launch_handoff(script_path: str) -> None:
    """Start the script in its own console so it survives this process exiting."""
    if not os.path.isfile(script_path):
        try:
            from core.audit import safe_activity
            safe_activity("updater", action="launch-handoff", outcome="error", error_type="FileNotFoundError")
        except Exception:
            pass
        raise FileNotFoundError(script_path)
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "Eche update", "cmd.exe", "/c", script_path],
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:
        try:
            from core.audit import safe_activity
            safe_activity(
                "updater",
                action="launch-handoff",
                outcome="error",
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        raise
    try:
        from core.audit import safe_activity
        safe_activity("updater", action="launch-handoff", outcome="success")
    except Exception:
        pass
