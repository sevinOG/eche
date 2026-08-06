"""Download Eche *application* source from the public GitHub hub."""
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

# Public hub (repo name kept for continuity; product brand is Eche)
GITHUB_OWNER = "sevinOG"
GITHUB_REPO = "eche"
GITHUB_BRANCH = "main"

# Application source only — NEVER installer source
DEFAULT_SOURCE_SUBDIR = "eche_source"
# Try these in order (rename / legacy)
APP_SOURCE_CANDIDATES = (
    "eche_source",
    "echelon_source",
    "eche-source",
    "echelon-source",
)
# If we see only these, we grabbed the wrong tree
INSTALLER_MARKERS = (
    "eche_installer_source",
    "echelon_installer_source",
)

USER_AGENT = "Eche-Installer/1.4 (+https://github.com/sevinOG/eche)"


def repo_web_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"


def one_tap_installer_url() -> str:
    return (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/raw/main/"
        f"prebuilt/Eche-Installer.exe"
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


def _looks_like_app_source(dest: Path) -> bool:
    """True if dest is the Eche *app* source (bot + GUI), not the installer."""
    if (dest / "core").is_dir() and (
        (dest / "eche_app.py").is_file()
        or (dest / "echelon_app.py").is_file()
        or (dest / "BUILD.bat").is_file()
    ):
        # Installer trees have src/main.py + build.spec at root, no cogs/
        if (dest / "cogs").is_dir() or (dest / "gui").is_dir():
            return True
        if (dest / "core" / "bot.py").is_file() or (dest / "core" / "eche.py").is_file():
            return True
    return False


def _looks_like_installer_source(dest: Path) -> bool:
    if (dest / "src" / "main.py").is_file() and (dest / "build.spec").is_file():
        if not (dest / "cogs").is_dir() and not (dest / "core" / "bot.py").is_file():
            return True
    return False


def fetch_source_from_github(
    dest_dir: str | Path,
    *,
    subdir: str | None = None,
    branch: str = GITHUB_BRANCH,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """
    Download the monorepo zip and extract **only the app source** folder
    (eche_source / legacy echelon_source) into dest_dir.

    Never extracts eche_installer_source or the full monorepo root.
    """
    log = log or (lambda _m: None)
    progress = progress or (lambda _p, _m: None)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # Prefer explicit subdir, then known app-source names
    candidates: list[str] = []
    if subdir and subdir.strip():
        candidates.append(subdir.strip().strip("/"))
    for c in APP_SOURCE_CANDIDATES:
        if c not in candidates:
            candidates.append(c)

    progress(8, "Contacting GitHub…")
    raw = _download(archive_zip_url(branch), log=log)
    progress(35, "Extracting Eche *app* source (not installer)…")

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("Empty archive from GitHub")
        root_prefix = names[0].split("/")[0] + "/"

        # Discover which app-source folder exists in the zip
        chosen: str | None = None
        want_prefix: str | None = None
        for cand in candidates:
            prefix = f"{root_prefix}{cand}/"
            if any(n.startswith(prefix) for n in names):
                chosen = cand
                want_prefix = prefix
                break

        if not chosen or not want_prefix:
            available = sorted(
                {
                    n[len(root_prefix) :].split("/")[0]
                    for n in names
                    if n.startswith(root_prefix) and n != root_prefix
                }
            )
            raise RuntimeError(
                "Could not find Eche *application* source in the GitHub archive. "
                f"Looked for: {', '.join(candidates)}. "
                f"Found top-level entries: {', '.join(available)}. "
                "Expected a folder like eche_source/ with core/, gui/, cogs/."
            )

        # Refuse installer-only folders even if somehow requested
        if chosen in INSTALLER_MARKERS or "installer_source" in chosen:
            raise RuntimeError(
                f"Refusing to install installer source ({chosen}). "
                "GitHub install must use eche_source (the app)."
            )

        log(f"Using monorepo folder: {chosen}/ (application source)")
        matched = [n for n in names if n.startswith(want_prefix)]
        count = 0
        for n in matched:
            rel = n[len(want_prefix) :]
            if not rel or n.endswith("/"):
                continue
            # Never copy nested installer trees if present under app (shouldn't be)
            if rel.replace("\\", "/").split("/")[0] in INSTALLER_MARKERS:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
        log(f"Extracted {count} app source files from {chosen}/")

    progress(70, "Verifying application source…")
    # Flatten one accidental nesting
    if not _looks_like_app_source(dest):
        for child in list(dest.iterdir()):
            if child.is_dir() and _looks_like_app_source(child):
                for item in child.iterdir():
                    shutil.move(str(item), str(dest / item.name))
                try:
                    child.rmdir()
                except OSError:
                    pass
                break

    if _looks_like_installer_source(dest):
        raise RuntimeError(
            "Download resolved to *installer* source, not the Eche app. "
            "This is a bug — expected eche_source with core/bot + cogs/gui."
        )

    if not _looks_like_app_source(dest):
        raise RuntimeError(
            "Downloaded files do not look like Eche application source "
            "(need core/ + BUILD.bat or eche_app.py + gui/cogs). "
            f"Contents: {[p.name for p in dest.iterdir()][:20]}"
        )

    marker = dest / ".eche_github_install.json"
    marker.write_text(
        json.dumps(
            {
                "repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
                "branch": branch,
                "subdir": chosen,
                "product": "eche_app_source",
                "url": repo_web_url(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    progress(80, "App source ready")
    log(f"Eche application source ready at {dest}")
    return dest


def try_fetch_portable_app_from_release(
    dest_dir: str | Path,
    *,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path | None:
    """Optional: overlay portable Eche.exe from GitHub Releases if published."""
    log = log or (lambda _m: None)
    progress = progress or (lambda _p, _m: None)
    dest = Path(dest_dir)

    api = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    try:
        progress(82, "Checking GitHub Releases for portable app…")
        data = _download_json(api)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log(f"No portable release asset (OK for source-only): {e}")
        return None

    if not isinstance(data, dict):
        return None
    assets = data.get("assets") or []
    pick = None
    for a in assets:
        name = (a.get("name") or "").lower()
        if "portable" in name and name.endswith(".zip"):
            pick = a
            break
    if pick is None:
        for a in assets:
            name = (a.get("name") or "").lower()
            if (
                name.endswith(".zip")
                and "eche" in name
                and "installer" not in name
            ):
                pick = a
                break
    if pick is None:
        log("Release has no portable app zip — app source install only")
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
            (dest / name).write_bytes(raw)

        for p in dest.rglob("Eche.exe"):
            if "uninstall" not in p.name.lower():
                log(f"Portable app found: {p}")
                progress(96, "Portable app ready")
                return p
        for p in dest.rglob("Echelon.exe"):  # legacy name
            if "uninstall" not in p.name.lower():
                return p
    except Exception as e:
        log(f"Portable app download skipped: {e}")
    return None


def fetch_to_temp_source(
    *,
    subdir: str | None = None,
    branch: str = GITHUB_BRANCH,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    """Download *app* source into a new temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="eche_app_src_"))
    fetch_source_from_github(
        tmp,
        subdir=subdir or DEFAULT_SOURCE_SUBDIR,
        branch=branch,
        log=log,
        progress=progress,
    )
    return tmp
