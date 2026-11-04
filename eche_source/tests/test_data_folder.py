"""Offline tests for the Data folder pointer, move and update/uninstall guards."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)
INSTALLER = os.path.join(os.path.dirname(SOURCE), "eche_installer_source", "src")

from core import paths, data_folder  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pkg = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(self.pkg, "config"))
        Path(self.pkg, "config", "settings.json").write_text('{"a": 1}')
        Path(self.pkg, "config", "secrets.dpapi.json").write_text("SECRET")
        Path(self.pkg, "config", "admin_tools.md").write_text("tools")
        Path(self.pkg, "config", "summarizer_prompt.txt").write_text("prompt")
        self.ptr = os.path.join(self.tmp, "local", "Eche", "data_root.txt")
        env = {k: v for k, v in os.environ.items() if k != "ECHE_USER_ROOT"}
        env["ECHE_POINTER_FILE"] = self.ptr
        self.envp = mock.patch.dict(os.environ, env, clear=True)
        self.envp.start()
        self.pkgp = mock.patch.object(paths, "package_root", return_value=self.pkg)
        self.pkgp.start()
        self.protp = mock.patch.object(data_folder, "_protected_roots", return_value=[self.pkg])
        self.protp.start()

    def tearDown(self):
        self.protp.stop()
        self.pkgp.stop()
        self.envp.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ResolutionTests(_Base):
    def test_default_is_package(self):
        self.assertEqual(paths.user_dir(), self.pkg)
        self.assertEqual(paths.user_dir_source(), "package")

    def test_pointer_beats_package(self):
        safe = os.path.join(self.tmp, "EcheSafe")
        os.makedirs(safe)
        paths.write_pointer(safe)
        self.assertEqual(paths.user_dir(), os.path.abspath(safe))
        self.assertEqual(paths.user_dir_source(), "pointer")

    def test_env_beats_pointer(self):
        safe = os.path.join(self.tmp, "EcheSafe")
        env = os.path.join(self.tmp, "envroot")
        os.makedirs(safe); os.makedirs(env)
        paths.write_pointer(safe)
        with mock.patch.dict(os.environ, {"ECHE_USER_ROOT": env}):
            self.assertEqual(paths.user_dir(), os.path.abspath(env))

    def test_missing_pointer_target_falls_back(self):
        paths.write_pointer(os.path.join(self.tmp, "gone"))
        self.assertEqual(paths.user_dir(), self.pkg)

    def test_pointer_ignores_comments_quotes_bom(self):
        safe = os.path.join(self.tmp, "Eche Safe")
        os.makedirs(safe)
        os.makedirs(os.path.dirname(self.ptr), exist_ok=True)
        Path(self.ptr).write_text('\ufeff# note\n\n"' + safe + '"\n', encoding="utf-8")
        self.assertEqual(paths.read_pointer(), os.path.abspath(safe))

    def test_clear_pointer(self):
        safe = os.path.join(self.tmp, "EcheSafe"); os.makedirs(safe)
        paths.write_pointer(safe)
        paths.write_pointer(None)
        self.assertFalse(os.path.exists(self.ptr))
        paths.write_pointer(None)  # idempotent

    def test_default_pointer_location_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}, clear=True):
            self.assertTrue(paths.pointer_file().endswith(os.path.join("Eche", "data_root.txt")))
            self.assertIn("AppData", paths.pointer_file())


class MoveTests(_Base):
    def test_move_fresh_folder(self):
        safe = os.path.join(self.tmp, "EcheSafe")
        plan = data_folder.plan_move(safe)
        self.assertEqual(plan.conflicts, [])
        res = data_folder.apply_move(plan)
        self.assertEqual(res["copied"], 4)
        for fn in ("settings.json", "secrets.dpapi.json", "admin_tools.md", "summarizer_prompt.txt"):
            self.assertTrue(os.path.isfile(os.path.join(safe, "config", fn)))
        self.assertEqual(paths.read_pointer(), os.path.abspath(safe))
        # source left in place
        self.assertTrue(os.path.isfile(os.path.join(self.pkg, "config", "settings.json")))

    def test_existing_copy_identical_and_conflict(self):
        safe = os.path.join(self.tmp, "EcheSafe")
        os.makedirs(os.path.join(safe, "config"))
        Path(safe, "config", "secrets.dpapi.json").write_text("SECRET")    # same
        Path(safe, "config", "settings.json").write_text('{"old": 1}')     # differs
        plan = data_folder.plan_move(safe)
        self.assertEqual(len(plan.same), 1)
        self.assertEqual([os.path.basename(c) for c in plan.conflicts], ["settings.json"])
        res = data_folder.apply_move(plan, overwrite=False)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(Path(safe, "config", "settings.json").read_text(), '{"old": 1}')

    def test_overwrite_when_confirmed(self):
        safe = os.path.join(self.tmp, "EcheSafe")
        os.makedirs(os.path.join(safe, "config"))
        Path(safe, "config", "settings.json").write_text('{"old": 1}')
        data_folder.apply_move(data_folder.plan_move(safe), overwrite=True)
        self.assertEqual(Path(safe, "config", "settings.json").read_text(), '{"a": 1}')

    def test_rejects_inside_or_containing_app(self):
        self.assertIsNotNone(data_folder.validate_target(os.path.join(self.pkg, "keep")))
        self.assertIsNotNone(data_folder.validate_target(self.pkg))
        self.assertIsNotNone(data_folder.validate_target(self.tmp))
        self.assertIsNotNone(data_folder.validate_target(""))
        self.assertIsNone(data_folder.validate_target(os.path.join(self.tmp, "EcheSafe")))
        with self.assertRaises(ValueError):
            data_folder.apply_move(data_folder.MovePlan(source=self.pkg, target=os.path.join(self.pkg, "x")))

    def test_rejects_file(self):
        f = os.path.join(self.tmp, "afile"); Path(f).write_text("x")
        self.assertIsNotNone(data_folder.validate_target(f))


class GuardTests(_Base):
    def test_handoff_never_removes_data_folder(self):
        from core import app_update
        safe = os.path.join(self.tmp, "EcheSafe"); os.makedirs(safe)
        paths.write_pointer(safe)
        target = os.path.join(self.tmp, "eche_source")
        script = app_update.write_handoff_script(pid=1, target=target, relaunch="x.exe", stage=safe)
        try:
            text = Path(script).read_text()
            self.assertNotIn(f'rmdir /s /q "{safe}"', text)
            self.assertNotIn(safe, text)
        finally:
            os.remove(script)

    def test_handoff_refuses_target_containing_data(self):
        from core import app_update
        target = os.path.join(self.tmp, "eche_source")
        safe = os.path.join(target, "data"); os.makedirs(safe)
        paths.write_pointer(safe)
        with self.assertRaises(ValueError):
            app_update.write_handoff_script(pid=1, target=target, relaunch="x.exe", stage=None)

    def test_skip_lists_still_protect_secrets(self):
        from core import app_update
        self.assertIn("secrets.dpapi.json", app_update._SKIP_FILES)
        self.assertIn("settings.json", app_update._SKIP_FILES)

    @unittest.skipUnless(os.path.isdir(INSTALLER), "installer source missing")
    def test_uninstaller_keeps_data_inside_install(self):
        sys.path.insert(0, INSTALLER)
        try:
            with mock.patch.dict(sys.modules, {
                "core.shortcut": mock.MagicMock(), "core.registry": mock.MagicMock(),
                "core.installer": mock.MagicMock(MANIFEST_NAME=".eche_install_manifest.json"),
            }):
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "core.uninstaller", os.path.join(INSTALLER, "core", "uninstaller.py"),
                    submodule_search_locations=None)
                mod = importlib.util.module_from_spec(spec)
                mod.__package__ = "core"
                spec.loader.exec_module(mod)
        finally:
            sys.path.remove(INSTALLER)
        inst = os.path.join(self.tmp, "Install")
        safe = os.path.join(inst, "EcheSafe"); os.makedirs(os.path.join(safe, "config"))
        Path(safe, "config", "secrets.dpapi.json").write_text("S")
        Path(inst, "Eche.exe").write_text("x")
        paths.write_pointer(safe)
        self.assertTrue(mod.Uninstaller(log_callback=lambda m: None).uninstall(inst))
        self.assertTrue(os.path.isfile(os.path.join(safe, "config", "secrets.dpapi.json")))
        # outside data folder: whole dir removed normally
        inst2 = os.path.join(self.tmp, "Install2"); os.makedirs(inst2)
        self.assertTrue(mod.Uninstaller(log_callback=lambda m: None).uninstall(inst2))
        self.assertFalse(os.path.exists(inst2))


if __name__ == "__main__":
    unittest.main()
