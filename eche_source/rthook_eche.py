# PyInstaller runtime hook:
# 1) Put package / _internal root on sys.path so core/cogs/gui import.
# 2) Optional legacy alias: eche_ecosystem.* → * (older frozen builds only).
import os
import sys
import importlib.abc
import importlib.machinery


def _setup_cwd_and_path():
    if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        return
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    cur = exe_dir
    for _ in range(5):
        if os.path.isdir(os.path.join(cur, "core")) and os.path.isdir(os.path.join(cur, "gui")):
            if cur not in sys.path:
                sys.path.insert(0, cur)
            os.chdir(cur)
            return
        internal = os.path.join(cur, "_internal")
        if os.path.isdir(os.path.join(internal, "core")) and os.path.isdir(
            os.path.join(internal, "gui")
        ):
            if internal not in sys.path:
                sys.path.insert(0, internal)
            if cur not in sys.path:
                sys.path.insert(0, cur)
            os.chdir(cur)
            return
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent


_setup_cwd_and_path()

# Keep frozen builds that still reference eche_ecosystem.* working
# until the next install.bat rebuild. Source code no longer uses this prefix.
_LEGACY_PREFIX = "eche_ecosystem."


class _LegacyAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(_LEGACY_PREFIX):
            return None
        real_name = fullname[len(_LEGACY_PREFIX) :]
        try:
            for finder in sys.meta_path:
                if finder is self:
                    continue
                try:
                    spec = finder.find_spec(real_name, path, target)
                    if spec:
                        return spec
                except Exception:
                    continue
            return importlib.machinery.PathFinder.find_spec(real_name, path, target)
        except Exception:
            return None


if not any(isinstance(f, _LegacyAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _LegacyAliasFinder())
