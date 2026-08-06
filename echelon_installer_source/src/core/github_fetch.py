"""Download Echelon packages from the public GitHub hub (no Git account required)."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Public hub
GITHUB_OWNER = "sevinOG"
GITHUB_REPO = "echelon_ecosystem"
GITHUB_BRANCH = "main"
DEFAULT_SOURCE_SUBDIR = "echelon_source"

USER_AGENT = "Echelon-Installer/1.3 (+https://github.com/sevinOG/echelon_ecosystem)"


def repo_web_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"


def one_tap_installer_url() -> str:
    """Direct file link (no Git skills needed) — raw path on main."""
    return (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/raw/main/"
        f"prebuilt/Echelon-Installer.exe"
    )


def releases_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


def archive_zip_url(branch: str = GITHUB_BRANCH) -> str:
    return (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
        f"archive/refs/heads/{branch}.zip"
    )


def _download(url: str, log: Callable[[str], None] | None = None) -> bytes:
    log = log or (lambda _m: None)
    log(f"Downloading {url}")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=180) as resp:
        data = resp.read()
    log(f"Downloaded {len(data):,} bytes")
    return data


def _download_json(url: str) -> dict | list:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_source_from_github(
    dest_dir: str | Path,
    *,
    subdir: str = DEFAULT_SOURCE_SUBDIR,
    branch: str = GITHUB_BRANCH,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """
    Download the monorepo zip and extract `subdir` into dest_dir.
    Returns dest_dir (source root ready for install-from-source).
    """
    log = log or (lambda _m: None)
    progress = progress or (lambda _p, _m: None)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    progress(8, "Contacting GitHub…")
    raw = _download(archive_zip_url(branch), log=log)
    progress(35, "Extracting Echelon source…")

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("Empty archive from GitHub")
        root_prefix = names[0].split("/")[0] + "/"
        want_prefix = f"{root_prefix}{subdir.strip('/')}/"
        matched = [n for n in names if n.startswith(want_prefix)]
        if not matched:
            log(f"Subdir {subdir!r} not found — extracting archive root")
            want_prefix = root_prefix
            matched = [n for n in names if n.startswith(want_prefix)]

        for n in matched:
            rel = n[len(want_prefix) :]
            if not rel or n.endswith("/"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)

    progress(70, "Verifying source tree…")
    if not (dest / "core").is_dir() and not (dest / "echelon_app.py").is_file():
        for child in list(dest.iterdir()):
            if child.is_dir() and (child / "core").is_dir():
                for item in child.iterdir():
                    shutil.move(str(item), str(dest / item.name))
                try:
                    child.rmdir()
                except OSError:
                    pass
                break

    if not (dest / "core").is_dir():
        raise RuntimeError(
            "Downloaded archive did not contain echelon_source/core. "
            "Check the GitHub repository layout."
        )

    marker = dest / ".echelon_github_install.json"
    marker.write_text(
        json.dumps(
            {
                "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
                "branch": branch,
                "subdir": subdir,
                "url": repo_web_url(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    progress(80, "Source ready")
    log(f"GitHub source ready at {dest}")
    return dest


def try_fetch_portable_app_from_release(
    dest_dir: str | Path,
    *,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path | None:
    """
    If a GitHub Release publishes a portable app zip/exe, download it into dest.
    Returns path to Echelon.exe if found, else None (beginners can still use source).
    """
    log = log or (lambda _m: None)
    progress = progress or (lambda _p, _m: None)
    dest = Path(dest_dir)

    api = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    try:
        progress(82, "Checking GitHub Releases for portable app…")
        data = _download_json(api)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
        log(f"No portable release asset (OK for source-only): {e}")
        return None

    if not isinstance(data, dict):
        return None
    assets = data.get("assets") or []
    # Prefer zip named portable / Echelon, then onedir-ish exe
    pick = None
    for a in assets:
        name = (a.get("name") or "").lower()
        if "portable" in name and name.endswith(".zip"):
            pick = a
            break
    if pick is None:
        for a in assets:
            name = (a.get("name") or "").lower()
            if name.endswith(".zip") and "echelon" in name and "installer" not in name:
                pick = a
                break
    if pick is None:
        log("Release has no portable app zip — source install only")
        return None

    url = pick.get("browser_download_url")
    name = pick.get("name") or "portable.zip"
    if not url:
        return None

    try:
        progress(88, f"Downloading {name}…")
        raw = _download(url, log=log)
        if name.lower().endswith(".zip"):
            progress(92, "Extracting portable app…")
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                zf.extractall(dest)
        else:
            out = dest / name
            out.write_bytes(raw)

        for p in dest.rglob("Echelon.exe"):
            if "uninstall" not in p.name.lower():
                log(f"Portable app found: {p}")
                progress(96, "Portable app ready")
                return p
    except Exception as e:
        log(f"Portable app download skipped: {e}")
    return None


def fetch_to_temp_source(
    *,
    subdir: str = DEFAULT_SOURCE_SUBDIR,
    branch: str = GITHUB_BRANCH,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """Download source into a new temp directory (for install-from-source handoff)."""
    tmp = Path(tempfile.mkdtemp(prefix="echelon_gh_src_"))
    fetch_source_from_github(
        tmp, subdir=subdir, branch=branch, log=log, progress=progress
    )
    return tmp
