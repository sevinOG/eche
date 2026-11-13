# Fourth-round repro (a36638f): stale-history drop shifts indices.
# Run from pr12d/eche_source: python -m pytest -q -p no:cacheprovider ../../repro/test_rereview3.py
import asyncio, io, json, os, sys, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import classifier as C, client
from core.safety import SAFETY_FOOTER, USER_GUARD
from tests.checker_fixtures import patch_legacy_checker_settings, start_image_path_classifier, stop_image_path_classifier

class _R(io.BytesIO):
    def __enter__(s): return s
    def __exit__(s, *a): return False

def _is_lg4(req):
    try:
        return json.loads(req.data.decode()).get("model") == C.CLASSIFIER_MODEL
    except Exception:
        return False

def _pack(v):
    return _R(json.dumps({"choices": [{"message": {"content": v}}]}).encode())

def _judge(req, *a, **k):
    if not _is_lg4(req):
        return _pack("NO")
    txt = req.data.decode()
    v = "unsafe\nS1" if ("HISTORY:" in txt and "BADLINE" in txt) else "safe"
    return _pack(v)

class StaleShift(unittest.TestCase):
    def setUp(self):
        C.allow_in_tests(False); C.clear_cache()
        self._legacy = patch_legacy_checker_settings(); self._legacy.start()
        self._images = start_image_path_classifier()
    def tearDown(self):
        stop_image_path_classifier(self._images); self._legacy.stop(); C.allow_in_tests(True); C.clear_cache()
    def test_flagged_window_with_stale_history(self):
        C.allow_in_tests(False); C.clear_cache()
        msgs = [{"role": "system", "content": "Persona: Eche."}]
        for n in range(10):
            msgs.append({"role": "user", "content": ("BADLINE " if n == 5 else "") + f"old line {n}"})
        msgs.append({"role": "assistant", "content": "assistant reply A"})
        msgs.append({"role": "user", "content": "=== MOST RECENT MESSAGE ===\nbob: hi there"})
        try:
            with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_x"}), \
                 mock.patch("urllib.request.urlopen", side_effect=_judge):
                out = asyncio.run(client._outgoing_messages_async(msgs))
        finally:
            C.allow_in_tests(True)
        texts = [str(m.get("content")) for m in out]
        self.assertFalse(any("BADLINE" in t for t in texts), "flagged history still sent")
        self.assertEqual(texts[-1], SAFETY_FOOTER, "footer is not the last message")
        self.assertIn(USER_GUARD, texts, "user guard dropped")
        self.assertTrue(any("hi there" in t for t in texts))


class FooterLastProperty(unittest.TestCase):
    """Footer is last and no flagged history line survives, for any length or flag index."""

    def setUp(self):
        C.allow_in_tests(False)
        C.clear_cache()
        self._legacy = patch_legacy_checker_settings()
        self._legacy.start()
        self._images = start_image_path_classifier()

    def tearDown(self):
        stop_image_path_classifier(self._images)
        self._legacy.stop()
        C.allow_in_tests(True)
        C.clear_cache()

    def test_any_length_and_flag_position(self):
        C.allow_in_tests(False)
        C.clear_cache()

        def judge(req, *a, **k):
            if not _is_lg4(req):
                return _pack("NO")
            txt = req.data.decode()
            v = "unsafe\nS1" if ("HISTORY:" in txt and "BADLINE" in txt) else "safe"
            return _pack(v)

        for length in range(1, 16):
            for flag_pos in range(length):
                msgs = [{"role": "system", "content": "Persona: Eche."}]
                for n in range(length):
                    prefix = "BADLINE " if n == flag_pos else ""
                    msgs.append({"role": "user", "content": prefix + f"old line {n}"})
                msgs.append({"role": "user", "content": "=== MOST RECENT MESSAGE ===\nbob: hi there"})
                with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_x"}), \
                     mock.patch("urllib.request.urlopen", side_effect=judge):
                    out = asyncio.run(client._outgoing_messages_async(msgs))
                texts = [str(m.get("content")) for m in out]
                self.assertFalse(
                    any("BADLINE" in t for t in texts),
                    f"flagged history survived length={length} pos={flag_pos}",
                )
                self.assertEqual(
                    texts[-1], SAFETY_FOOTER,
                    f"footer not last length={length} pos={flag_pos}",
                )
                self.assertIn(USER_GUARD, texts, f"guard dropped length={length} pos={flag_pos}")
                self.assertTrue(
                    any("hi there" in t for t in texts),
                    f"latest dropped length={length} pos={flag_pos}",
                )



if __name__ == "__main__":
    unittest.main()
