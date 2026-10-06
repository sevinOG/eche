"""Stage a standalone ffmpeg.exe for source runs and frozen GitHub installs.

A GitHub install builds Eche on the target PC. That PC often has no ffmpeg
on PATH and no C:\\ffmpeg, so ?play raises "ffmpeg was not found". The dev
machine already has ffmpeg, which is why playback works there.

Discord only needs ffmpeg.exe. A shared build also needs avcodec DLLs beside
it, and those DLLs are not copied into _internal. This module accepts an exe
only when it still runs with PATH reduced to System32, then downloads a
static Windows build if nothing local qualifies.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from urllib.request import Request, urlopen

# Static win64 builds (not *-shared). Essentials/gpl is enough for Discord
# audio: https, aac, opus, mp3 → s16le PCM.
_ZIP_URLS = (
    # ~110 MB essentials build. The gpl zips below are ~200 MB fallbacks.
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
)

_USER_AGENT = "Eche-FFmpegFetch/1.3 (+https://github.com/sevinOG/eche)"
# A real static ffmpeg.exe is tens of MB. Reject stubs and text placeholders.
_MIN_BYTES = 1_000_000

_lock = threading.Lock()
_last_error = ""


def last_error() -> str:
    return _last_error


def staged_ffmpeg_path(root: str | os.PathLike) -> str:
    return os.path.join(os.path.abspath(str(root)), "ffmpeg.exe")


def _set_error(message: str) -> None:
    global _last_error
    _last_error = message


def _runs_standalone(path: str) -> bool:
    """True when this exe starts without DLLs from its own folder or PATH."""
    if not path or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) < _MIN_BYTES:
            return False
    except OSError:
        return False
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    env = os.environ.copy()
    env["PATH"] = os.path.join(system_root, "System32")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            timeout=30,
            env=env,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    blob = (result.stdout or b"") + (result.stderr or b"")
    return result.returncode == 0 and b"ffmpeg version" in blob.lower()


def _existing_candidates(root: str) -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    found: list[str] = [
        os.path.join(root, "ffmpeg.exe"),
        os.path.join(root, "_internal", "ffmpeg.exe"),
        os.path.join(root, "ffmpeg", "bin", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.join(local, "Programs", "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for name in ("ffmpeg", "ffmpeg.exe"):
        which = shutil.which(name)
        if which:
            found.append(which)
    return found


def _copy_if_standalone(src: str, dest: str) -> bool:
    if os.path.abspath(src) == os.path.abspath(dest):
        return _runs_standalone(dest)
    if not _runs_standalone(src):
        return False
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    partial = dest + ".partial"
    shutil.copy2(src, partial)
    os.replace(partial, dest)
    return _runs_standalone(dest)


def _pick_ffmpeg_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    best: tuple[tuple[int, int], zipfile.ZipInfo] | None = None
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        parts = [p for p in name.split("/") if p]
        if any(p == ".." for p in parts):
            continue
        if not parts or parts[-1].lower() != "ffmpeg.exe":
            continue
        if info.file_size < _MIN_BYTES:
            continue
        in_bin = len(parts) >= 2 and parts[-2].lower() == "bin"
        rank = (0 if in_bin else 1, len(name))
        if best is None or rank < best[0]:
            best = (rank, info)
    return None if best is None else best[1]


def _download_zip(url: str, dest_zip: str, log) -> None:
    log(f"Downloading FFmpeg: {url}")
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=180) as resp, open(dest_zip, "wb") as out:
        total = 0
        next_log = 20 * 1024 * 1024
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
            if total >= next_log:
                log(f"  ... {total // (1024 * 1024)} MB")
                next_log += 20 * 1024 * 1024
    log(f"Downloaded {total:,} bytes")
    if total < _MIN_BYTES:
        raise RuntimeError(f"download too small ({total} bytes)")


def _extract_ffmpeg_exe(zip_path: str, dest: str, log) -> bool:
    with zipfile.ZipFile(zip_path) as zf:
        info = _pick_ffmpeg_member(zf)
        if info is None:
            log("Zip did not contain a usable bin/ffmpeg.exe")
            return False
        log(f"Extracting {info.filename}")
        tmp_dir = tempfile.mkdtemp(prefix="eche_ff_")
        try:
            zf.extract(info, tmp_dir)
            extracted = os.path.join(tmp_dir, *info.filename.replace("\\", "/").split("/"))
            if not os.path.isfile(extracted):
                return False
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            partial = dest + ".partial"
            shutil.copy2(extracted, partial)
            os.replace(partial, dest)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    if _runs_standalone(dest):
        return True
    try:
        os.remove(dest)
    except OSError:
        pass
    return False


def ensure_ffmpeg(root: str | os.PathLike | None = None, log=None) -> str | None:
    """Return a standalone ffmpeg.exe inside root, downloading it if needed."""
    global _last_error
    log = log or (lambda _m: None)
    if root is None:
        from core.paths import package_root

        root = package_root()
    root = os.path.abspath(str(root))
    dest = staged_ffmpeg_path(root)

    with _lock:
        if _runs_standalone(dest):
            _last_error = ""
            return dest
        for cand in _existing_candidates(root):
            if _copy_if_standalone(cand, dest):
                log(f"Using standalone ffmpeg: {cand}")
                _last_error = ""
                return dest

        last = "no download attempted"
        for url in _ZIP_URLS:
            tmp = tempfile.mkdtemp(prefix="eche_ffzip_")
            zip_path = os.path.join(tmp, "ffmpeg.zip")
            try:
                _download_zip(url, zip_path, log)
                if _extract_ffmpeg_exe(zip_path, dest, log):
                    log(f"FFmpeg ready: {dest}")
                    _last_error = ""
                    return dest
                last = f"extracted exe from {url} did not run"
                log(f"FFmpeg download failed: {last}")
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                log(f"FFmpeg download failed ({url}): {last}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        _set_error(
            "Could not download a standalone ffmpeg.exe (" + last + "). "
            "Install ffmpeg from https://ffmpeg.org and restart Eche, "
            "or place ffmpeg.exe at C:\\ffmpeg\\bin\\ffmpeg.exe."
        )
        log(_last_error)
        return None
