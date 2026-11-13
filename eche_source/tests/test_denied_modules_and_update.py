# tests/test_denied_modules_and_update.py
# Leftover media modules must not load, and the updater must delete
# program files the new release no longer ships without touching user data.
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
import sys
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from core.app_update import (  # noqa: E402
    iter_shipped_code_files,
    purge_removed_program_files,
    write_handoff_script,
)
from core.denied_modules import DENIED_MODULES, is_denied_module  # noqa: E402


class DeniedModuleTests(unittest.TestCase):
    def test_known_media_modules_are_denied(self):
        for name in (
            "cogs.asset",
            "cogs.flash",
            "core.imagine",
            "core.tools_asset",
            "core.asset_message",
            "asset",
            "flash",
        ):
            self.assertTrue(is_denied_module(name), name)
        self.assertFalse(is_denied_module("cogs.image_search"))
        self.assertFalse(is_denied_module("cogs.help.help"))
        self.assertFalse(is_denied_module("core.bot"))

    def test_loader_and_manager_consult_the_denylist(self):
        from core import gui_bridge
        from gui.widgets import cogmanager

        src = Path(gui_bridge.__file__).read_text(encoding="utf-8")
        self.assertIn("denied_modules", src)
        self.assertIn("is_denied_module", src)
        mgr = Path(cogmanager.__file__).read_text(encoding="utf-8")
        self.assertIn("is_denied_module", mgr)
        self.assertTrue(DENIED_MODULES)


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

    def tearDown(self):
        self.tmp.cleanup()

    def test_purge_deletes_stale_code_only(self):
        removed = purge_removed_program_files(self.stage, self.target)
        self.assertEqual(
            set(removed),
            {"cogs/asset.py", "cogs/flash.py", "core/imagine.py"},
        )
        self.assertFalse((self.target / "cogs" / "asset.py").exists())
        self.assertFalse((self.target / "cogs" / "flash.py").exists())
        self.assertFalse((self.target / "core" / "imagine.py").exists())
        self.assertTrue((self.target / "core" / "bot.py").exists())
        self.assertTrue((self.target / "cogs" / "help.py").exists())
        self.assertTrue((self.target / "settings.json").exists())
        self.assertTrue((self.target / ".env").exists())
        self.assertEqual((self.target / ".env").read_text(encoding="utf-8"), "TOKEN=secret\n")
        self.assertTrue((self.target / "config" / "personality.txt").exists())
        self.assertTrue((self.target / "memories" / "user.txt").exists())
        self.assertTrue((self.target / "logs" / "gui.log").exists())
        self.assertTrue((self.target / "core" / "config" / "stale.py").exists())

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


class UnsplashFilterTests(unittest.TestCase):
    def test_unsplash_uses_high_filter_and_blocklist(self):
        src = (SOURCE / "cogs" / "image_search.py").read_text(encoding="utf-8")
        self.assertIn("is_blocked", src)
        self.assertIn('content_filter": "high"', src)
        self.assertIn("author_is_owner", src)
