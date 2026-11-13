# tests/test_denied_modules_and_update.py
# Leftover media modules must not load, and the updater must delete only
# program files Eche itself shipped and later removed, never user cogs.
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from core.app_update import (  # noqa: E402
    REMOVED_PROGRAM_FILES,
    iter_shipped_code_files,
    purge_removed_program_files,
    write_handoff_script,
)
from core.denied_modules import DENIED_MODULES, is_denied_module  # noqa: E402
from core.media_scan import module_is_blocked  # noqa: E402


class DeniedModuleTests(unittest.TestCase):
    def test_known_media_modules_are_denied(self):
        for name in ("cogs.flash", "flash"):
            self.assertTrue(is_denied_module(name), name)
        for name in (
            "cogs.asset",
            "core.imagine",
            "core.tools_asset",
            "core.asset_safety",
            "asset",
        ):
            self.assertFalse(is_denied_module(name), name)
        self.assertFalse(is_denied_module("cogs.image_search"))
        self.assertFalse(is_denied_module("cogs.help.help"))
        self.assertFalse(is_denied_module("core.bot"))

    def test_loader_and_manager_consult_the_denylist(self):
        from core import gui_bridge
        from gui.widgets import cogmanager

        src = Path(gui_bridge.__file__).read_text(encoding="utf-8")
        self.assertIn("media_scan", src)
        self.assertIn("module_is_blocked", src)
        mgr = Path(cogmanager.__file__).read_text(encoding="utf-8")
        self.assertIn("module_is_blocked", mgr)
        self.assertTrue(DENIED_MODULES)

    def test_media_scan_blocks_generation_source(self):
        with tempfile.TemporaryDirectory(prefix="eche-scan-") as raw:
            root = Path(raw)
            (root / "cogs").mkdir()
            drop = root / "cogs" / "sneaky.py"
            drop.write_text(
                "import requests\nrequests.post('https://api.openai.com/v1/images/generations')\n",
                encoding="utf-8",
            )
            reason = module_is_blocked("cogs.sneaky", source_root=root)
            self.assertIn("generation marker", reason)
            clean = root / "cogs" / "utils.py"
            clean.write_text("# user utils cog\n", encoding="utf-8")
            self.assertEqual(module_is_blocked("cogs.utils", source_root=root), "")


class UpdaterPurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eche-purge-")
        self.root = Path(self.tmp.name)
        self.stage = self.root / "stage"
        self.target = self.root / "target"
        for tree in (self.stage, self.target):
            for folder in ("core", "cogs", "gui"):
                (tree / folder).mkdir(parents=True)
            (tree / "core" / "bot.py").write_text("# bot\n", encoding="utf-8")
            (tree / "cogs" / "help.py").write_text("# help\n", encoding="utf-8")
        # Stale generators only in the installed tree.
        (self.target / "cogs" / "asset.py").write_text("# leftover asset\n", encoding="utf-8")
        (self.target / "cogs" / "flash.py").write_text("# leftover flash\n", encoding="utf-8")
        (self.target / "core" / "imagine.py").write_text("# leftover imagine\n", encoding="utf-8")
        # User data that must survive, including if placed near the app.
        (self.target / "settings.json").write_text("{}", encoding="utf-8")
        (self.target / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (self.target / "config").mkdir()
        (self.target / "config" / "personality.txt").write_text("hi", encoding="utf-8")
        (self.target / "memories").mkdir()
        (self.target / "memories" / "user.txt").write_text("pin", encoding="utf-8")
        (self.target / "logs").mkdir()
        (self.target / "logs" / "gui.log").write_text("log", encoding="utf-8")
        # A leftover under a protected name must not be touched even in core/.
        (self.target / "core" / "config").mkdir()
        (self.target / "core" / "config" / "stale.py").write_text("nope", encoding="utf-8")
        # User-dropped cogs that must survive an update.
        (self.target / "cogs" / "utils.py").write_text("# user utils\n", encoding="utf-8")
        (self.target / "cogs" / "games").mkdir()
        (self.target / "cogs" / "games" / "mygame.py").write_text("# user game\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_purge_deletes_stale_code_only(self):
        removed = purge_removed_program_files(self.stage, self.target)
        self.assertEqual(set(removed), {"cogs/flash.py"})
        # Official Imagine files are no longer tombstoned; leftovers stay
        # until a later copy from the stage replaces them.
        self.assertTrue((self.target / "cogs" / "asset.py").exists())
        self.assertFalse((self.target / "cogs" / "flash.py").exists())
        self.assertTrue((self.target / "core" / "imagine.py").exists())
        self.assertTrue((self.target / "core" / "bot.py").exists())
        self.assertTrue((self.target / "cogs" / "help.py").exists())
        self.assertTrue((self.target / "settings.json").exists())
        self.assertTrue((self.target / ".env").exists())
        self.assertEqual((self.target / ".env").read_text(encoding="utf-8"), "TOKEN=secret\n")
        self.assertTrue((self.target / "config" / "personality.txt").exists())
        self.assertTrue((self.target / "memories" / "user.txt").exists())
        self.assertTrue((self.target / "logs" / "gui.log").exists())
        self.assertTrue((self.target / "core" / "config" / "stale.py").exists())

    def test_user_dropped_cog_survives_update(self):
        removed = purge_removed_program_files(self.stage, self.target)
        self.assertNotIn("cogs/utils.py", removed)
        self.assertNotIn("cogs/games/mygame.py", removed)
        self.assertTrue((self.target / "cogs" / "utils.py").exists())
        self.assertEqual(
            (self.target / "cogs" / "utils.py").read_text(encoding="utf-8"),
            "# user utils\n",
        )
        self.assertTrue((self.target / "cogs" / "games" / "mygame.py").exists())
        self.assertEqual(
            (self.target / "cogs" / "games" / "mygame.py").read_text(encoding="utf-8"),
            "# user game\n",
        )
        self.assertTrue((self.target / "cogs" / "asset.py").exists())

    def test_user_dropped_cog_survives_reinstall(self):
        # Reinstall copies the new tree over the existing folder, then
        # runs the same tombstone purge. User files that were not in the
        # source tree must still be there afterwards.
        import shutil

        for item in self.stage.rglob("*"):
            if not item.is_file():
                continue
            dest = self.target / item.relative_to(self.stage)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
        self.assertTrue((self.target / "cogs" / "utils.py").exists())
        self.assertTrue((self.target / "cogs" / "asset.py").exists())

        installer = _load_installer()
        logs: list[str] = []
        inst = installer.Installer(log_callback=logs.append)
        inst._purge_removed_program_files(self.stage, self.target)
        self.assertTrue((self.target / "cogs" / "utils.py").exists())
        self.assertTrue((self.target / "cogs" / "games" / "mygame.py").exists())
        self.assertTrue((self.target / "cogs" / "asset.py").exists())
        self.assertFalse((self.target / "cogs" / "flash.py").exists())
        self.assertTrue((self.target / "core" / "imagine.py").exists())
        self.assertTrue(any("Removed stale program file" in line for line in logs))

    def test_purge_uses_normcase(self):
        src = (SOURCE / "core" / "app_update.py").read_text(encoding="utf-8")
        self.assertIn("os.path.normcase", src)
        # Simulate Windows case folding: Flash.py is the same tombstone.
        original = os.path.normcase

        def folded(value):
            return original(value).lower().replace("\\", "/")

        flash = self.target / "cogs" / "Flash.py"
        if not flash.exists():
            (self.target / "cogs" / "flash.py").replace(flash)
        with mock.patch("os.path.normcase", side_effect=folded):
            removed = purge_removed_program_files(self.stage, self.target)
        removed_norm = {os.path.normcase(item).lower().replace("\\", "/") for item in removed}
        self.assertIn("cogs/flash.py", removed_norm)
        self.assertFalse(flash.exists())
        self.assertFalse((self.target / "cogs" / "flash.py").exists())

    def test_purge_skips_symlinks_and_outside_targets(self):
        outside = self.root / "outside"
        outside.mkdir()
        victim = outside / "secret.py"
        victim.write_text("keep me\n", encoding="utf-8")
        stale = self.target / "cogs" / "flash.py"
        stale.unlink()
        stale.symlink_to(victim)
        removed = purge_removed_program_files(self.stage, self.target)
        self.assertNotIn("cogs/flash.py", removed)
        self.assertTrue(victim.exists())
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep me\n")
        self.assertTrue(stale.is_symlink())

    def test_purge_refuses_empty_manifest(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(RuntimeError):
            purge_removed_program_files(empty, self.target)

    def test_handoff_script_runs_the_purge(self):
        script = write_handoff_script(pid=1, target=str(self.target), relaunch="x", stage=str(self.stage))
        try:
            body = Path(script).read_text(encoding="utf-8", errors="replace")
        finally:
            try:
                os.remove(script)
            except OSError:
                pass
        self.assertIn("purge_removed_program_files", body)
        self.assertIn("/PURGE", Path(SOURCE, "core", "app_update.py").read_text(encoding="utf-8"))
        self.assertTrue(REMOVED_PROGRAM_FILES)

    def test_games_registry_is_explicit(self):
        src = (SOURCE / "cogs" / "games" / "registry.py").read_text(encoding="utf-8")
        self.assertNotIn("pkgutil", src)
        self.assertNotIn("iter_modules", src)
        self.assertIn("_KNOWN_GAMES", src)
        self.assertIn("highlow", src)
        self.assertIn("slots", src)
        self.assertIn("simon", src)
        self.assertIn("holdem", src)


def _load_installer():
    inst_core = SOURCE.parent / "eche_installer_source" / "src" / "core"
    if not inst_core.is_dir():
        raise unittest.SkipTest("installer source not next to eche_source")
    pkg = types.ModuleType("eche_installer_core")
    pkg.__path__ = [str(inst_core)]
    pkg.__package__ = "eche_installer_core"
    sys.modules["eche_installer_core"] = pkg
    shortcut = types.ModuleType("eche_installer_core.shortcut")
    shortcut.create_shortcut = lambda *a, **k: False
    shortcut.get_desktop_path = lambda: Path(".")
    shortcut.get_start_menu_path = lambda: Path(".")
    sys.modules["eche_installer_core.shortcut"] = shortcut
    registry = types.ModuleType("eche_installer_core.registry")
    registry.register_uninstall = lambda *a, **k: None
    registry.APP_VERSION = "0"
    sys.modules["eche_installer_core.registry"] = registry
    spec = importlib.util.spec_from_file_location(
        "eche_installer_core.installer",
        inst_core / "installer.py",
        submodule_search_locations=[str(inst_core)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "eche_installer_core"
    sys.modules["eche_installer_core.installer"] = mod
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
