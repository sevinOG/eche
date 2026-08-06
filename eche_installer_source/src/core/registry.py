"""Windows registry for Add/Remove Programs"""
import platform
from pathlib import Path

APP_NAME = "Eche"
APP_PUBLISHER = "Eche Team"
APP_VERSION = "1.3.0"

def register_uninstall(install_dir: str, uninstall_exe: str, display_icon: str = None, version: str = APP_VERSION) -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Eche"
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f"'{uninstall_exe}'")
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, display_icon or uninstall_exe)
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, _calc_size_kb(install_dir))
            winreg.CloseKey(key)
            return True
        except PermissionError:
            return False
    except Exception as exc:
        print(f"Registry register failed: {exc}")
        return False

def unregister_uninstall() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Eche"
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            return True
        except FileNotFoundError:
            return True
        except Exception:
            try:
                winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                return True
            except Exception as exc:
                print(f"Registry unregister failed: {exc}")
                return False
    except Exception as exc:
        print(f"Registry module failed: {exc}")
        return False

def is_installed():
    if platform.system() != "Windows":
        return False, ""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Eche"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(root, key_path)
                loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                winreg.CloseKey(key)
                if loc and Path(loc).exists():
                    return True, loc
            except FileNotFoundError:
                continue
        return False, ""
    except Exception:
        return False, ""

def _calc_size_kb(path: str) -> int:
    try:
        total = 0
        for p in Path(path).rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total // 1024
    except Exception:
        return 0
