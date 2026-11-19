"""18+ only: minors rule in the chat footer, and banning self-declared minors.
Owner request 2026-11-18 21:42 ET. Mock-only."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from core import minor_claims as mc
from core import safety as sf


def run(c):
    return asyncio.run(c)


class FooterTests(unittest.TestCase):
    def test_footer_constant_and_sha_unchanged(self):
        self.assertEqual(
            sf.footer_sha256(),
            "dbb7d6ac3c29129fde736329e121f46d64827bf842f544c8c8fed7f60c669ec5",
        )

    def test_minors_rule_only_when_18_plus(self):
        minors = dict(sf.SAFETY_CATEGORIES)["minors"]
        with mock.patch("core.secrets.load_all", return_value={"asset_adult_enabled": "1"}):
            msgs = sf.prepare_llm_messages([{"role": "user", "content": "hi"}])
            self.assertIn(minors, msgs[0]["content"])
            self.assertEqual(msgs[0]["content"], sf.SAFETY_FOOTER)
        with mock.patch("core.secrets.load_all", return_value={"asset_adult_enabled": "0"}):
            msgs = sf.prepare_llm_messages([{"role": "user", "content": "hi"}])
            self.assertNotIn("minor", msgs[0]["content"].lower())
            self.assertNotIn("minor", msgs[-1]["content"].lower())
            self.assertIn("No sexual content involving non-consent.", msgs[0]["content"])

    def test_read_error_keeps_rule(self):
        with mock.patch("core.secrets.load_all", side_effect=OSError("x")):
            self.assertEqual(sf.chat_footer(), sf.SAFETY_FOOTER)

    def test_both_footers_recognised(self):
        for text in (sf.SAFETY_FOOTER, sf.CHAT_FOOTER_NO_MINORS):
            self.assertTrue(sf.is_footer_message({"role": "system", "content": text}))

    def test_asset_prompt_footer_still_full(self):
        from core.asset_safety import build_sent_prompt

        with mock.patch("core.secrets.load_all", return_value={"asset_adult_enabled": "0"}):
            sent, _ = build_sent_prompt("a red boat")
        self.assertTrue(sent.endswith(sf.SAFETY_FOOTER))


class DetectorTests(unittest.TestCase):
    def test_claims(self):
        for text in ("im 15", "I am 16 years old", "i'm a minor lol", "my age is 14",
                     "i just turned 17", "im 15 yo", "im only 12", "I'm underage"):
            self.assertTrue(mc.says_minor(text), text)

    def test_not_claims(self):
        for text in ("I'm 15 minutes away", "im not a minor", "I'm 25", "i'm 30 years old",
                     "i am 18", "im 2 drinks in", '"im 15" is what she said', "im just a kid at heart",
                     "my son is 12", "the rule about minors", ""):
            self.assertFalse(mc.says_minor(text), text)


def _msg(content="im 15", *, author_id=50, top=1, me_top=5, perms=True):
    guild = SimpleNamespace(
        id=8, owner_id=1, ban=mock.AsyncMock(),
        me=SimpleNamespace(id=99, top_role=me_top, guild_permissions=SimpleNamespace(ban_members=perms)),
    )
    channel = SimpleNamespace(id=5, send=mock.AsyncMock())
    author = SimpleNamespace(id=author_id, bot=False, top_role=top, name="u")
    return SimpleNamespace(content=content, guild=guild, channel=channel, author=author)


class BanTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        p = mock.patch("core.audit.record", side_effect=lambda ev, **kw: self.rows.append((ev, kw)))
        p.start()
        self.addCleanup(p.stop)
        self.bot = SimpleNamespace(user=SimpleNamespace(id=99), is_owner=mock.AsyncMock(return_value=False))

    def _on(self, value="1"):
        return mock.patch("core.secrets.load_all", return_value={"asset_adult_enabled": value})

    def test_bans_with_audit_and_neutral_line(self):
        m = _msg()
        with self._on(), mock.patch("core.admin_tools.configured_owner_ids", return_value=[]):
            self.assertTrue(run(mc.enforce(self.bot, m)))
        m.guild.ban.assert_awaited_once()
        outcomes = [kw["outcome"] for ev, kw in self.rows if ev == "moderation"]
        self.assertEqual(outcomes, ["attempt", "success"])
        row = [kw for ev, kw in self.rows if ev == "moderation"][0]
        self.assertEqual(row["category"], "self-declared-minor")
        self.assertTrue(row["safety"])
        self.assertNotIn("im 15", repr(row))
        sent = m.channel.send.await_args.args[0]
        self.assertEqual(sent, mc.NEUTRAL_LINE)
        self.assertNotIn("minor", sent.lower())

    def test_off_when_18_plus_disabled(self):
        m = _msg()
        with self._on("0"):
            self.assertFalse(run(mc.enforce(self.bot, m)))
        m.guild.ban.assert_not_awaited()
        self.assertEqual(self.rows, [])

    def test_never_bans_owner(self):
        m = _msg(author_id=7)
        with self._on(), mock.patch("core.admin_tools.configured_owner_ids", return_value=[7]):
            self.assertFalse(run(mc.enforce(self.bot, m)))
        m.guild.ban.assert_not_awaited()
        self.bot.is_owner = mock.AsyncMock(return_value=True)
        with self._on(), mock.patch("core.admin_tools.configured_owner_ids", return_value=[]):
            self.assertFalse(run(mc.enforce(self.bot, _msg(author_id=8))))
        self.bot.is_owner = mock.AsyncMock(side_effect=RuntimeError("x"))
        m2 = _msg(author_id=9)
        with self._on(), mock.patch("core.admin_tools.configured_owner_ids", return_value=[]):
            self.assertFalse(run(mc.enforce(self.bot, m2)))
        m2.guild.ban.assert_not_awaited()

    def test_permission_and_role_checks(self):
        for m in (_msg(perms=False), _msg(top=9, me_top=5), _msg(author_id=1)):
            with self._on(), mock.patch("core.admin_tools.configured_owner_ids", return_value=[]):
                self.assertFalse(run(mc.enforce(self.bot, m)))
            m.guild.ban.assert_not_awaited()

    def test_failed_audit_write_means_no_ban(self):
        m = _msg()
        with self._on(), mock.patch("core.admin_tools.configured_owner_ids", return_value=[]), \
             mock.patch("core.audit.record", side_effect=OSError("disk")):
            self.assertFalse(run(mc.enforce(self.bot, m)))
        m.guild.ban.assert_not_awaited()

    def test_no_claim_no_action(self):
        m = _msg("I'm 25 and tired")
        with self._on():
            self.assertFalse(run(mc.enforce(self.bot, m)))
        m.guild.ban.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
