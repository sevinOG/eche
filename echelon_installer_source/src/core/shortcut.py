"""Shortcut creation"""
import os
import platform
import subprocess
from pathlib import Path

def create_shortcut(target_path: str, shortcut_path: str, working_dir: str = None, description: str = "Echelon", icon_path: str = None) -> bool:
    target_path = str(Path(target_path).resolve())
    shortcut_path = str(Path(shortcut_path).resolve())
    working_dir = str(Path(working_dir).resolve()) if working_dir else str(Path(target_path).parent)
    os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.Targetpath = target_path
        shortcut.WorkingDirectory = working_dir
        shortcut.Description = description
        if icon_path and Path(icon_path).exists():
            shortcut.IconLocation = str(Path(icon_path).resolve())
        shortcut.save()
        return True
    except Exception:
        pass
    if platform.system() == "Windows":
        try:
            icon_line = f"$s.IconLocation = '{icon_path}';" if icon_path and Path(icon_path).exists() else ""
            ps = f"""
$WshShell = New-Object -comObject WScript.Shell
$s = $WshShell.CreateShortcut('{shortcut_path}')
$s.TargetPath = '{target_path}'
$s.WorkingDirectory = '{working_dir}'
$s.Description = '{description}'
{icon_line}
$s.Save()
"""
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True, timeout=10)
            return Path(shortcut_path).exists()
        except Exception as exc:
            print(f"Shortcut PS fallback failed: {exc}")
            return False
    else:
        try:
            desktop_content = f"""[Desktop Entry]
Type=Application
Name=Echelon
Comment={description}
Exec={target_path}
Path={working_dir}
Terminal=false
Categories=Utility;
"""
            Path(shortcut_path).write_text(desktop_content, encoding="utf-8")
            return True
        except Exception as exc:
            print(f"Desktop file failed: {exc}")
            return False

def get_desktop_path() -> Path:
    return Path(os.path.expanduser("~/Desktop"))

def get_start_menu_path() -> Path:
    if platform.system() == "Windows":
        return Path(os.path.expanduser("~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs"))
    return Path(os.path.expanduser("~/.local/share/applications"))

def remove_shortcut(shortcut_path: str) -> bool:
    try:
        p = Path(shortcut_path)
        if p.exists():
            p.unlink()
            return True
        return False
    except Exception:
        return False
