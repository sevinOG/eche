"""The chat asset tool follows the Asset switch, not the retired prompt box.

Live 22:28 ET: asset_enabled=1 put "You make and edit pictures with the asset
tool" in the prompt, but a leftover tool_asset=0 from an older install kept
the tool out of the list, so Eche said it could not reach the asset tool.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core import prompt_text  # noqa: E402
from core import secrets  # noqa: E402
from core import tools  # noqa: E402
from core import tools_asset  # noqa: E402
from core import tools_thread  # noqa: E402


def _names() -> set[str]:
    return {spec["function"]["name"] for spec in tools.specs_for(speaker_is_owner=False)}


class AssetOfferFollowsSwitch(unittest.TestCase):
    def setUp(self):
        tools_asset.register_asset_tool()
        if hasattr(tools_thread, "register_thread_tool"):
            tools_thread.register_thread_tool()
        self._tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self._tmp.name, "config"), exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _settings(self, **values):
        with open(os.path.join(self._tmp.name, "config", "settings.json"), "w", encoding="utf-8") as f:
            json.dump(values, f)
        return mock.patch("core.secrets._user_root", return_value=self._tmp.name)

    def test_leftover_tool_asset_zero_does_not_hide_the_tool(self):
        # The PC's saved values on 2026-11-13.
        with self._settings(asset_enabled="1", tool_asset="0", default_tools="", tool_thread="1"), \
                mock.patch("core.asset_safety.asset_enabled", return_value=True):
            self.assertEqual(secrets.public_value("tool_asset"), "")
            self.assertIn("asset", _names())
            self.assertEqual(prompt_text._pictures_line(), prompt_text._PICTURES_ON)

    def test_asset_switch_off_hides_tool_and_prompt_says_no_pictures(self):
        with self._settings(asset_enabled="0", tool_asset="1"), \
                mock.patch("core.asset_safety.asset_enabled", return_value=False):
            self.assertNotIn("asset", _names())
            self.assertEqual(prompt_text._pictures_line(), prompt_text._PICTURES_OFF)

    def test_pictures_line_and_offer_agree(self):
        for on in (True, False):
            with self._settings(tool_asset="0"), \
                    mock.patch("core.asset_safety.asset_enabled", return_value=on):
                offered = "asset" in _names()
                says_on = prompt_text._pictures_line() == prompt_text._PICTURES_ON
                self.assertEqual(offered, says_on, on)

    def test_other_prompt_boxes_still_work(self):
        if "thread" not in tools.registered_names():
            self.skipTest("thread tool not registered")
        with self._settings(tool_thread="0"), \
                mock.patch("core.asset_safety.asset_enabled", return_value=True):
            self.assertNotIn("thread", _names())

    def test_no_asset_box_in_settings(self):
        self.assertNotIn("asset", {name for name, _ in tools.PROMPT_TOOLS})
        self.assertIn("tool_asset", secrets.RETIRED_PUBLIC_KEYS)
        self.assertNotIn("tool_asset", secrets.PUBLIC_KEYS)


if __name__ == "__main__":
    unittest.main()
