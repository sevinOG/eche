"""Owner tier above Admin (core/permissions.py). Temp files and mocks only."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from discord.ext import commands  # noqa: E402

from core import permissions as P  # noqa: E402

OWNER = 123456789012345678
ADMIN = 223456789012345678
OTHER = 323456789012345678


def run(coro):
    return asyncio.run(coro)


def user(uid):
    return SimpleNamespace(id=uid, bot=False)


class Base(unittest.TestCase):
    def setUp(self):
        P._reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        env = {k: v for k, v in os.environ.items() if k != P.ENV_FILE}
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.admins = {OWNER, ADMIN}
        self.patches = [
            mock.patch.object(P, "backend_dir", lambda: self.dir),
            mock.patch("core.admin_tools.author_is_owner",
                       mock.AsyncMock(side_effect=lambda _b, u: getattr(u, "id", None) in self.admins)),
            mock.patch("core.admin_tools.configured_owner_ids", lambda: sorted(self.admins)),
        ]
        for p in self.patches:
            p.start()
        self.logged = []
        rec = mock.patch("core.audit.record", side_effect=lambda event, **f: self.logged.append((event, f)))
        rec.start()
        self.patches.append(rec)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()
        P._reset_for_tests()

    def write(self, data, name="waerhouse_runtime.json", raw=None):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(data))
        P._cache["checked"] = None  # skip the 2 s wait
        P._cache["sig"] = None
        return path

    def hosted(self, value=""):
        os.environ[P.ENV_FILE] = value


class Standalone(Base):
    def test_standalone_owner_is_admin(self):
        bot = SimpleNamespace()
        self.assertFalse(P.hosted(bot))
        self.assertEqual(P.owner_state(bot), "standalone")
        self.assertTrue(run(P.is_owner(bot, user(ADMIN))))
        self.assertTrue(run(P.is_owner(bot, user(OWNER))))
        self.assertFalse(run(P.is_owner(bot, user(OTHER))))

    def test_owner_file_ignored_when_standalone(self):
        self.write({"owner_uid": str(OTHER)})
        self.assertFalse(run(P.is_owner(SimpleNamespace(), user(OTHER))))


class Hosted(Base):
    def test_env_relative_path_resolves_against_backend(self):
        self.hosted("runtime/owner.json")
        os.makedirs(os.path.join(self.dir, "runtime"))
        self.write({"owner_uid": str(OWNER)}, name=os.path.join("runtime", "owner.json"))
        self.assertEqual(P.owner_file(), os.path.join(self.dir, "runtime", "owner.json"))
        self.assertEqual(P.owner_uid(), OWNER)

    def test_blank_env_uses_backend_default_file(self):
        self.hosted("")
        self.write({"owner_uid": str(OWNER)})
        self.assertEqual(P.owner_file(), os.path.join(self.dir, "waerhouse_runtime.json"))
        self.assertEqual(P.owner_state(), "ok")

    def test_absolute_env_path(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        path = os.path.join(other.name, "r.json")
        with open(path, "w") as f:
            json.dump({"owner_uid": OWNER}, f)
        self.hosted(path)
        self.assertEqual(P.owner_uid(), OWNER)

    def test_class_attribute_turns_hosting_on(self):
        bot = SimpleNamespace(owner_tier_source="mine.json")
        self.write({"owner_uid": str(OWNER)}, name="mine.json")
        self.assertTrue(P.hosted(bot))
        self.assertTrue(run(P.is_owner(bot, user(OWNER))))
        self.assertFalse(run(P.is_owner(bot, user(ADMIN))))   # Admin is not Owner
        self.assertTrue(run(P.is_admin(bot, user(ADMIN))))    # Admin powers stay

    def test_register_bot_remembers_the_class_source(self):
        class Hosted:
            owner_tier_source = "mine.json"
        P.register_bot(Hosted())
        self.assertTrue(P.hosted())

    def test_only_the_file_uid_is_owner(self):
        self.hosted("")
        self.write({"owner_uid": str(OWNER)})
        self.assertTrue(run(P.is_owner(None, user(OWNER))))
        self.assertFalse(run(P.is_owner(None, user(ADMIN))))
        self.assertFalse(run(P.is_owner(None, user(OTHER))))
        self.assertFalse(run(P.is_owner(None, None)))

    def test_owner_must_also_be_admin(self):
        self.hosted("")
        self.write({"owner_uid": str(OTHER)})
        self.assertFalse(run(P.is_owner(None, user(OTHER))))

    def test_bad_files_fail_closed(self):
        self.hosted("")
        cases = [
            ("missing", None, None),
            ("invalid", None, ""),
            ("invalid", None, "not json"),
            ("invalid", [str(OWNER)], None),
            ("invalid", {}, None),
            ("invalid", {"owner_uid": "1234567890123456"}, None),        # 16 digits
            ("invalid", {"owner_uid": "123456789012345678901"}, None),   # 21 digits
            ("invalid", {"owner_uid": f"<@{OWNER}>"}, None),
            ("invalid", {"owner_uid": f" {OWNER}"}, None),
            ("invalid", {"owner_uid": True}, None),
            ("invalid", {"owner_uid": float(OWNER)}, None),
            ("invalid", {"owner_uid": None}, None),
            ("invalid", {"uid": str(OWNER)}, None),
        ]
        for state, data, raw in cases:
            with self.subTest(data=data, raw=raw):
                path = os.path.join(self.dir, "waerhouse_runtime.json")
                if os.path.exists(path):
                    os.remove(path)
                if data is not None or raw is not None:
                    self.write(data, raw=raw)
                P._cache["checked"] = None
                P._cache["sig"] = None
                self.assertEqual(P.owner_state(), state)
                self.assertIsNone(P.owner_uid())
                self.assertFalse(run(P.is_owner(None, user(OWNER))))
                self.assertTrue(run(P.is_admin(None, user(OWNER))))  # Admin keeps working

    def test_oversize_file_refused(self):
        self.hosted("")
        self.write(None, raw=json.dumps({"owner_uid": str(OWNER), "pad": "x" * (P.MAX_BYTES + 10)}))
        self.assertEqual(P.owner_state(), "invalid")

    @unittest.skipIf(not hasattr(os, "symlink"), "no symlinks")
    def test_symlink_refused(self):
        self.hosted("")
        real = self.write({"owner_uid": str(OWNER)}, name="real.json")
        try:
            os.symlink(real, os.path.join(self.dir, "waerhouse_runtime.json"))
        except OSError:
            self.skipTest("cannot symlink")
        self.assertEqual(P.owner_state(), "invalid")

    def test_deleting_the_file_never_means_standalone(self):
        self.hosted("")
        path = self.write({"owner_uid": str(OWNER)})
        self.assertTrue(run(P.is_owner(None, user(OWNER))))
        os.remove(path)
        P._cache["checked"] = None
        self.assertEqual(P.owner_state(), "missing")
        self.assertFalse(run(P.is_owner(None, user(OWNER))))
        self.assertFalse(run(P.is_owner(None, user(ADMIN))))

    def test_refresh_every_two_seconds(self):
        self.hosted("")
        path = os.path.join(self.dir, "waerhouse_runtime.json")
        with open(path, "w") as f:
            json.dump({"owner_uid": str(OWNER)}, f)
        self.assertEqual(P._lookup(now=100.0)[0], OWNER)
        with open(path, "w") as f:
            json.dump({"owner_uid": str(ADMIN)}, f)
        os.utime(path, ns=(1, 1))  # a different mtime
        self.assertEqual(P._lookup(now=101.0)[0], OWNER)   # inside 2 s: cached
        self.assertEqual(P._lookup(now=102.5)[0], ADMIN)   # after 2 s: re-read

    def test_unchanged_file_is_not_reread(self):
        self.hosted("")
        self.write({"owner_uid": str(OWNER)})
        P._lookup(now=10.0)
        with mock.patch.object(P, "_read", side_effect=AssertionError("re-read")):
            self.assertEqual(P._lookup(now=20.0)[0], OWNER)

    def test_uid_never_logged(self):
        self.hosted("")
        self.write({"owner_uid": str(OWNER)})
        P.owner_uid()
        P.owner_uid()
        os.remove(os.path.join(self.dir, "waerhouse_runtime.json"))
        P._cache["checked"] = None
        P.owner_uid()
        self.assertEqual([f["state"] for _e, f in self.logged], ["ok", "missing"])
        self.assertNotIn(str(OWNER), repr(self.logged))

    def test_protected_ids_include_owner(self):
        self.hosted("")
        self.admins = {ADMIN}
        self.write({"owner_uid": str(OWNER)})
        self.assertEqual(P.protected_ids(), {ADMIN, OWNER})


class Commands(Base):
    MOVED = {
        "cogs.context_debug": ["context_overwrite", "context_clear", "bot_repair"],
        "cogs.debug_commands": ["context_optin_all", "flashything_nuke"],
        "cogs.forceoptin": ["forceoptin"],
        "cogs.charoverride": ["charoverride"],
    }
    KEPT = {
        "cogs.context_debug": ["context_show", "context_raw", "context_debuguser"],
        "cogs.debug_commands": ["ping", "flashything"],
    }

    def commands_of(self, module):
        import importlib

        mod = importlib.import_module(module)
        found = {}
        for _name, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, commands.Cog):
                for value in vars(cls).values():
                    if isinstance(value, commands.Command):
                        found[value.name] = value
        return found

    def check_ok(self, cmd, uid, bot):
        ctx = SimpleNamespace(bot=bot, author=user(uid))
        try:
            return all(run(c(ctx)) for c in cmd.checks)
        except commands.CheckFailure:
            return False

    def test_moved_commands_are_owner_only_when_hosted(self):
        self.hosted("")
        self.write({"owner_uid": str(OWNER)})
        bot = SimpleNamespace()
        for module, names in self.MOVED.items():
            found = self.commands_of(module)
            for name in names:
                with self.subTest(command=name):
                    self.assertTrue(self.check_ok(found[name], OWNER, bot))
                    self.assertFalse(self.check_ok(found[name], ADMIN, bot))
                    self.assertFalse(self.check_ok(found[name], OTHER, bot))

    def test_moved_commands_follow_admin_when_standalone(self):
        bot = SimpleNamespace()
        for module, names in self.MOVED.items():
            found = self.commands_of(module)
            for name in names:
                with self.subTest(command=name):
                    self.assertTrue(self.check_ok(found[name], ADMIN, bot))
                    self.assertFalse(self.check_ok(found[name], OTHER, bot))

    def test_refusal_is_a_not_owner_error(self):
        self.hosted("")
        bot = SimpleNamespace()
        cmd = self.commands_of("cogs.forceoptin")["forceoptin"]
        ctx = SimpleNamespace(bot=bot, author=user(ADMIN))
        with self.assertRaises(P.NotOwnerTier):
            run(cmd.checks[0](ctx))
        self.assertTrue(issubclass(P.NotOwnerTier, commands.NotOwner))

    def test_kept_commands_still_use_admin_is_owner(self):
        for module, names in self.KEPT.items():
            src = inspect.getsource(__import__(module, fromlist=["x"]))
            for name in names:
                with self.subTest(command=name):
                    self.assertIn(f'@commands.command(name="{name}")\n    @commands.is_owner()', src)


class Unchanged(unittest.TestCase):
    def test_eche_is_owner_stays_admin_and_default_is_standalone(self):
        from core.bot import Eche

        self.assertIsNone(Eche.owner_tier_source)
        src = inspect.getsource(Eche.is_owner)
        self.assertIn("owner_decision", src)
        self.assertNotIn("permissions", src)

    def test_frozen_signatures(self):
        from core.builder import build_prompt
        from core.client import call_groq_turn

        self.assertIn("prompt", inspect.signature(call_groq_turn).parameters)
        self.assertTrue(inspect.iscoroutinefunction(build_prompt))

    def test_declared_minor_ban_untouched(self):
        from core import tools_admin

        src = inspect.getsource(tools_admin.ban_declared_minor)
        self.assertNotIn("permissions", src)
        self.assertIn("configured_owner_ids", src)


if __name__ == "__main__":
    unittest.main()
