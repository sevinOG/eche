import json
import shutil
import os
import time
import subprocess
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

            if (install_path / MANIFEST_NAME).exists():
                self.log("Existing installation detected, will overwrite")

            install_path.mkdir(parents=True, exist_ok=True)
            self.progress(10, "Copying files...")
            copied_files: List[str] = []
            created_shortcuts: List[str] = []

            if opts.source_type == "github":
                import shutil as _shutil
                tmp_root = None
                try:
                    from .github_fetch import fetch_to_temp_source, repo_web_url, try_fetch_portable_app_from_release
                    sub = (opts.github_subdir or "eche_source").strip()
                    if "installer" in sub.lower():
                        sub = "eche_source"
                    self.log(f"Fetching Eche application source from {repo_web_url()} (folder {sub}/) ...")
                    tmp_root = fetch_to_temp_source(subdir=sub, log=self.log, progress=self.progress)
                    tmp_path = Path(tmp_root)
                    if (tmp_path / "src" / "main.py").is_file() and not (tmp_path / "cogs").is_dir():
                        raise RuntimeError("Staged tree looks like installer source, not app source.")
                    if not (tmp_path / "core").is_dir():
                        raise RuntimeError("Staged tree missing core/")
                    self.progress(45, "Installing application source...")
                    self._copy_tree(tmp_path, install_path, copied_files)
                    try:
                        exe = try_fetch_portable_app_from_release(install_path, log=self.log, progress=self.progress)
                        if exe:
                            self.log(f"Portable app included: {exe}")
                            for p in install_path.rglob("*"):
                                if p.is_file() and str(p) not in copied_files:
                                    copied_files.append(str(p))
                    except Exception as pe:
                        self.log(f"Portable overlay skipped: {pe}")

                    exe_candidate = self._find_main_exe(install_path)
                    if not exe_candidate:
                        sibling = install_path.parent / "eche" / "Eche.exe"
                        if sibling.exists() and self._is_real_app_exe(sibling):
                            exe_candidate = sibling
                    if not exe_candidate:
                        self.log("No prebuilt Eche.exe - auto-building now (2-3 min, one time)...")
                        self.progress(50, "Building Eche.exe automatically...")
                        built = self._auto_build_app(install_path)
                        if built and built.exists():
                            self.log(f"Auto-build succeeded: {built}")
                            exe_candidate = built
                            for p in install_path.rglob("*"):
                                if p.is_file() and str(p) not in copied_files:
                                    copied_files.append(str(p))
                            # re-check main exe after build
                            sibling = install_path.parent / "eche" / "Eche.exe"
                            if sibling.exists():
                                exe_candidate = sibling
                        else:
                            self.log("Auto-build did not produce Eche.exe - will fall back to RUN_ECHE.bat")
                    self.log(f"[SUCCESS] GitHub install finished -> {install_path}")
                    if exe_candidate and exe_candidate.exists():
                        self.log(f"Eche ready to run: {exe_candidate}")
                except Exception as e:
                    self.log(f"ERROR: GitHub download/build failed: {e}")
                    import traceback; self.log(traceback.format_exc())
                    return False
                finally:
                    if tmp_root:
                        try: _shutil.rmtree(tmp_root, ignore_errors=True)
                        except: pass

            elif opts.source_type == "recover_source":
                ok = self._recover_source_from_app(source_path, install_path, copied_files)
                if not ok:
                    return False
            elif opts.source_type == "exe":
                dest_exe = install_path / source_path.name
                if source_path.is_file() and (source_path.parent.name in ("dist", "Eche") or (source_path.parent / "_internal").exists()):
                    self._copy_tree(source_path.parent, install_path, copied_files)
                elif source_path.is_file():
                    shutil.copy2(source_path, dest_exe)
                    copied_files.append(str(dest_exe))
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
            if not main_exe:
                sibling = install_path.parent / "eche" / "Eche.exe"
                if sibling.exists() and self._is_real_app_exe(sibling):
                    main_exe = sibling

            launch_target: Path | None = main_exe
            if opts.source_type in ("github", "source_dir") and not main_exe:
                run_bat = self._write_run_eche_bat(install_path)
                if run_bat and run_bat.is_file():
                    launch_target = run_bat
                    copied_files.append(str(run_bat))
                    self.log(f"Wrote fallback launcher: {run_bat.name}")

            if opts.source_type == "github":
                self._write_beginner_next_steps(install_path, main_exe)
                copied_files.append(str(install_path / "START_HERE.txt"))

            if opts.source_type == "recover_source":
                self.progress(90, "Writing recovery notes...")
                readme = install_path / "README_RECOVERED.md"
                readme.write_text("# Recovered Eche source\n\nInstall Python 3.11+, then:\npython -m venv .venv\n.venv\\Scripts\\pip install -r requirements.txt\nBUILD.bat\n", encoding="utf-8")
                copied_files.append(str(readme))
                self.progress(100, "Source recovery complete")
                self.log(f"[SUCCESS] Source recovered to {install_path}")
                return True

            brand_icon = self._find_brand_icon(install_path, main_exe)
            can_shortcut = bool(launch_target and Path(launch_target).exists() and opts.source_type != "recover_source")
            if can_shortcut and opts.create_desktop_shortcut:
                desktop = get_desktop_path()
                shortcut_path = desktop / "Eche.lnk"
                if create_shortcut(str(launch_target), str(shortcut_path), str(install_path), "Eche - Discord bot control panel", brand_icon):
                    created_shortcuts.append(str(shortcut_path))
            if can_shortcut and opts.create_start_menu_shortcut:
                start_menu = get_start_menu_path()
                eche_menu = start_menu / "Eche"
                eche_menu.mkdir(parents=True, exist_ok=True)
                shortcut_path = eche_menu / "Eche.lnk"
                if create_shortcut(str(launch_target), str(shortcut_path), str(install_path), "Eche", brand_icon):
                    created_shortcuts.append(str(shortcut_path))

            self.progress(75, "Writing manifest...")
            uninstaller_path = install_path / "Uninstall.exe"
            try:
                import sys
                if getattr(sys, 'frozen', False):
                    self_exe = Path(sys.executable)
                    shutil.copy2(self_exe, uninstaller_path)
                    copied_files.append(str(uninstaller_path))
                else:
                    uninstall_py = install_path / "uninstall.py"
                    uninstall_py.write_text(self._generate_uninstall_script(), encoding="utf-8")
                    copied_files.append(str(uninstall_py))
            except Exception as exc:
                self.log(f"Uninstaller warning: {exc}")

            manifest = InstallManifest(version=APP_VERSION, install_dir=str(install_path), source=str(source_path), files=copied_files, shortcuts=created_shortcuts, installed_at=time.time(), options=asdict(opts))
            (install_path / MANIFEST_NAME).write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")

            self.progress(85, "Registering uninstall...")
            register_uninstall(str(install_path), str(uninstaller_path), str(main_exe) if main_exe else str(uninstaller_path), APP_VERSION)

            self.progress(100, "Install complete")
            if launch_target and Path(launch_target).exists():
                self.log(f"Launch: {launch_target}")
                try: (install_path / ".eche_launch_path").write_text(str(launch_target), encoding="utf-8")
                except: pass
            else:
                self.log(f"[SUCCESS] Eche installed to {install_path} - open folder to launch")

            return True
        except Exception as exc:
            self.log(f"[FATAL] Install failed: {exc}")
            import traceback; self.log(traceback.format_exc())
            return False

    def _copy_tree(self, src: Path, dst: Path, copied_files: List[str]):
        src = Path(src)
        total = sum(1 for _ in src.rglob("*") if _.is_file())
        count = 0
        for item in src.rglob("*"):
            if self._cancel: break
            if item.is_file():
                if ".venv" in str(item) or "__pycache__" in str(item): continue
                rel = item.relative_to(src)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, target)
                    copied_files.append(str(target))
                    count += 1
                    if count % 50 == 0:
                        pct = 10 + int(50 * count / max(total,1))
                        self.progress(min(pct,60), f"Copying {rel}...")
                except Exception as exc:
                    self.log(f"Copy warning {rel}: {exc}")

    def _auto_build_app(self, install_path: Path):
        install_path = Path(install_path)
        eche_source = install_path if (install_path / "core").is_dir() else install_path
        python_exe = None
        for cmd in [["py","-3.12"], ["py"], ["python"], ["python3"]]:
            try:
                r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    python_exe = cmd
                    break
            except: continue
        if not python_exe:
            self.log("Python not found - cannot auto-build")
            return None
        self.log(f"Using Python: {' '.join(python_exe)}")
        try:
            venv_py = eche_source / ".venv" / "Scripts" / "python.exe"
            if not venv_py.exists():
                subprocess.run(python_exe + ["-m", "venv", ".venv"], cwd=eche_source, check=True)
            subprocess.run([str(venv_py), "-m", "pip", "install", "-U", "pip"], cwd=eche_source, check=False)
            subprocess.run([str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=eche_source, check=False)
            env = os.environ.copy()
            env["ECHE_NO_PAUSE"] = "1"
            subprocess.run([str(eche_source / "BUILD.bat")], cwd=eche_source, shell=True, env=env)
            for cand in [eche_source.parent / "eche" / "Eche.exe", eche_source / "dist" / "Eche" / "Eche.exe", install_path / "eche" / "Eche.exe", install_path / "Eche.exe"]:
                if cand.exists():
                    return cand
        except Exception as e:
            self.log(f"Build error: {e}")
        return None

    def _write_beginner_next_steps(self, install_path: Path, main_exe):
        has_exe = bool(main_exe and self._is_real_app_exe(Path(main_exe)))
        lines = ["WELCOME TO ECHE", "==================", ""]
        if has_exe:
            lines += [f"Eche.exe is ready: {main_exe}", "Double-click it to start.", ""]
        else:
            lines += ["Eche is installed. If Eche.exe is missing, install Python from python.org", ""]
        (install_path / "START_HERE.txt").write_text("\n".join(lines), encoding="utf-8")

    def _write_portable_marker(self, install_path: Path):
        try: (Path(install_path) / "install.json").write_text('{"kind":"portable_app"}', encoding="utf-8")
        except: pass

    def _is_real_app_exe(self, path: Path) -> bool:
        p = Path(path)
        return p.is_file() and p.name.lower() in ("eche.exe","eche_app.exe") and ".venv" not in str(p).lower()

    def _find_main_exe(self, install_path: Path):
        install_path = Path(install_path)
        for cand in [install_path / "Eche.exe", install_path.parent / "eche" / "Eche.exe", install_path / "dist" / "Eche" / "Eche.exe", install_path / "eche" / "Eche.exe"]:
            if self._is_real_app_exe(cand):
                return cand
        for p in install_path.rglob("Eche.exe"):
            if self._is_real_app_exe(p):
                return p
        return None

    def _write_run_eche_bat(self, install_path: Path):
        bat = Path(install_path) / "RUN_ECHE.bat"
        bat.write_text('@echo off\ncd /d "%~dp0"\nif exist "Eche.exe" start "" "Eche.exe" & exit /b 0\nif exist "..\\eche\\Eche.exe" start "" "..\\eche\\Eche.exe" & exit /b 0\nif exist ".venv\\Scripts\\python.exe" ".venv\\Scripts\\python.exe" eche_app.py\npython eche_app.py\npause\n', encoding="utf-8")
        return bat

    def _find_brand_icon(self, install_path: Path, main_exe):
        bases = [Path(install_path)]
        if main_exe: bases.append(Path(main_exe).parent)
        for base in bases:
            for rel in [("assets","icon.ico"), ("icon.ico",)]:
                p = base.joinpath(*rel)
                if p.is_file(): return str(p)
        return str(main_exe) if main_exe else None

    def _recover_source_from_app(self, source_path: Path, dest: Path, copied_files: List[str]) -> bool:
        dest.mkdir(parents=True, exist_ok=True)
        donor = Path(source_path) if Path(source_path).is_dir() else Path(source_path).parent
        if (donor / "core").is_dir():
            self._copy_tree(donor, dest, copied_files)
            return True
        return False

    def _generate_uninstall_script(self) -> str:
        return 'import os, shutil, sys\nprint("Uninstalling Eche...")\n'
