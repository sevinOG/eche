# tests/test_blocklist.py
# Offline checks for the minors hard block (core/blocklist.py) on chat input,
# chat output, tool calls, ?asset and image/video prompts, and thread posts,
# plus the Blocked words setting and the memory slur-masking toggle.
#
# Run from eche_source:  python -m unittest tests.test_blocklist -v

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(HERE)
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from core import blocklist as bl  # noqa: E402
from core import content_filter as cf  # noqa: E402
from core import context_summarizer as cs  # noqa: E402
from core import episodic  # noqa: E402
from core import imagine  # noqa: E402
from core import summarizer_prompt as sp  # noqa: E402
from core.client import ModelTurn as _ModelTurn  # noqa: E402


def ModelTurn(reply="", tool_calls=None, **kw):
    kw.setdefault("thoughts", "")
    return _ModelTurn(reply=reply, tool_calls=tool_calls or [], **kw)

SLUR = cf._ANYWHERE[0]


def run(coro):
    return asyncio.run(coro)


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eche-block-")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ECHE_")}
        env["ECHE_USER_ROOT"] = self.root
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        bl._extra_cache = ("", -1.0, None)
        cf._mask_cache = ("", -1.0, True)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.root, ignore_errors=True)
        bl._extra_cache = ("", -1.0, None)
        cf._mask_cache = ("", -1.0, True)

    def save(self, **values):
        from core.secrets import settings_path

        path = settings_path(self.root)
        data = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        data.update(values)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        # Force a fresh read even inside one mtime tick.
        bl._extra_cache = ("", -1.0, None)
        cf._mask_cache = ("", -1.0, True)


# ------------------------------------------------------------- the word list


class MatchTests(TempRoot):
    BLOCKED = [
        "my kid", "the kids", "hey kiddo", "Children", "childs", "a minor", "minors",
        "underage", "under-age", "preteen", "pre-teen", "toddler", "infant",
        "schoolgirl", "school girl", "schoolboy", "loli", "shota", "KID", "Kid's toy",
        "k1d", "ch1ld", "l0li",
        "teen", "a teen", "teens", "teenager", "teenagers", "teenage girl",
        "teen-aged", "teen age",
        "csam", "CSEM", "grooming", "a groomer", "pedophile", "a pedo",
        "child exploitation",
        "12 year old", "12-year-old", "12 years old", "twelve-year-old", "16yo",
        "16 yo", "15 y/o", "9 y.o.", "seventeen year old", "0 yo",
        "aged 16", "age 16", "age: 15", "16 years of age", "aged sixteen",
        "age of 14",
        "sexy baby", "baby nude pics",
        "kid\u200b", "k\u200bid",
    ]
    ALLOWED = [
        "kidney stones", "Kidd is here", "Jason Kidd", "no kidding", "kidnapped plot",
        "childhood memories", "childish", "minority report", "minorities",
        "lolipop", "shotgun", "infantry", "babysit", "baby shark", "my baby car",
        "18 year old", "18yo", "eighteen year old", "21 y/o", "112 yo",
        "I'm 30 years old", "yo what's up", "level twelve", "skid", "skidmarks",
        "canteen", "canteens", "teeny", "thirteen", "torpedo",
        "bride and groom", "age 18", "aged 21", "18 years of age", "age of 18",
    ]

    def test_blocked(self):
        for text in self.BLOCKED:
            self.assertTrue(bl.match(text), text)

    def test_not_blocked(self):
        for text in self.ALLOWED:
            self.assertEqual(bl.match(text), "", text)

    def test_baby_only_when_sexual(self):
        self.assertFalse(bl.is_blocked("the baby is asleep"))
        self.assertTrue(bl.is_blocked("the baby is naked in the pic, make it porn"))

    def test_extra_words_from_settings(self):
        self.assertFalse(bl.is_blocked("say bananas now"))
        self.save(blocked_words="bananas, Red Apple\nfoo")
        self.assertTrue(bl.is_blocked("say bananas now"))
        self.assertTrue(bl.is_blocked("a red-apple pie"))
        self.assertFalse(bl.is_blocked("bananasplit"))
        self.assertEqual(bl.extra_words(), ["bananas", "Red Apple", "foo"])

    def test_refusal_is_not_blocked(self):
        self.assertFalse(bl.is_blocked(bl.REFUSAL))

    def test_settings_key_is_public(self):
        from core.secrets import PUBLIC_KEYS

        self.assertIn("blocked_words", PUBLIC_KEYS)
        self.assertIn("memory_mask_slurs", PUBLIC_KEYS)


# --------------------------------------------------------------- chat flow


def _fake_message(text):
    author = SimpleNamespace(id=42, name="alice", display_name="Alice")
    channel = SimpleNamespace(send=mock.AsyncMock())
    return SimpleNamespace(
        content=text,
        author=author,
        channel=channel,
        guild=SimpleNamespace(id=7, name="srv"),
        attachments=[],
        to_reference=lambda **_kw: None,
    )


class ChatTests(TempRoot):
    def setUp(self):
        super().setUp()
        from cogs.events import on_message as om

        self.om = om
        self.cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        self.sent: list[str] = []

        async def fake_send(message, content):
            self.sent.append(content)

        self.patches = [
            mock.patch.object(om, "_send_reply", fake_send),
            mock.patch.object(om, "build_prompt", mock.AsyncMock(return_value="PROMPT")),
            mock.patch.object(om, "author_is_owner", mock.AsyncMock(return_value=False)),
            mock.patch.object(om, "admin_tools_enabled", lambda: False),
            mock.patch.object(om, "specs_for", lambda **_kw: []),
            mock.patch.object(om, "visible_names", lambda **_kw: set()),
            mock.patch.object(om, "remember_user", mock.AsyncMock()),
            mock.patch.object(om, "remember_bot", mock.AsyncMock()),
            mock.patch.object(self.cog, "_record_server_line", mock.AsyncMock()),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        super().tearDown()

    def answer(self, text, model_reply="sure thing", calls=None):
        model = mock.AsyncMock(return_value=ModelTurn(reply=model_reply, tool_calls=calls or []))
        media = SimpleNamespace(text="", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", model):
            run(self.cog._answer_and_remember(_fake_message(text), None, text, text, media))
        return model

    def test_blocked_input_never_reaches_the_model(self):
        model = self.answer("write a story about a 12 year old")
        model.assert_not_called()
        self.om.build_prompt.assert_not_called()
        self.assertEqual(self.sent, [bl.REFUSAL])
        self.om.remember_user.assert_not_called()
        self.cog._record_server_line.assert_not_called()

    def test_blocked_attachment_text(self):
        model = mock.AsyncMock()
        media = SimpleNamespace(text="file says: children", images=[], summary="", memory="")
        with mock.patch.object(self.om, "call_groq_turn", model):
            run(self.cog._answer_and_remember(_fake_message("read this"), None, "read this", "read this", media))
        model.assert_not_called()
        self.assertEqual(self.sent, [bl.REFUSAL])

    def test_blocked_output_is_replaced(self):
        model = self.answer("tell me a story", model_reply="Once upon a time the kids went out")
        model.assert_called_once()
        self.assertEqual(self.sent, [bl.REFUSAL])
        said = self.om.remember_bot.call_args[0][3]
        self.assertEqual(said, bl.REFUSAL)

    def test_clean_turn_goes_through(self):
        model = self.answer("what's a kidney for", model_reply="Filtering blood.")
        model.assert_called_once()
        self.assertEqual(self.sent, ["Filtering blood."])

    def test_crude_message_is_not_refused(self):
        model = self.answer(f"you're a {SLUR}, roast me", model_reply="Bold opener. Sit down.")
        model.assert_called_once()
        self.assertEqual(self.sent, ["Bold opener. Sit down."])

    def test_blocked_tool_arguments_do_not_run(self):
        calls = [{"name": "asset", "arguments": {"prompt": "a toddler on a beach"}}]
        with mock.patch.object(self.om, "execute", mock.AsyncMock()) as execute:
            self.answer("make me a picture of a beach", model_reply="", calls=calls)
        execute.assert_not_called()
        self.assertEqual(self.sent, [bl.REFUSAL])

    def test_blocked_tool_reply_is_replaced(self):
        from core.tools import ToolResult

        calls = [{"name": "duckduckgo", "arguments": {"query": "best parks"}}]
        result = ToolResult(text="Parks for children and toddlers.", ran=True, name="duckduckgo", fence=False)
        with mock.patch.object(self.om, "execute", mock.AsyncMock(return_value=result)):
            self.answer("best parks?", model_reply="", calls=calls)
        joined = "\n".join(self.sent)
        self.assertIn(bl.REFUSAL, joined)
        self.assertNotIn("children", joined)

    def test_episode_skips_blocked_lines(self):
        from cogs.events import on_message as om

        cog = om.OnMessage(SimpleNamespace(user=SimpleNamespace(id=1)))
        with mock.patch("core.episodic.append_episodic", mock.AsyncMock()) as append:
            run(cog._record_server_line(_fake_message("x"), "look at that kid"))
            append.assert_not_called()
            run(cog._record_server_line(_fake_message("x"), "look at that kidney"))
            append.assert_called_once()


class DesktopChatTests(TempRoot):
    def test_local_chat_blocks_both_ways(self):
        from core import client, local_chat

        with mock.patch.object(client, "call_groq", mock.AsyncMock(return_value="fine")) as model:
            self.assertEqual(run(local_chat.reply_local("my kids say hi")), bl.REFUSAL)
            model.assert_not_called()
        with mock.patch.object(client, "call_groq", mock.AsyncMock(return_value="the children laughed")):
            self.assertEqual(run(local_chat.reply_local("tell me a joke")), bl.REFUSAL)


# ------------------------------------------------------- picture and video


class AssetTests(TempRoot):
    def test_plan_refuses_minors(self):
        for prompt in ("image of a kid on a bike", "video of a 10 year old", "a loli anime girl"):
            with self.assertRaises(imagine.ImagineError) as caught:
                imagine.plan_asset(prompt)
            self.assertEqual(caught.exception.text, bl.REFUSAL)

    def test_run_refuses_before_any_request(self):
        plan = imagine.AssetPlan(mode="image", prompt="a schoolgirl portrait")
        with mock.patch.object(imagine, "http_json") as http:
            with self.assertRaises(imagine.ImagineError):
                run(imagine.run_asset(plan, "key"))
            http.assert_not_called()

    def test_slur_and_crude_prompts_pass(self):
        plan = imagine.plan_asset(f"a protest sign that says {SLUR}")
        self.assertIn(SLUR, plan.prompt)
        imagine.plan_asset("image of a kidney bean")

    def test_extra_word_blocks_asset(self):
        self.save(blocked_words="clown")
        with self.assertRaises(imagine.ImagineError):
            imagine.plan_asset("image of a clown")

    def test_asset_tool_refuses_before_fetch_and_api(self):
        from core import tools_asset

        ctx = SimpleNamespace(message=SimpleNamespace(content="", author=SimpleNamespace(id=1)), bot=None)
        with mock.patch.object(tools_asset, "_allowed", mock.AsyncMock(return_value=(True, ""))), \
                mock.patch.object(tools_asset, "source_from_message", mock.AsyncMock()) as fetch, \
                mock.patch.object(imagine, "http_json") as http:
            result = run(tools_asset.make_asset(ctx, {"prompt": "two children playing"}))
        self.assertEqual(result.text, bl.REFUSAL)
        fetch.assert_not_called()
        http.assert_not_called()

    def test_asset_command_refuses_before_fetch(self):
        from cogs import asset as asset_cog

        cog = asset_cog.Asset(SimpleNamespace()) if hasattr(asset_cog, "Asset") else None
        if cog is None:
            self.skipTest("asset cog class name changed")
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), message=SimpleNamespace(), reply=mock.AsyncMock())
        with mock.patch.object(asset_cog, "speaker_allowed", lambda _o: True), \
                mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=True)), \
                mock.patch.object(asset_cog, "source_from_message", mock.AsyncMock()) as fetch, \
                mock.patch.object(asset_cog, "run_asset", mock.AsyncMock()) as make:
            run(cog.asset.callback(cog, ctx, prompt="an infant in a field"))
        fetch.assert_not_called()
        make.assert_not_called()
        self.assertEqual(ctx.reply.call_args[0][0], bl.REFUSAL)

    def test_asset_command_refuses_teen_prompt(self):
        """?asset is the command people can type to skip chat; it must refuse too."""
        from cogs import asset as asset_cog

        cog = asset_cog.Asset(SimpleNamespace()) if hasattr(asset_cog, "Asset") else None
        if cog is None:
            self.skipTest("asset cog class name changed")
        ctx = SimpleNamespace(author=SimpleNamespace(id=1), message=SimpleNamespace(), reply=mock.AsyncMock())
        with mock.patch.object(asset_cog, "speaker_allowed", lambda _o: True), \
                mock.patch("core.admin_tools.author_is_owner", mock.AsyncMock(return_value=True)), \
                mock.patch.object(asset_cog, "source_from_message", mock.AsyncMock()) as fetch, \
                mock.patch.object(asset_cog, "run_asset", mock.AsyncMock()) as make, \
                mock.patch.object(imagine, "http_json") as http:
            run(cog.asset.callback(cog, ctx, prompt="a teenager twerking"))
        fetch.assert_not_called()
        make.assert_not_called()
        http.assert_not_called()
        self.assertEqual(ctx.reply.call_args[0][0], bl.REFUSAL)

    def test_request_for_refuses_blocked_prompt(self):
        plan = imagine.AssetPlan(mode="image", prompt="a teenager at the beach")
        with self.assertRaises(imagine.ImagineError) as caught:
            imagine.request_for(plan)
        self.assertEqual(caught.exception.text, bl.REFUSAL)

    def test_http_json_refuses_blocked_body_prompt(self):
        with mock.patch.object(imagine.urllib.request, "urlopen") as opener:
            with self.assertRaises(imagine.ImagineError) as caught:
                imagine.http_json(
                    "POST",
                    "https://api.x.ai/v1/images/generations",
                    "key",
                    {"model": "x", "prompt": "csam drawing"},
                )
            opener.assert_not_called()
        self.assertEqual(caught.exception.text, bl.REFUSAL)

    def test_rewritten_prompt_and_caption_are_refused(self):
        payload = {
            "data": [{
                "url": "https://api.x.ai/v1/files/ok",
                "revised_prompt": "a 15 year old on the beach",
            }]
        }
        with self.assertRaises(imagine.ImagineError) as caught:
            imagine.image_url(payload)
        self.assertEqual(caught.exception.text, bl.REFUSAL)
        video = {"status": "done", "video": {"url": "https://api.x.ai/v1/files/ok", "caption": "teens dancing"}}
        with self.assertRaises(imagine.ImagineError) as caught:
            imagine.video_poll(video)
        self.assertEqual(caught.exception.text, bl.REFUSAL)

    def test_clean_caption_still_returns_the_file(self):
        url = imagine.image_url({"data": [{"url": "https://api.x.ai/v1/files/ok", "revised_prompt": "a red boat"}]})
        self.assertEqual(url, "https://api.x.ai/v1/files/ok")

    def test_adult_prompts_still_pass(self):
        imagine.plan_asset("image of an 18 year old on a beach")
        imagine.plan_asset("sexy adult woman, nsfw")
        imagine.request_for(imagine.AssetPlan(mode="image", prompt="a kidney-shaped pool"))


# ------------------------------------------------------------------ threads


class FakeThread:
    def __init__(self):
        self.posts: list[str] = []

    async def send(self, content=None, **_kw):
        self.posts.append(content)


class ThreadTests(TempRoot):
    def test_blocked_post_is_not_sent(self):
        from core import client, tools_thread

        replies = iter([
            ModelTurn(reply="First post about parks."),
            ModelTurn(reply="Second post: the kids ran around."),
            ModelTurn(reply="Third post."),
        ])
        thread = FakeThread()
        with mock.patch.object(client, "call_groq_turn", mock.AsyncMock(side_effect=lambda *a, **k: next(replies))):
            body = run(tools_thread._execute(SimpleNamespace(), thread, "plan", [], "parks"))
        self.assertEqual(thread.posts[0], "First post about parks.")
        self.assertEqual(thread.posts[-1], bl.REFUSAL)
        self.assertFalse(any("kids" in (post or "") for post in thread.posts))
        self.assertNotIn("Third post.", thread.posts)
        self.assertNotIn("kids", body)

    def test_blocked_request_never_plans(self):
        from core import tools_thread

        message = SimpleNamespace(content="open a thread about raising children", channel=None)
        ctx = SimpleNamespace(message=message, bot=None)
        with mock.patch.object(tools_thread, "_name_and_plan", mock.AsyncMock()) as planner, \
                mock.patch.object(tools_thread, "_create_thread", mock.AsyncMock()) as opener:
            result = run(tools_thread.open_thread(ctx, {}))
        planner.assert_not_called()
        opener.assert_not_called()
        self.assertEqual(result.text, bl.REFUSAL)

    def test_blocked_plan_never_opens(self):
        from core import tools_thread

        message = SimpleNamespace(content="open a thread about school trips", channel=None)
        ctx = SimpleNamespace(message=message, bot=None)
        plan = mock.AsyncMock(return_value=("Trips", "- what toddlers pack", []))
        with mock.patch.object(tools_thread, "_name_and_plan", plan), \
                mock.patch.object(tools_thread, "previous_topic", mock.AsyncMock(return_value="")), \
                mock.patch.object(tools_thread, "_create_thread", mock.AsyncMock()) as opener:
            result = run(tools_thread.open_thread(ctx, {}))
        opener.assert_not_called()
        self.assertEqual(result.text, bl.REFUSAL)


# ------------------------------------------------------- memory slur toggle


class MaskToggleTests(TempRoot):
    def test_default_on(self):
        self.assertTrue(cf.masking_enabled())
        out = cf.clean_memory_text(f"said {SLUR} to <@123456789012345678>")
        self.assertFalse(cf.has_slur(out))
        self.assertNotIn("<@", out)

    def test_off_keeps_words_but_strips_ids(self):
        self.save(memory_mask_slurs="0")
        self.assertFalse(cf.masking_enabled())
        out = cf.clean_memory_text(f"said {SLUR} to <@123456789012345678> 123456789012345678")
        self.assertIn(SLUR, out)
        self.assertNotIn("<@", out)
        self.assertNotRegex(out, r"\\d{17,20}")

    def test_drop_rule_follows_toggle(self):
        on = [
            sp.build_merge_prompt("- Old.", "user: hi", char_limit=500),
            sp.build_condense_prompt("- Old.", 500),
            episodic.fold_prompt("old", ["a: b"], 500),
            episodic.condense_prompt("a", "b", 500),
            cs.condense_memory_prompt("user", "a", "b", 500),
        ]
        for text in on:
            self.assertIn("No slurs", text)
            self.assertIn("@mentions", text)
        self.save(memory_mask_slurs="0")
        off = [
            sp.build_merge_prompt("- Old.", "user: hi", char_limit=500),
            sp.build_condense_prompt("- Old.", 500),
            episodic.fold_prompt("old", ["a: b"], 500),
            episodic.condense_prompt("a", "b", 500),
            cs.condense_memory_prompt("user", "a", "b", 500),
        ]
        for text in off:
            self.assertNotIn("slurs", text.lower())
            self.assertIn("@mentions", text)

    def test_toggle_back_on(self):
        self.save(memory_mask_slurs="0")
        self.assertFalse(cf.masking_enabled())
        self.save(memory_mask_slurs="1")
        self.assertTrue(cf.masking_enabled())


if __name__ == "__main__":
    unittest.main()
