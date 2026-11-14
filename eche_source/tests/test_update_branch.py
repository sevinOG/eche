# tests/test_update_branch.py
# Update Eche must pull the branch this install came from (or the one picked in
# Settings), never silently switch a branch install to main, and the handoff
# must not stop after the copy when the new release has no purge step.
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from core import app_update  # noqa: E402
from core.app_update import (  # noqa: E402
    INSTALL_INFO_FILE,
    archive_zip_url,
    clean_branch,
    extract_app_source_zip,
    read_install_info,
    resolve_update_branch,
    stage_github_source,
    write_handoff_script,
    write_install_info,
)

SHA = "0a19a8953b1560fe411bd6c4168f1924d70f8fda"


def _repo_zip(top: str = "eche-asset-toggles", comment: str = SHA) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        base = f"{top}/eche_source/"
        zf.writestr(base + "BUILD.bat", "@echo off\n")
        zf.writestr(base + "eche_app.py", "print('eche')\n")
        zf.writestr(base + "core/__init__.py", "")
        zf.writestr(base + "core/eche.py", "")
        zf.writestr(base + "gui/__init__.py", "")
        zf.writestr(base + "cogs/__init__.py", "")
        zf.comment = comment.encode("ascii")
    return buf.getvalue()


class BranchNameTests(unittest.TestCase):
    def test_accepts_plain_branches(self):
        for name in ("main", "asset-toggles", "hardened-asset", "feature/x_1.2"):
            self.assertEqual(clean_branch(name), name)
        self.assertEqual(clean_branch("  main  "), "main")

    def test_rejects_unsafe_names(self):
        for name in ("", None, "-x", "../main", "a..b", "a b", "a&b", "a|b", 'a"b',
                     "a%b", "/main", "main/", "x.lock", "a@{1}", "a//b", "a^b"):
            self.assertEqual(clean_branch(name), "", repr(name))

    def test_archive_url_uses_branch(self):
        self.assertEqual(
            archive_zip_url("asset-toggles"),
            "https://github.com/sevinOG/eche/archive/refs/heads/asset-toggles.zip",
        )
        self.assertTrue(archive_zip_url().endswith("/refs/heads/main.zip"))
        with self.assertRaises(ValueError):
            archive_zip_url("a&b")


class InstallInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eche-branch-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        write_install_info(self.root, "asset-toggles", SHA)
        info = read_install_info(self.root)
        self.assertEqual(info["branch"], "asset-toggles")
        self.assertEqual(info["commit"], SHA)
        self.assertEqual(info["repo"], "sevinOG/eche")

    def test_bad_values_are_dropped(self):
        (self.root / INSTALL_INFO_FILE).write_text(
            json.dumps({"branch": "a&b", "commit": "nothex"}), encoding="utf-8"
        )
        info = read_install_info(self.root)
        self.assertEqual(info["branch"], "")
        self.assertEqual(info["commit"], "")
        self.assertEqual(resolve_update_branch(None, self.root), "main")

    def test_missing_or_corrupt_file(self):
        self.assertEqual(read_install_info(self.root), {})
        self.assertEqual(read_install_info(None), {})
        (self.root / INSTALL_INFO_FILE).write_text("{not json", encoding="utf-8")
        self.assertEqual(read_install_info(self.root), {})

    def test_old_installer_marker_still_reads(self):
        (self.root / INSTALL_INFO_FILE).write_text(json.dumps({
            "repo": "sevinOG/eche", "branch": "main", "subdir": "eche_source",
        }), encoding="utf-8")
        self.assertEqual(read_install_info(self.root)["branch"], "main")

    def test_resolution_order(self):
        self.assertEqual(resolve_update_branch(None, self.root), "main")
        write_install_info(self.root, "asset-toggles", SHA)
        self.assertEqual(resolve_update_branch("", self.root), "asset-toggles")
        self.assertEqual(resolve_update_branch(None, self.root), "asset-toggles")
        self.assertEqual(resolve_update_branch("main", self.root), "main")
        self.assertEqual(resolve_update_branch("hardened-asset", self.root), "hardened-asset")
        # An invalid setting never wins over the installed branch.
        self.assertEqual(resolve_update_branch("a&b", self.root), "asset-toggles")

    def test_extract_records_branch_and_commit(self):
        dest = self.root / "stage"
        extract_app_source_zip(_repo_zip(), dest, branch="asset-toggles")
        info = read_install_info(dest)
        self.assertEqual(info["branch"], "asset-toggles")
        self.assertEqual(info["commit"], SHA)

    def test_extract_without_branch_writes_nothing(self):
        dest = self.root / "stage"
        extract_app_source_zip(_repo_zip(), dest)
        self.assertFalse((dest / INSTALL_INFO_FILE).exists())

    def test_extract_without_comment_keeps_branch(self):
        dest = self.root / "stage"
        extract_app_source_zip(_repo_zip(comment=""), dest, branch="asset-toggles")
        info = read_install_info(dest)
        self.assertEqual(info["branch"], "asset-toggles")
        self.assertEqual(info["commit"], "")

    def test_stage_downloads_the_requested_branch(self):
        seen = []

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            return _Resp(_repo_zip())

        with mock.patch.object(app_update, "urlopen", fake_urlopen):
            stage_github_source(self.root / "s1", branch="asset-toggles")
            stage_github_source(self.root / "s2")
            stage_github_source(self.root / "s3", branch="bad&name")
        self.assertTrue(seen[0].endswith("/refs/heads/asset-toggles.zip"))
        self.assertTrue(seen[1].endswith("/refs/heads/main.zip"))
        self.assertTrue(seen[2].endswith("/refs/heads/main.zip"))
        self.assertEqual(read_install_info(self.root / "s1")["branch"], "asset-toggles")
        self.assertEqual(read_install_info(self.root / "s2")["branch"], "main")


class SettingsWiringTests(unittest.TestCase):
    def test_update_branch_is_a_public_setting(self):
        from core.secrets import PUBLIC_KEYS, SECRET_KEYS
        self.assertIn("update_branch", PUBLIC_KEYS)
        self.assertNotIn("update_branch", SECRET_KEYS)

    def test_gui_passes_branch_to_the_worker(self):
        workers = (SOURCE / "gui" / "widgets" / "settings_workers.py").read_text(encoding="utf-8")
        self.assertIn("branch=self.branch", workers)
        window = (SOURCE / "gui" / "widgets" / "settingswindow.py").read_text(encoding="utf-8")
        self.assertIn("update_branch_edit", window)
        self.assertIn("resolve_update_branch", window)
        self.assertIn('payload["update_branch"]', window)
        self.assertNotIn('"GitHub  ·  sevinOG/eche main"', window)


_OLD_UPDATER = '''
REMOVED_PROGRAM_FILES = ("cogs/flash.py",)
def purge_removed_program_files(stage, target):
    import os
    p = os.path.join(target, "cogs", "flash.py")
    if os.path.isfile(p):
        os.remove(p)
        return ["cogs/flash.py"]
    return []
'''


class HandoffPurgeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eche-handoff-")
        root = Path(self.tmp.name)
        self.stage = root / "stage"
        self.target = root / "target"
        for tree in (self.stage, self.target):
            (tree / "core").mkdir(parents=True)
            (tree / "cogs").mkdir()
            (tree / "core" / "__init__.py").write_text("", encoding="utf-8")
            (tree / "cogs" / "keep.py").write_text("", encoding="utf-8")
        (self.target / "cogs" / "flash.py").write_text("old\n", encoding="utf-8")
        (self.target / "cogs" / "my_cog.py").write_text("mine\n", encoding="utf-8")
        (self.target / "config").mkdir()
        (self.target / "config" / "settings.json").write_text("{}", encoding="utf-8")
        self.files: list[str] = []

    def tearDown(self):
        for path in self.files:
            try:
                os.remove(path)
            except OSError:
                pass
        self.tmp.cleanup()

    def _script(self):
        script = write_handoff_script(
            pid=1, target=str(self.target), relaunch="x", stage=str(self.stage)
        )
        self.files.append(script)
        body = Path(script).read_text(encoding="utf-8")
        helper = next(
            line.split('"')[1] for line in body.splitlines()
            if line.strip().startswith("py -3 ") and "eche-update-purge-" in line
        )
        self.files.append(helper)
        return body, helper

    def _run(self, helper: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, helper], capture_output=True, text=True, timeout=60,
            cwd=tempfile.gettempdir(),
        )

    def test_script_never_stops_on_a_purge_failure(self):
        body, helper = self._script()
        self.assertNotIn("Could not remove stale program files", body)
        self.assertIn(helper, body)
        self.assertIn("WARNING", body)
        start = body.index("Removing program files")
        end = body.index("BUILD.bat is missing")
        self.assertNotIn("exit /b 1", body[start:end])

    def test_stage_without_purge_falls_back_to_installed_copy(self):
        # The installed copy has the purge step; the downloaded release does not.
        (self.target / "core" / "app_update.py").write_text(_OLD_UPDATER, encoding="utf-8")
        (self.stage / "core" / "app_update.py").write_text("X = 1\n", encoding="utf-8")
        _body, helper = self._script()
        # Simulate robocopy overwriting the installed file before the purge runs.
        (self.target / "core" / "app_update.py").write_text("X = 1\n", encoding="utf-8")
        res = self._run(helper)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("previously installed copy", res.stdout)
        self.assertFalse((self.target / "cogs" / "flash.py").exists())
        self.assertTrue((self.target / "cogs" / "my_cog.py").exists())
        self.assertTrue((self.target / "config" / "settings.json").exists())

    def test_no_purge_anywhere_skips_with_warning(self):
        (self.stage / "core" / "app_update.py").write_text("X = 1\n", encoding="utf-8")
        _body, helper = self._script()
        res = self._run(helper)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WARNING: skipping stale-file cleanup", res.stdout)
        self.assertTrue((self.target / "cogs" / "flash.py").exists())

    def test_purge_error_warns_and_continues(self):
        (self.stage / "core" / "app_update.py").write_text(
            "def purge_removed_program_files(s, t):\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        _body, helper = self._script()
        res = self._run(helper)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("cleanup failed", res.stdout)

    def test_stage_with_purge_uses_it(self):
        real = (SOURCE / "core" / "app_update.py").read_text(encoding="utf-8")
        (self.stage / "core" / "app_update.py").write_text(real, encoding="utf-8")
        _body, helper = self._script()
        res = self._run(helper)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Removed 1 stale program file", res.stdout)
        self.assertFalse((self.target / "cogs" / "flash.py").exists())
        self.assertTrue((self.target / "cogs" / "my_cog.py").exists())

    def test_local_update_writes_no_helper(self):
        script = write_handoff_script(pid=1, target=str(self.target), relaunch="x", stage=None)
        self.files.append(script)
        body = Path(script).read_text(encoding="utf-8")
        self.assertNotIn("eche-update-purge-", body)


if __name__ == "__main__":
    unittest.main()
