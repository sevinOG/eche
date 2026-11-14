# Llama Guard removed everywhere (2026-11-14). The prompt child check is a
# narrow YES/NO question: Grok on the xAI key first, the local qwen3-vl as
# backup, and any error, unclear reply, or both failing blocks. Every
# verdict is audited. Mock-only: no network, no image generation, and no
# child-like images anywhere.
from __future__ import annotations

import asyncio
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import classifier as C  # noqa: E402

XAI_URL = "https://api.x.ai/v1/chat/completions"

# Live prompts that Llama Guard falsely blocked (audit log, 2026-11-14 ET).
C17_0701 = "add a video of an anthromorphic c17 globemaster as a heavy set female"
C17_0708 = "an actual c17"
ADDERALL = "adderall jaso"
FALSE_POSITIVES = (C17_0701, C17_0708, ADDERALL)

# Explicit minor text (text only, never an image).
EXPLICIT_MINORS = (
    "a 12 year old girl at the beach",
    "a little boy in a kindergarten class",
    "a teenager in a high school uniform",
)


class _Resp(io.BytesIO):
    status = 200
    headers = {"x-request-id": "req-test"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _reply(text):
    # Both shapes: xAI (choices) and local Ollama (message).
    return _Resp(json.dumps({"choices": [{"message": {"content": text}}], "message": {"content": text}}).encode())


def _user_text(body):
    return "\n".join(str(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "user")


class Base(unittest.TestCase):
    def setUp(self):
        self._prev = C.tests_are_allowing()
        C.allow_in_tests(False)
        C.force_backend(None)
        self.audits = []
        self.requests = []
        self._patches = [
            mock.patch.object(C, "_xai_key", return_value="xai-test-key"),
            mock.patch.object(C, "_xai_vision_model", return_value="grok-test"),
            mock.patch("core.audit.api", side_effect=lambda service, **f: self.audits.append((service, f))),
            mock.patch.object(C, "settings_file_problem", return_value=""),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        C.force_backend(None)
        C.allow_in_tests(self._prev)

    def _opener(self, grok=None, local=None):
        """grok/local: a reply string, an Exception, or a callable(user_text)."""
        def opener(req, *a, **k):
            body = json.loads(req.data.decode())
            self.requests.append((req.full_url, body))
            which = grok if req.full_url.startswith("https://api.x.ai") else local
            if callable(which) and not isinstance(which, type):
                which = which(_user_text(body))
            if isinstance(which, BaseException):
                raise which
            if which is None:
                raise OSError("unexpected backend")
            return _reply(which)
        return opener

    def _check(self, text, **kw):
        with mock.patch("urllib.request.urlopen", side_effect=self._opener(**kw)):
            return asyncio.run(C.asset_prompt_check_async(text))

    def _child_audits(self):
        return [f for s, f in self.audits if s == "child-check" and "verdict" in f]


class FalsePositivesPass(Base):
    def test_live_false_positives_pass_on_grok_no(self):
        for text in FALSE_POSITIVES:
            self.requests.clear()
            self.assertEqual(self._check(text, grok="NO"), "", text)
            self.assertEqual(len(self.requests), 1, text)
            url, body = self.requests[0]
            self.assertEqual(url, XAI_URL)
            self.assertEqual(body["model"], "grok-test")
            self.assertEqual(body["messages"][0]["content"], C.CHILD_CHECK_PROMPT)
            self.assertIn(text.split()[0], _user_text(body))

    def test_prompt_is_narrow(self):
        p = C.CHILD_CHECK_PROMPT.lower()
        for must in ("only yes or no", "under 18", "childlike body", "school uniform",
                     "anthropomorphized", "vehicles", "aircraft", "adult women",
                     "busty", "heavyset", "drugs", "named fictional child characters"):
            self.assertIn(must, p, must)
        self.assertNotIn("llama", p)
        self.assertNotIn("s1:", p)

    def test_reply_parsing(self):
        for raw, want in (("YES", True), ("yes.", True), ("NO", False), ("No.", False), (" NO \n", False)):
            self.assertIs(C.parse_child_reply(raw), want, raw)
        for raw in ("", "YES\nNO", "Maybe", "NO, but", "safe", "unsafe\nS4", "I can't help"):
            self.assertIsNone(C.parse_child_reply(raw), raw)


class ExplicitMinorsBlock(Base):
    def test_explicit_minor_text_blocks_on_yes(self):
        for text in EXPLICIT_MINORS:
            self.assertEqual(self._check(text, grok="YES"), "child-check-unsafe", text)

    def test_local_yes_blocks_when_grok_is_down(self):
        self.assertEqual(self._check(EXPLICIT_MINORS[0], grok=OSError("down"), local="YES"), "child-check-unsafe")

    def test_explicit_minor_words_still_hit_the_media_filter_or_blocklist(self):
        from core.asset_filter import media_prompt_blocked
        from core import blocklist

        for text in ("a toddler in a garden", "a 12 year old girl", "a schoolgirl"):
            blocked = media_prompt_blocked(text, adult=False)
            try:
                blocked = blocked or bool(blocklist.is_blocked(text))
            except Exception:
                pass
            self.assertTrue(blocked, text)


class FailClosed(Base):
    def test_grok_error_falls_back_to_local(self):
        self.assertEqual(self._check(C17_0708, grok=OSError("down"), local="NO"), "")
        urls = [u for u, _ in self.requests]
        self.assertEqual(urls, [XAI_URL, C.OLLAMA_CHAT_URL])
        local_body = self.requests[1][1]
        self.assertEqual(local_body["model"], C.LOCAL_VISION_MODEL)
        self.assertIs(local_body["think"], False)

    def test_grok_unclear_falls_back_to_local(self):
        self.assertEqual(self._check(C17_0708, grok="I think so", local="NO"), "")
        self.assertEqual(len(self.requests), 2)

    def test_both_fail_blocks(self):
        self.assertEqual(self._check(C17_0708, grok=OSError("down"), local=OSError("down")), "child-check-error")

    def test_unparseable_from_both_blocks(self):
        self.assertEqual(self._check(C17_0708, grok="maybe", local="It depends."), "child-check-error")
        self.assertEqual(self._check(C17_0708, grok="maybe", local="YES\nNO"), "child-check-error")

    def test_no_xai_key_uses_local_only(self):
        with mock.patch.object(C, "_xai_key", return_value=""):
            self.assertEqual(self._check(ADDERALL, local="NO"), "")
            self.assertEqual([u for u, _ in self.requests], [C.OLLAMA_CHAT_URL])
            self.assertEqual(self._check(ADDERALL, local=OSError("refused")), "child-check-error")

    def test_deadline_blocks(self):
        def slow(_text):
            import time
            time.sleep(0.5)
            return "NO"

        with mock.patch.object(C, "_child_check_deadline", return_value=0.1):
            self.assertEqual(self._check(ADDERALL, grok=slow), "child-check-error")

    def test_unreadable_settings_block(self):
        with mock.patch.object(C, "settings_file_problem", return_value="settings.json unreadable"), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("no request")):
            self.assertEqual(asyncio.run(C.asset_prompt_check_async(ADDERALL)), "child-check-error")

    def test_install_path_in_argv_does_not_disable(self):
        with mock.patch.object(sys, "argv", [r"D:\pytest_old\eche\Eche.exe", "--bot"]):
            self.assertEqual(self._check(ADDERALL, grok=OSError("down"), local=OSError("down")), "child-check-error")


class EveryVerdictIsAudited(Base):
    def test_pass_block_and_error_are_each_audited(self):
        cases = (
            (dict(grok="NO"), "", "no", "pass", "child-check"),
            (dict(grok="YES"), "child-check-unsafe", "yes", "block", "child-check-unsafe"),
            (dict(grok=OSError("x"), local=OSError("y")), "child-check-error", "error", "block", "child-check-error"),
            (dict(grok="??", local="??"), "child-check-error", "error", "block", "child-check-error"),
        )
        for kw, want, verdict, outcome, category in cases:
            self.audits.clear()
            self.assertEqual(self._check("hello there", **kw), want, kw)
            rows = self._child_audits()
            self.assertEqual(len(rows), 1, (kw, rows))
            row = rows[0]
            self.assertEqual((row["verdict"], row["outcome"], row["category"]), (verdict, outcome, category))
            blob = json.dumps([f for _s, f in self.audits], default=str)
            self.assertNotIn("hello there", blob)
            self.assertNotIn("xai-test-key", blob)

    def test_deadline_is_audited(self):
        with mock.patch.object(C, "_child_check_deadline", return_value=0.05), \
             mock.patch.object(C, "_child_check_sync", side_effect=lambda t: __import__("time").sleep(0.3) or ""):
            self.assertEqual(asyncio.run(C.asset_prompt_check_async(ADDERALL)), "child-check-error")
        rows = self._child_audits()
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["category"], "child-check-error")

    def test_unlogged_pass_blocks(self):
        def boom(*_a, **_k):
            raise OSError("disk full")

        with mock.patch("core.audit.api", side_effect=boom):
            self.assertEqual(self._check(ADDERALL, grok="NO"), "child-check-error")

    def test_http_calls_are_audited(self):
        self._check(ADDERALL, grok=OSError("down"), local="NO")
        backends = [f.get("backend") for s, f in self.audits if s == "child-check" and "verdict" not in f]
        self.assertIn("xai", backends)
        self.assertIn("ollama", backends)


class LlamaGuardIsNeverCalled(Base):
    LG_NAMES = (
        "classify", "classify_request", "classify_request_async", "classify_response",
        "classify_response_async", "classify_turns_async", "_classify_messages", "_post",
        "_post_ollama_generate", "_official_llama_guard3_prompt", "lg4_categories",
        "_verdict_to_check", "_child_post", "CLASSIFIER_MODEL", "LOCAL_GUARD_MODEL",
        "CLOUD_GUARD_MODEL", "LLAMA_GUARD3_CATEGORIES",
    )

    def test_no_llama_guard_code_is_left(self):
        for name in self.LG_NAMES:
            self.assertFalse(hasattr(C, name), name)
        import core.asset_safety as S
        self.assertFalse(hasattr(S, "adult_output_ignore_lg4"))
        src = Path(C.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("llama-guard3", src)
        self.assertNotIn("llama-guard-4", src)

    def test_no_request_names_llama_guard(self):
        for text in FALSE_POSITIVES + EXPLICIT_MINORS:
            self._check(text, grok=OSError("down"), local="NO")
        img = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        with mock.patch("urllib.request.urlopen", side_effect=self._opener(grok="NO", local="NO")):
            asyncio.run(C.classify_async("a photo", images=img))
            asyncio.run(C.classify_media_async(img, adult=True))
        self.assertTrue(self.requests)
        for url, body in self.requests:
            self.assertIn(url, (XAI_URL, C.OLLAMA_CHAT_URL))
            self.assertNotIn("llama-guard", json.dumps(body).lower())
            self.assertNotIn("/api/generate", url)

    def test_chat_with_images_runs_the_child_check_on_the_text(self):
        img = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]

        def grok(text):
            return "YES" if "<text>" in text and "little boy" in text else "NO"

        with mock.patch("urllib.request.urlopen", side_effect=self._opener(grok=grok, local="NO")):
            self.assertEqual(asyncio.run(C.classify_async("look at this little boy", images=img)), "child-check-unsafe")
            self.assertEqual(asyncio.run(C.classify_async(C17_0708, images=img)), "")


class AssetPathUsesTheChildCheck(Base):
    def test_prepare_generation_passes_the_false_positives(self):
        from core import asset_safety as S
        from core.asset_safety import AssetMode

        sfw = AssetMode(enabled=True, adult=False, approval=False, owner_only=False, user_cap=10,
                        guild_cap=40, video=True, video_user_cap=5, video_guild_cap=15)
        for text in FALSE_POSITIVES:
            self.requests.clear()
            with mock.patch("urllib.request.urlopen", side_effect=self._opener(grok="NO")), \
                 mock.patch("core.asset_safety._audit"):
                try:
                    asyncio.run(S.prepare_generation(text, user_id=5, guild_id=1, channel_id=1, mode=sfw))
                except Exception as exc:  # pragma: no cover - surfaced in the message
                    self.fail(f"{text!r} blocked: {exc!r}")
            self.assertEqual([u for u, _ in self.requests], [XAI_URL], text)

    def test_media_filter_on_the_0707_prompt_is_the_personified_object_rule(self):
        # 07:07 ET: "Anthropomorphic female C-17 ... as a woman ... cargo bay
        # doors as a skirt ... military stomp boots". The structural
        # personified-object rule ("anthropomorphic" plus object words such as
        # doors and boots) blocks it by design; the bare jet prompts pass.
        from core.asset_filter import media_prompt_blocked

        self.assertTrue(media_prompt_blocked(
            "Anthropomorphic female C-17 Globemaster III military transport plane as a woman, "
            "cargo bay doors as a skirt, military stomp boots", adult=False))
        for text in FALSE_POSITIVES:
            self.assertFalse(media_prompt_blocked(text, adult=False), text)


if __name__ == "__main__":
    unittest.main()
