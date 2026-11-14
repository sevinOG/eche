# tests/test_discord_url_false_blocks.py
# 2026-11-13 20:47 ET false block: a Discord CDN signing hash (hm=...8f...)
# read as "8 female" and tripped the under-age keyword rule on an adult
# image. Machine-made URL parts are no longer scanned; typed text and real
# filename words still are, with the rule unchanged.
from __future__ import annotations

import random
import secrets
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import blocklist as bl  # noqa: E402

HOSTS = ("https://cdn.discordapp.com", "https://media.discordapp.net")
NAMES = ("asset.jpg", "image.png", "IMG_4021.jpg", "video.mp4", "unknown.png", "Screenshot_2026-11-13_204512.png")


def _url(name=None, hm=None, host=None):
    rng = random.Random()
    return (
        f"{host or rng.choice(HOSTS)}/attachments/{rng.randint(10**17, 10**19)}/"
        f"{rng.randint(10**17, 10**19)}/{name or rng.choice(NAMES)}"
        f"?ex={secrets.token_hex(4)}&is={secrets.token_hex(4)}&hm={hm or secrets.token_hex(32)}&"
    )


class DiscordUrlTests(unittest.TestCase):
    def test_random_signed_urls_never_trip(self):
        for _ in range(300):
            url = _url()
            self.assertEqual(bl.match_info(f"url: {url}"), ("", ""), url)
            self.assertFalse(bl.is_blocked(f"Attachment: x\nurl: {url}"), url)

    def test_hm_with_8f_or_under_age_numbers_does_not_trip(self):
        for hm in ("8f3e2a1b", "ab8fcd", "17m00", "deadbeef8f8f", "12f"):
            url = _url(name="asset.jpg", hm=hm)
            self.assertEqual(bl.match_info(url), ("", ""), url)
        self.assertEqual(bl.match_info(_url(name=f"{secrets.token_hex(8)}.webp")), ("", ""))

    def test_typed_text_still_trips(self):
        for typed in ("an 8 f", "she is 8f", "8yo", "8 year old girl"):
            self.assertEqual(bl.match_info(typed)[1], "age", typed)
        # Typed words next to a URL are still scanned.
        self.assertEqual(bl.match_info("she is 8f " + _url(hm="00")) [1], "age")
        self.assertTrue(bl.is_blocked("an 8 f " + _url()))

    def test_meaningful_filename_words_still_scanned(self):
        self.assertEqual(bl.match_info(_url(name="8yo_girl.png"))[1], "age")
        proxy = "https://media.discordapp.net/external/AbC123xyz/https/example.com/8yo-girl/pic.png?width=400"
        self.assertEqual(bl.match_info(proxy)[1], "age")

    def test_other_hosts_are_untouched(self):
        self.assertEqual(bl.match_info("https://example.com/a?hm=8f")[1], "age")


if __name__ == "__main__":
    unittest.main()
