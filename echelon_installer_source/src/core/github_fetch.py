"""Download Echelon packages from the public GitHub hub."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

# Public hub — no auth required for public archive download
GITHUB_OWNER = "sevinOG"
GITHUB_REPO = "echelon_ecosystem"
GITHUB_BRANCH = "main"
# Default product to install from the monorepo
DEFAULT_SOURCE_SUBDIR = "echelon_source"

USER_AGENT = "Echelon-Installer/1.2 (+https://github.com/sevinOG/echelon_ecosystem)"


def repo_web_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"


def archive_zip_url(branch: str = GITHUB_BRANCH) -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/archive/refs/heads/{branch}.zip"


def _download(url: str, log: Callable[[str], None] | None = None) -> bytes:
    log = log or (lambda _m: None)
    log(f"Downloading {url}")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
    log(f"Downloaded {len(data):,} bytes")
    return data


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
    Returns the path to the extracted source root (dest_dir).
    """
    log = log or (lambda _m: None)
    progress = progress or (lambda _p, _m: None)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    progress(10, "Contacting GitHub…")
    raw = _download(archive_zip_url(branch), log=log)
    progress(40, "Extracting archive…")

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        # Root folder is usually echelon_ecosystem-main/
        names = zf.namelist()
        if not names:
            raise RuntimeError("Empty archive from GitHub")
        root_prefix = names[0].split("/")[0] + "/"
        want_prefix = f"{root_prefix}{subdir.strip('/')}/"
        matched = [n for n in names if n.startswith(want_prefix)]
        if not matched:
            # Fallback: entire repo
            log(f"Subdir {subdir!r} not found — extracting full archive root")
            want_prefix = root_prefix
            matched = [n for n in names if n.startswith(want_prefix)]

        # Clear dest carefully if empty-ish
        for n in matched:
            rel = n[len(want_prefix) :]
            if not rel or n.endswith("/"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)

    progress(85, "Verifying source tree…")
    if not (dest / "core").is_dir() and not (dest / "echelon_app.py").is_file():
        # maybe nested one level
        for child in dest.iterdir():
            if child.is_dir() and (child / "core").is_dir():
                # flatten
                for item in child.iterdir():
                    shutil.move(str(item), str(dest / item.name))
                try:
                    child.rmdir()
                except OSError:
                    pass
                break

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
    progress(100, "GitHub source ready")
    log(f"Installed source to {dest}")
    return dest
