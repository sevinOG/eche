"""Live 2026-11-19 00:19/00:24 ET: GLM added a bare thread call next to an
asset call and an unrequested thread opened. Mock-only."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from core import tools_thread as T


def _msg(text, replied=None):
    ref = SimpleNamespace(resolved=SimpleNamespace(content=replied)) if replied is not None else None
    return SimpleNamespace(content=text, reference=ref, channel=SimpleNamespace(), author=SimpleNamespace(id=1))


class ThreadRequestedTests(unittest.TestCase):
    def test_asked(self):
        for text in ("make a thread about F1 engines", "open a thread on this", "thread it",
                     "can you start a thread about dolphins"):
            self.assertTrue(T.thread_requested(_msg(text)), text)

    def test_agrees_to_offer(self):
        self.assertTrue(T.thread_requested(_msg("yes", "Want me to open a thread on that?")))
        self.assertTrue(T.thread_requested(_msg("sure, go ahead", "I can make a thread for it")))

    def test_not_asked(self):
        for text, replied in (("Sure make it", "Want a new image?"), ("Why not", None),
                              ("multiple women driving cars", "Eche used *asset*!"),
                              ("don't make a thread", None), ("yes", None)):
            self.assertFalse(T.thread_requested(_msg(text, replied)), text)


class OpenThreadDeclinesTests(unittest.TestCase):
    def test_declines_before_any_work(self):
        ctx = SimpleNamespace(message=_msg("Sure make it", "Want a new image?"), bot=None)
        with mock.patch.object(T, "_name_and_plan", side_effect=AssertionError("ran")):
            result = asyncio.run(T.open_thread(ctx, {}))
        self.assertTrue(result.declined)
        self.assertEqual(result.status, T.THREAD_NOT_ASKED)

    def test_execute_reports_not_run(self):
        from core.tools import ToolContext, execute

        ctx = ToolContext(bot=SimpleNamespace(user=SimpleNamespace(id=9)), message=_msg("why not"))
        with mock.patch.object(T, "_name_and_plan", side_effect=AssertionError("ran")), \
             mock.patch("core.tools._audit_tool"):
            result = asyncio.run(execute("thread", {}, ctx))
        self.assertFalse(result.ran)
        self.assertTrue(result.declined)


class RetryOffersNeitherTests(unittest.TestCase):
    def test_gated_set(self):
        from cogs.events.on_message import DECLINE_GATED_TOOLS
        self.assertEqual(DECLINE_GATED_TOOLS, frozenset({"asset", "thread"}))


if __name__ == "__main__":
    unittest.main()
