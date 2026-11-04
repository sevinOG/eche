# tests/test_memory_access.py
# Offline checks for "Let users see their memory". No network, no Discord.
#
# Run from eche_source:  python -m unittest tests.test_memory_access -v

from __future__ import annotations

import asyncio
import itertools
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

import discord  # noqa: E402

from core import discord_store as ds  # noqa: E402
from core import memory_access as ma  # noqa: E402

_ids = itertools.count(700000000000000000)


def run(coro):
    return asyncio.run(coro)


def not_found():
    response = mock.Mock(status=404, reason="Not Found")
    return discord.NotFound(response, "Unknown Member")


class FakeMember:
    def __init__(self, user_id):
        self.id = user_id

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id


class FakeThread:
    def __init__(self, name, parent, private=False):
        self.id = next(_ids)
        self.name = name
        self.parent_id = parent.id
        self.archived = False
        self.guild = parent.guild
        self.type = discord.ChannelType.private_thread if private else discord.ChannelType.public_thread
        self.members = []
        self.added = []

    async def add_user(self, member):
        self.added.append(member.id)
        self.members.append(member)

    async def fetch_members(self):
        return list(self.members)

    async def edit(self, **_kw):
        raise AssertionError("thread must not be edited")


class FakeChannel:
    def __init__(self, name, guild):
        self.id = next(_ids)
        self.name = name
        self.guild = guild
        self.threads = []
        self.overwrites = {}
        self.calls = []

    def thread(self, name, private=False):
        thread = FakeThread(name, self, private)
        self.threads.append(thread)
        return thread

    def overwrites_for(self, target):
        return self.overwrites.get(target.id, discord.PermissionOverwrite())

    async def set_permissions(self, target, *, overwrite=None, reason=None):
        self.calls.append((target.id, overwrite, reason))
        self.overwrites[target.id] = overwrite

    async def archived_threads(self, limit=100):
        if False:
            yield None


class FakeCategory:
    def __init__(self, name):
        self.name = name
        self.text_channels = []


class FakeGuild:
    def __init__(self, member_ids=(), cached_ids=()):
        self.id = next(_ids)
        self.categories = [FakeCategory(ds.CATEGORY_NAME)]
        self.member_ids = set(member_ids)
        self.cached_ids = set(cached_ids)
        self.fetched = []

    def channel(self, name):
        channel = FakeChannel(name, self)
        self.categories[0].text_channels.append(channel)
        return channel

    def get_member(self, user_id):
        return FakeMember(user_id) if user_id in self.cached_ids else None

    async def fetch_member(self, user_id):
        self.fetched.append(user_id)
        if user_id in self.member_ids:
            return FakeMember(user_id)
        raise not_found()

    async def active_threads(self):
        return []


A, B, C, D = 111111111111111111, 222222222222222222, 333333333333333333, 444444444444444444


class MemoryAccessTests(unittest.TestCase):
    def setUp(self):
        ds.clear_caches()
        # A cached member, B fetched member, C not on the server.
        self.guild = FakeGuild(member_ids={A, B}, cached_ids={A})
        self.a = self.guild.channel(f"user-{A}")
        self.a_thread = self.a.thread(ds.THREAD_CONTEXT)
        self.b = self.guild.channel(f"user-{B}")
        self.b_thread = self.b.thread(ds.THREAD_CONTEXT, private=True)
        self.c = self.guild.channel(f"user-{C}")
        self.c.thread(ds.THREAD_CONTEXT)
        self.bot = self.guild.channel(ds.BOT_CHANNEL_NAME)
        self.bot.thread(ds.THREAD_EPISODIC)
        self.general = self.guild.channel("general")

    def unlock(self, **kw):
        return run(ma.unlock_memory(self.guild, log=lambda _t: None, **kw))

    def test_grants_read_only_to_each_owner(self):
        report = self.unlock()
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.channels, 3)
        self.assertEqual(sorted(report.granted), [A, B])
        self.assertEqual(report.not_in_server, [C])
        self.assertEqual(report.already, [])
        for channel, uid in ((self.a, A), (self.b, B)):
            self.assertEqual(len(channel.calls), 1)
            target, overwrite, reason = channel.calls[0]
            self.assertEqual(target, uid)
            self.assertEqual(reason, ma.REASON)
            self.assertIs(overwrite.view_channel, True)
            self.assertIs(overwrite.read_message_history, True)
            self.assertIs(overwrite.send_messages, False)
            self.assertIs(overwrite.send_messages_in_threads, False)
            self.assertIs(overwrite.add_reactions, False)
            self.assertIs(overwrite.create_public_threads, False)

    def test_only_own_channel_and_never_bot(self):
        self.unlock()
        self.assertEqual([c[0] for c in self.a.calls], [A])
        self.assertEqual([c[0] for c in self.b.calls], [B])
        self.assertEqual(self.c.calls, [])
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(self.general.calls, [])

    def test_private_thread_gets_owner_added(self):
        report = self.unlock()
        self.assertEqual(self.b_thread.added, [B])
        self.assertEqual(self.a_thread.added, [])  # public: follows the channel
        self.assertEqual(report.thread_added, [B])

    def test_dry_run_changes_nothing(self):
        report = self.unlock(dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(sorted(report.granted), [A, B])
        self.assertEqual(report.thread_added, [B])
        self.assertEqual(report.not_in_server, [C])
        for channel in (self.a, self.b, self.c, self.bot):
            self.assertEqual(channel.calls, [])
            self.assertEqual(channel.overwrites, {})
        self.assertEqual(self.b_thread.added, [])
        self.assertIn("dry run", report.summary())
        self.assertIn("would grant", report.summary())

    def test_second_run_reports_already_had(self):
        self.unlock()
        again = self.unlock()
        self.assertTrue(again.ok)
        self.assertEqual(again.granted, [])
        self.assertEqual(sorted(again.already), [A, B])
        self.assertEqual(again.thread_added, [])
        self.assertEqual(len(self.a.calls), 1)
        self.assertEqual(len(self.b_thread.added), 1)

    def test_keeps_hand_set_values(self):
        self.a.overwrites[A] = discord.PermissionOverwrite(attach_files=True, send_messages=True)
        self.unlock()
        overwrite = self.a.overwrites[A]
        self.assertIs(overwrite.attach_files, True)
        self.assertIs(overwrite.send_messages, False)

    def test_member_cache_used_before_fetch(self):
        self.unlock()
        self.assertNotIn(A, self.guild.fetched)
        self.assertIn(B, self.guild.fetched)

    def test_failures_are_counted_not_raised(self):
        async def boom(*_a, **_kw):
            raise discord.Forbidden(mock.Mock(status=403, reason="Forbidden"), "Missing Permissions")

        self.a.set_permissions = boom
        report = self.unlock()
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failed), 1)
        self.assertIn(f"user-{A}", report.failed[0])
        self.assertEqual(report.granted, [B])

    def test_member_lookup_error_is_a_failure(self):
        async def broken(_uid):
            raise RuntimeError("rate limited")

        self.guild.fetch_member = broken
        report = self.unlock()
        self.assertEqual(report.granted, [A])
        self.assertEqual(report.not_in_server, [])
        self.assertEqual(len(report.failed), 2)

    def test_missing_thread_is_listed(self):
        d = self.guild.channel(f"user-{D}")
        self.guild.member_ids.add(D)
        report = self.unlock()
        self.assertIn(D, report.granted)
        self.assertEqual(report.no_thread, [D])
        self.assertEqual(len(d.calls), 1)

    def test_no_guild_or_category(self):
        report = run(ma.unlock_memory(None, log=lambda _t: None))
        self.assertIn("home server", report.problem)
        self.guild.categories = []
        report = self.unlock()
        self.assertIn("bot memory", report.problem)

    def test_report_lines_list_ids(self):
        logged = []
        report = run(ma.unlock_memory(self.guild, log=logged.append))
        text = "\n".join(logged)
        self.assertIn(f"Not in server (1): {C}", text)
        self.assertIn("Granted (2):", text)
        self.assertIn("Already had (0): -", text)
        self.assertEqual(logged[-1], report.summary())

    def test_has_read_only(self):
        self.assertFalse(ma.has_read_only(None))
        self.assertFalse(ma.has_read_only(discord.PermissionOverwrite(view_channel=True)))
        self.assertTrue(ma.has_read_only(ma.read_only_overwrite()))


class MemoryAccessCliTests(unittest.TestCase):
    def test_needs_yes_or_dry_run(self):
        with mock.patch.object(ma, "run_unlock") as runner:
            self.assertEqual(ma.main([]), 2)
            runner.assert_not_called()

    def test_dry_run_flag_passes_through(self):
        with mock.patch.object(ma, "run_unlock", return_value=ma.AccessReport(dry_run=True)) as runner:
            self.assertEqual(ma.main(["--dry-run"]), 0)
            runner.assert_called_once_with(dry_run=True)

    def test_yes_runs_for_real(self):
        with mock.patch.object(ma, "run_unlock", return_value=ma.AccessReport()) as runner:
            self.assertEqual(ma.main(["--yes"]), 0)
            runner.assert_called_once_with(dry_run=False)

    def test_problem_exits_nonzero(self):
        report = ma.AccessReport(problem="Discord token missing. Set it in Settings.")
        with mock.patch.object(ma, "run_unlock", return_value=report):
            self.assertEqual(ma.main(["--yes"]), 1)

    def test_missing_token_never_connects(self):
        with mock.patch.dict(os.environ, {"DISCORD_TOKEN": "", "HOME_SERVER_ID": ""}), \
                mock.patch("discord.Client") as client:
            report = ma.run_unlock(token="", home_raw="123", dry_run=True)
            self.assertIn("token", report.problem.lower())
            client.assert_not_called()

    def test_exe_flag_routes_here(self):
        import eche_app

        with mock.patch.object(sys, "argv", ["Eche.exe", "--unlock-memory", "--dry-run"]), \
                mock.patch.object(eche_app, "_bootstrap"), \
                mock.patch.object(ma, "main", return_value=0) as entry:
            with self.assertRaises(SystemExit) as stop:
                eche_app.main()
            self.assertEqual(stop.exception.code, 0)
            entry.assert_called_once_with(["--dry-run"])


class BridgeTests(unittest.TestCase):
    def test_unlock_memory_line_runs_in_bot(self):
        from core import gui_bridge

        seen = {}

        async def fake_unlock(guild, *, dry_run=False, log=None):
            seen["guild"] = guild
            return ma.AccessReport()

        with mock.patch.object(ma, "unlock_memory", fake_unlock), \
                mock.patch("core.context_manager.get_home_guild", return_value="G"), \
                mock.patch.object(gui_bridge, "emit") as emit:
            run(gui_bridge._handle_line(object(), "UNLOCK_MEMORY"))
        self.assertEqual(seen["guild"], "G")
        self.assertEqual(emit.call_args[0][0], "memory_access")


if __name__ == "__main__":
    unittest.main()
