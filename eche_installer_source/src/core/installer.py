"""Core installer logic"""
import json
import shutil
import os
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Callable, List

from .shortcut import create_shortcut, get_desktop_path, get_start_menu_path
from .registry import register_uninstall, APP_VERSION

MANIFEST_NAME = ".eche_install_manifest.json"

@dataclass
class InstallOptions:
    source_exe: str
    install_dir: str
    create_desktop_shortcut: bool = True
    create_start_menu_shortcut: bool = True
    launch_after: bool = True
    # github | exe | dist_dir | source_dir | portable_dir | recover_source
    source_type: str = "github"
    github_subdir: str = "eche_source"

@dataclass
class InstallManifest:
    version: str
    install_dir: str
    source: str
    files: List[str]
    shortcuts: List[str]
    installed_at: float
    options: dict

class Installer:
    def __init__(self, log_callback: Callable[[str], None] = None, progress_callback: Callable[[int, str], None] = None):
        self.log_cb = log_callback or (lambda m: print(m))
        self.progress_cb = progress_callback or (lambda p, m: None)
        self._cancel = False

    def log(self, msg: str):
        self.log_cb(msg)

    def progress(self, percent: int, msg: str = ""):
        self.progress_cb(percent, msg)

    def cancel(self):
        self._cancel = True

    def install(self, opts: InstallOptions) -> bool:
        try:
            install_path = Path(opts.install_dir)
            source_path = Path(opts.source_exe)

            self.log(f"[Eche] Starting install v{APP_VERSION}")
            self.log(f"Source: {source_path} ({opts.source_type})")
            self.log(f"Target: {install_path}")
            self.progress(5, "Preparing...")

            if opts.source_type != "github" and not source_path.exists():
                self.log(f"ERROR: Source not found: {source_path}")
                return False

            existing_manifest = install_path / MANIFEST_NAME
            if existing_manifest.exists():
                self.log("Existing installation detected, will overwrite")

            install_path.mkdir(parents=True, exist_ok=True)
            self.progress(10, "Copying files...")
            self.log(f"Created directory: {install_path}")

            copied_files: List[str] = []
            created_shortcuts: List[str] = []

            if opts.source_type == "github":
                # 1) Download source to a temp folder
                # 2) Re-run as install-from-source into the user destination
                # 3) Optionally overlay a portable app from GitHub Releases
                import shutil as _shutil
                import tempfile as _tempfile

                tmp_root = None
                try:
                    from .github_fetch import (
                        fetch_to_temp_source,
                        repo_web_url,
                        try_fetch_portable_app_from_release,
                    )

                    # Always app source (eche_source), never installer source
                    sub = (opts.github_subdir or "eche_source").strip()
                    if "installer" in sub.lower():
                        self.log(
                            f"Ignoring invalid github_subdir={sub!r}; "
                            "forcing eche_source (application)."
                        )
                        sub = "eche_source"
                    self.log(
                        f"Fetching Eche *application* source from {repo_web_url()} "
                        f"(folder {sub}/) …"
                    )
                    tmp_root = fetch_to_temp_source(
                        subdir=sub,
                        log=self.log,
                        progress=self.progress,
                    )
                    # Hard check before copy
                    tmp_path = Path(tmp_root)
                    if (tmp_path / "src" / "main.py").is_file() and not (
                        tmp_path / "cogs"
                    ).is_dir():
                        raise RuntimeError(
                            "Staged tree looks like installer source, not app source. Aborting."
                        )
                    if not (tmp_path / "core").is_dir():
                        raise RuntimeError(
                            "Staged tree missing core/ — not Eche application source."
                        )
                    self.log(f"App source staged at {tmp_root}")
                    self.log("Continuing as install-from-source (application)…")
                    self.progress(45, "Installing application source…")
                    self._copy_tree(tmp_path, install_path, copied_files)

                    # Best-effort: if Releases publish a portable app, put it here too
                    try:
                        exe = try_fetch_portable_app_from_release(
                            install_path,
                            log=self.log,
                            progress=self.progress,
                        )
                        if exe:
                            self.log(f"Portable app included: {exe}")
                            for p in install_path.rglob("*"):
                                if p.is_file() and str(p) not in copied_files:
                                    copied_files.append(str(p))
                    except Exception as pe:
                        self.log(f"Portable overlay skipped: {pe}")

                    self.log(
                        "GitHub → source install complete. "
                        "If Eche.exe is present, launch it. "
                        "Otherwise use SETUP_AND_BUILD.bat (needs Python once)."
                    )
                except Exception as e:
                    self.log(f"ERROR: GitHub download failed: {e}")
                    return False
                finally:
                    if tmp_root:
                        try:
                            _shutil.rmtree(tmp_root, ignore_errors=True)
                        except Exception:
                            pass
            elif opts.source_type == "recover_source":
                # Two-way: rebuild a dev source tree from a portable app / onedir
                ok = self._recover_source_from_app(source_path, install_path, copied_files)
                if not ok:
                    return False
            elif opts.source_type == "exe":
                dest_exe = install_path / source_path.name
                if source_path.is_file() and (
                    source_path.parent.name in ("dist", "Eche")
                    or (source_path.parent / "_internal").exists()
                ):
                    self._copy_tree(source_path.parent, install_path, copied_files)
                    dest_exe = install_path / source_path.name
                elif source_path.is_file():
                    shutil.copy2(source_path, dest_exe)
                    copied_files.append(str(dest_exe))
                    self.log(f"Copied {source_path.name}")
                else:
                    self.log(f"ERROR: Not an executable: {source_path}")
                    return False
                self._write_portable_marker(install_path)
            elif opts.source_type in ("dist_dir", "source_dir", "portable_dir"):
                self._copy_tree(source_path, install_path, copied_files)
                if opts.source_type != "source_dir":
                    self._write_portable_marker(install_path)
            else:
                self.log(f"Unknown source type: {opts.source_type}")
                return False

            if self._cancel:
                self.log("Install cancelled")
                return False

            self.progress(60, "Creating shortcuts...")
            main_exe = self._find_main_exe(install_path)
            # Source trees: only create shortcuts if we also got an EXE (release overlay)
            skip_app_shortcuts = opts.source_type in (
                "source_dir",
                "recover_source",
            ) or (
                opts.source_type == "github"
                and not (main_exe and Path(main_exe).exists())
            )

            if opts.source_type == "github":
                self._write_beginner_next_steps(install_path, main_exe)
                copied_files.append(str(install_path / "START_HERE.txt"))
                self.log(f"[SUCCESS] GitHub install finished → {install_path}")

            if opts.source_type == "recover_source":
                self.progress(90, "Writing recovery notes...")
                readme = install_path / "README_RECOVERED.md"
                readme.write_text(
                    "# Recovered Eche source\n\n"
                    "This tree was reconstructed from a portable app / frozen package.\n"
                    "Install Python 3.11+, then:\n\n"
                    "```bat\n"
                    "python -m venv .venv\n"
                    ".venv\\Scripts\\pip install -r requirements.txt\n"
                    "BUILD.bat\n"
                    "```\n",
                    encoding="utf-8",
                )
                copied_files.append(str(readme))
                self.progress(100, "Source recovery complete")
                self.log(f"[SUCCESS] Source recovered to {install_path}")
                return True

            brand_icon = self._find_brand_icon(install_path, main_exe)

            if (
                not skip_app_shortcuts
                and opts.create_desktop_shortcut
                and main_exe
                and Path(main_exe).exists()
            ):
                desktop = get_desktop_path()
                shortcut_path = desktop / "Eche.lnk"
                if create_shortcut(
                    str(main_exe),
                    str(shortcut_path),
                    str(install_path),
                    "Eche - Elite Operations",
                    brand_icon,
                ):
                    created_shortcuts.append(str(shortcut_path))
                    self.log(f"Created desktop shortcut: {shortcut_path}")
                else:
                    self.log("Failed to create desktop shortcut")

            if (
                not skip_app_shortcuts
                and opts.create_start_menu_shortcut
                and main_exe
                and Path(main_exe).exists()
            ):
                start_menu = get_start_menu_path()
                eche_menu = start_menu / "Eche"
                eche_menu.mkdir(parents=True, exist_ok=True)
                shortcut_path = eche_menu / "Eche.lnk"
                if create_shortcut(
                    str(main_exe),
                    str(shortcut_path),
                    str(install_path),
                    "Eche",
                    brand_icon,
                ):
                    created_shortcuts.append(str(shortcut_path))
                    self.log(f"Created start menu shortcut: {shortcut_path}")

            self.progress(75, "Writing manifest...")
            uninstaller_path = install_path / "Uninstall.exe"
            try:
                import sys
                if getattr(sys, 'frozen', False):
                    self_exe = Path(sys.executable)
                    shutil.copy2(self_exe, uninstaller_path)
                    self.log(f"Created uninstaller: {uninstaller_path}")
                    copied_files.append(str(uninstaller_path))
                    if opts.create_start_menu_shortcut:
                        sm_uninstall = get_start_menu_path() / "Eche" / "Uninstall Eche.lnk"
                        create_shortcut(str(uninstaller_path), str(sm_uninstall), str(install_path), "Uninstall Eche", str(uninstaller_path))
                        if str(sm_uninstall) not in created_shortcuts:
                            created_shortcuts.append(str(sm_uninstall))
                else:
                    uninstall_py = install_path / "uninstall.py"
                    uninstall_py.write_text(self._generate_uninstall_script(), encoding="utf-8")
                    uninstall_bat = install_path / "Uninstall.bat"
                    uninstall_bat.write_text(f'@echo off\n"{sys.executable}" "{uninstall_py}" --uninstall\npause\n', encoding="utf-8")
                    copied_files.extend([str(uninstall_py), str(uninstall_bat)])
                    uninstaller_path = uninstall_py
                    self.log("Created uninstall script (dev mode)")
            except Exception as exc:
                self.log(f"Uninstaller creation warning: {exc}")

            manifest = InstallManifest(
                version=APP_VERSION,
                install_dir=str(install_path),
                source=str(source_path),
                files=copied_files,
                shortcuts=created_shortcuts,
                installed_at=time.time(),
                options=asdict(opts)
            )
            manifest_path = install_path / MANIFEST_NAME
            manifest_path.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")
            copied_files.append(str(manifest_path))

            self.progress(85, "Registering uninstall...")
            reg_ok = register_uninstall(str(install_path), str(uninstaller_path), str(main_exe) if main_exe else str(uninstaller_path), APP_VERSION)
            if reg_ok:
                self.log("Registered in Add/Remove Programs")
            else:
                self.log("Registry registration skipped (non-Windows or permission)")

            self.progress(100, "Install complete")
            self.log(f"[SUCCESS] Eche installed to {install_path}")

            # Eche.exe is only expected for portable/EXE installs (or recovery
            # that already had a freeze). GitHub / source installs ship the
            # recipe — use SETUP_AND_BUILD.bat; no EXE warning.
            needs_exe = opts.source_type in ("exe", "dist_dir", "portable_dir")
            if main_exe and Path(main_exe).exists():
                self.log(f"Launch: {main_exe}")
                try:
                    launch_marker = install_path / ".eche_launch_path"
                    launch_marker.write_text(str(main_exe), encoding="utf-8")
                except Exception:
                    pass
            elif needs_exe:
                self.log(
                    "WARNING: Could not locate Eche.exe after portable install — "
                    "use Open Folder to check the install path."
                )
            elif opts.source_type in ("github", "source_dir"):
                self.log(
                    "Application source is ready (no Eche.exe yet — normal). "
                    "Open the folder and run SETUP_AND_BUILD.bat after installing Python, "
                    "or use START_HERE.txt."
                )

            return True

        except Exception as exc:
            self.log(f"[FATAL] Install failed: {exc}")
            import traceback
            self.log(traceback.format_exc())
            return False

    def _copy_tree(self, src: Path, dst: Path, copied_files: List[str]):
        src = Path(src)
        total = sum(1 for _ in src.rglob("*") if _.is_file())
        count = 0
        for item in src.rglob("*"):
            if self._cancel:
                break
            if item.is_file():
                if ".venv" in str(item) or "__pycache__" in str(item):
                    continue
                rel = item.relative_to(src)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, target)
                    copied_files.append(str(target))
                    count += 1
                    if count % 50 == 0:
                        pct = 10 + int(50 * count / max(total, 1))
                        self.progress(min(pct, 60), f"Copying {rel}...")
                except Exception as exc:
                    self.log(f"Copy warning {rel}: {exc}")
        self.log(f"Copied {len(copied_files)} files")

    def _write_beginner_next_steps(
        self, install_path: Path, main_exe: Path | None
    ) -> None:
        """Plain-English file for people who have never used GitHub or AI tools."""
        has_exe = bool(main_exe and Path(main_exe).exists())
        lines = [
            "WELCOME TO ECHE",
            "==================",
            "",
            "You do NOT need to understand GitHub to use this folder.",
            "This is a learning project — a Discord bot with a simple desktop window.",
            "",
        ]
        if has_exe:
            lines += [
                "GOOD NEWS: Eche.exe is already here.",
                f"  → Double-click: {main_exe}",
                "",
                "First run tips:",
                "  1. Open Settings and paste your Discord bot token (free from Discord).",
                "  2. Optional: add a free Groq API key for AI chat (console.groq.com).",
                "  3. Press Run Bot.",
                "",
            ]
        else:
            lines += [
                "This folder is the Eche SOURCE (the recipe).",
                "To turn it into a double-click app once:",
                "",
                "  A) Install Python 3.11+ from https://www.python.org/downloads/",
                "     (check the box: Add python.exe to PATH)",
                "",
                "  B) Double-click SETUP_AND_BUILD.bat in this folder.",
                "     Wait until it finishes — then open dist\\Eche\\Eche.exe",
                "     (or the published eche\\ folder if present).",
                "",
            ]
        lines += [
            "Learning links:",
            "  • Project home: https://github.com/sevinOG/eche",
            "  • Read START_HERE.md on GitHub if you got lost",
            "",
            "You never need to share tokens, passwords, or server IDs publicly.",
            "",
        ]
        (install_path / "START_HERE.txt").write_text("\n".join(lines), encoding="utf-8")

        # One-tap build helper for source installs (safe if BUILD.bat missing)
        setup = install_path / "SETUP_AND_BUILD.bat"
        if (install_path / "BUILD.bat").is_file() and not setup.is_file():
            setup.write_text(
                "@echo off\n"
                "setlocal\n"
                "title Eche - one-tap setup\n"
                "cd /d \"%~dp0\"\n"
                "echo.\n"
                "echo  ECHE — first-time setup (beginner)\n"
                "echo  =====================================\n"
                "echo.\n"
                "where python >nul 2>&1\n"
                "if errorlevel 1 (\n"
                "  echo [NEED] Python was not found.\n"
                "  echo   1. Open https://www.python.org/downloads/\n"
                "  echo   2. Install Python 3.11 or newer\n"
                "  echo   3. ENABLE: Add python.exe to PATH\n"
                "  echo   4. Close this window and run SETUP_AND_BUILD.bat again\n"
                "  start https://www.python.org/downloads/\n"
                "  pause\n"
                "  exit /b 1\n"
                ")\n"
                "if not exist .venv\\Scripts\\python.exe (\n"
                "  echo [1/3] Creating virtual environment...\n"
                "  python -m venv .venv\n"
                "  if errorlevel 1 (\n"
                "    echo Failed to create .venv\n"
                "    pause\n"
                "    exit /b 1\n"
                "  )\n"
                ")\n"
                "echo [2/3] Installing libraries (several minutes)...\n"
                "\".venv\\Scripts\\python.exe\" -m pip install -U pip\n"
                "\".venv\\Scripts\\python.exe\" -m pip install -r requirements.txt\n"
                "if errorlevel 1 (\n"
                "  echo pip install failed\n"
                "  pause\n"
                "  exit /b 1\n"
                ")\n"
                "echo [3/3] Building portable app...\n"
                "set ECHE_NO_PAUSE=1\n"
                "call BUILD.bat\n"
                "echo.\n"
                "echo Done. Look for dist\\Eche\\Eche.exe or ..\\eche\\Eche.exe\n"
                "pause\n",
                encoding="utf-8",
            )

    def _write_portable_marker(self, install_path: Path) -> None:
        marker = install_path / "install.json"
        try:
            marker.write_text(
                '{\n  "kind": "portable_app",\n  "version": "'
                + APP_VERSION
                + '"\n}\n',
                encoding="utf-8",
            )
        except Exception as e:
            self.log(f"Could not write install.json: {e}")

    def _recover_source_from_app(
        self, source_path: Path, dest: Path, copied_files: List[str]
    ) -> bool:
        """
        Extract open-source modules from a portable / onedir package into dest.
        Looks for core/, cogs/, gui/ next to the exe or under _internal/.
        """
        source_path = Path(source_path)
        roots: list[Path] = []
        if source_path.is_file():
            roots.append(source_path.parent)
            roots.append(source_path.parent / "_internal")
        else:
            roots.append(source_path)
            roots.append(source_path / "_internal")
            if (source_path / "Eche.exe").is_file():
                roots.append(source_path)
                roots.append(source_path / "_internal")

        # also accept an existing source tree as the "from" side (clone/repair)
        for r in list(roots):
            if (r / "core").is_dir() and (r / "gui").is_dir():
                pass

        donor: Path | None = None
        for r in roots:
            if (r / "core").is_dir() and (r / "gui").is_dir():
                donor = r
                break
            # nested datas layout
            for sub in ("", "eche_source"):
                cand = r / sub if sub else r
                if (cand / "core").is_dir() and (cand / "gui").is_dir():
                    donor = cand
                    break
            if donor:
                break

        if donor is None:
            self.log(
                "ERROR: Could not find core/ + gui/ inside the portable package. "
                "Rebuild with package_portable.bat so source modules are bundled."
            )
            return False

        dest.mkdir(parents=True, exist_ok=True)
        self.log(f"Recovering source modules from: {donor}")

        for name in ("core", "cogs", "gui", "assets"):
            src = donor / name
            if not src.exists():
                # try _internal
                alt = donor / "_internal" / name if donor.name != "_internal" else None
                if alt and alt.exists():
                    src = alt
                else:
                    self.log(f"  skip missing: {name}")
                    continue
            target = dest / name
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(src, target)
            self.log(f"  recovered {name}/")
            for p in target.rglob("*"):
                if p.is_file():
                    copied_files.append(str(p))

        # helper files if present on donor or sibling source
        for fname in (
            "eche_app.py",
            "rthook_eche.py",
            "build_exe.spec",
            "requirements.txt",
            "VERSION",
            "package_portable.bat",
            "install.bat",
            "README.md",
        ):
            for base in (donor, donor.parent, source_path if source_path.is_dir() else source_path.parent):
                srcf = Path(base) / fname
                if srcf.is_file():
                    shutil.copy2(srcf, dest / fname)
                    copied_files.append(str(dest / fname))
                    self.log(f"  recovered {fname}")
                    break

        # minimal requirements if missing
        req = dest / "requirements.txt"
        if not req.is_file():
            req.write_text(
                "discord.py>=2.3\nPyQt6>=6.5\npython-dotenv>=1.0\n"
                "requests>=2.31\naiohttp>=3.9\npsutil>=5.9\n"
                "dateparser>=1.2\nyt-dlp>=2024.1.0\npyinstaller>=6.0\n",
                encoding="utf-8",
            )
            copied_files.append(str(req))

        if not (dest / "eche_app.py").is_file():
            (dest / "eche_app.py").write_text(
                "from __future__ import annotations\n"
                "import os, sys\n\n"
                "def main():\n"
                "    root = os.path.dirname(os.path.abspath(__file__))\n"
                "    if root not in sys.path:\n"
                "        sys.path.insert(0, root)\n"
                "    os.chdir(root)\n"
                "    if '--bot' in sys.argv:\n"
                "        from core.eche import main as bot_main\n"
                "        bot_main()\n"
                "    else:\n"
                "        from gui.main import launch_gui\n"
                "        launch_gui()\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n",
                encoding="utf-8",
            )
            copied_files.append(str(dest / "eche_app.py"))

        self.log(f"Recovered source tree at {dest}")
        return True

    def _find_brand_icon(self, install_path: Path, main_exe: Path | None) -> str | None:
        """Prefer assets/icon.ico (Windows shortcuts), fall back to icon.png / exe."""
        bases: list[Path] = [Path(install_path)]
        if main_exe:
            bases.append(Path(main_exe).parent)
            bases.append(Path(main_exe).parent / "_internal")
        for base in bases:
            for rel in (
                ("assets", "icon.ico"),
                ("assets", "icon.png"),
                ("icon.ico",),
                ("icon.png",),
            ):
                p = base.joinpath(*rel)
                if p.is_file():
                    return str(p)
            # also walk one level for onedir package roots
            for p in base.rglob("icon.ico"):
                if "assets" in p.parts or p.parent == base:
                    return str(p)
        if main_exe and Path(main_exe).is_file():
            return str(main_exe)
        return None

    def _find_main_exe(self, install_path: Path):
        """Locate the real app EXE (never Uninstall.exe)."""
        install_path = Path(install_path)
        prefer = [
            install_path / "Eche.exe",
            install_path / "Eche" / "Eche.exe",
            install_path / "dist" / "Eche" / "Eche.exe",
            install_path / "dist" / "Eche.exe",
        ]
        for p in prefer:
            if p.is_file() and "uninstall" not in p.name.lower():
                return p
        # Shallow first, then deep
        for p in sorted(install_path.glob("*.exe")):
            if p.is_file() and "uninstall" not in p.name.lower():
                return p
        for p in sorted(install_path.rglob("Eche.exe")):
            if p.is_file():
                return p
        for p in sorted(install_path.rglob("*.exe")):
            if p.is_file() and "uninstall" not in p.name.lower():
                return p
        return None

    def _generate_uninstall_script(self):
        return """
import json
import shutil
from pathlib import Path

def main():
    install_dir = Path(__file__).parent
    manifest = install_dir / ".eche_install_manifest.json"
    print(f"Uninstalling Eche from {install_dir}")
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for sc in data.get("shortcuts", []):
                try:
                    Path(sc).unlink(missing_ok=True)
                    print(f"Removed shortcut {sc}")
                except Exception as exc:
                    print(f"Shortcut remove failed {sc}: {exc}")
        except Exception as exc:
            print(f"Manifest read failed: {exc}")
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Eche")
    except Exception:
        pass
    print("Done. You may manually delete the folder if files remain.")

if __name__ == "__main__":
    main()
"""
