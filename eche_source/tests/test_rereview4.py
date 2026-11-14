# Fifth-round independent repro for R12-14 (7d78ca8). Network mocked.
# Run from pr12e/eche_source: python -m pytest -q -p no:cacheprovider ../../repro/test_rereview4.py
import asyncio, hashlib, io, json, os, sys, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import classifier as C, client
from tests.checker_fixtures import patch_legacy_checker_settings, start_image_path_classifier, stop_image_path_classifier
from core.safety import SAFETY_FOOTER, USER_GUARD, MinorsBlocked

SHA = "dbb7d6ac3c29129fde736329e121f46d64827bf842f544c8c8fed7f60c669ec5"

class _R(io.BytesIO):
    def __enter__(s): return s
    def __exit__(s, *a): return False

def _judge_factory(markers):
    def judge(req, *a, **k):
        try:
            body = json.loads(req.data.decode())
            if body.get("model") != C.CLASSIFIER_MODEL:
                v = "NO"
            else:
                txt = req.data.decode()
                hit = "HISTORY:" in txt and any(m in txt for m in markers)
                v = "unsafe\nS12" if hit else "safe"
        except Exception:
            v = "safe"
        return _R(json.dumps({"choices": [{"message": {"content": v}}]}).encode())
    return judge

def build(n_hist, flagged, wordlist_at=(), combined_latest=False, assistant_every=0):
    msgs = [{"role": "system", "content": "Persona: Eche."}]
    for n in range(n_hist):
        if n in wordlist_at:
            content = f"old {n}: she's 14 btw"          # word-list flag
        elif n in flagged:
            content = f"old {n}: CLSFLAG{n} explicit"    # classifier-only flag
        else:
            content = f"old {n}: hello"
        role = "assistant" if assistant_every and n % assistant_every == 0 else "user"
        msgs.append({"role": role, "content": content})
    if combined_latest:
        msgs.append({"role": "user", "content": "RECENT:\nbob: CLSFLAGX in pin\n=== MOST RECENT MESSAGE ===\nbob: hi there"})
    else:
        msgs.append({"role": "user", "content": "=== MOST RECENT MESSAGE ===\nbob: hi there"})
    return msgs

class StaleAndWindow(unittest.TestCase):
    def setUp(self):
        C.allow_in_tests(False); C.clear_cache()
        self._legacy = patch_legacy_checker_settings(); self._legacy.start()
        self._images = start_image_path_classifier()
    def tearDown(self):
        stop_image_path_classifier(self._images); self._legacy.stop(); C.allow_in_tests(True); C.clear_cache()

    def _run(self, msgs, markers):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_x"}), \
             mock.patch("urllib.request.urlopen", side_effect=_judge_factory(markers)):
            return asyncio.run(client._outgoing_messages_async(msgs))

    def _check(self, out, banned):
        texts = [str(m.get("content")) for m in out]
        self.assertEqual(hashlib.sha256(SAFETY_FOOTER.encode()).hexdigest(), SHA)
        self.assertEqual(texts[0], SAFETY_FOOTER, "footer not first")
        self.assertEqual(texts[-1], SAFETY_FOOTER, "footer not last")
        self.assertEqual(texts[-2], USER_GUARD, "guard not before last footer")
        self.assertEqual(texts.count(SAFETY_FOOTER), 2)
        for b in banned:
            self.assertFalse(any(b in t for t in texts), f"flagged {b!r} was sent")
        self.assertTrue(any("hi there" in t for t in texts), "latest dropped")
        self.assertTrue(any("Persona: Eche." in t for t in texts), "persona dropped")

    def test_matrix(self):
        cases = []
        for n_hist in (9, 12, 20):
            for pos in (0, n_hist // 2, n_hist - 1):
                cases.append((n_hist, {pos}, ()))
            cases.append((n_hist, {1, n_hist - 2}, (n_hist // 3,)))       # mixed: classifier + word list
        for n_hist, flagged, wl in cases:
            with self.subTest(n=n_hist, flagged=sorted(flagged), wl=wl):
                msgs = build(n_hist, flagged, wordlist_at=wl, assistant_every=3)
                out = self._run(msgs, [f"CLSFLAG{p}" for p in flagged])
                self._check(out, [f"CLSFLAG{p}" for p in flagged] + ["she's 14"])

    def test_combined_latest_with_pin_flag(self):
        msgs = build(11, {4}, combined_latest=True)
        out = self._run(msgs, ["CLSFLAG4", "CLSFLAGX"])
        self._check(out, ["CLSFLAG4", "CLSFLAGX"])

    def test_no_flags_stale_dropped_footer_ok(self):
        out = self._run(build(15, set()), [])
        self._check(out, ["old 0:", "old 6:"])   # beyond window (15-8=7 stale: 0..6) must be gone

if __name__ == "__main__":
    unittest.main()
