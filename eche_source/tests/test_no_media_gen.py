# tests/test_no_media_gen.py
# Fails if image or video generation is reintroduced: no Imagine client,
# no generation endpoints or hostnames in production source, no generation
# tool in the registry, and leftover config keys must not be loaded.
from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Production trees only. Tests may mention deleted APIs when asserting absence.
_SKIP_DIRS = {"tests", "__pycache__", "assets", "venv", ".venv"}

_ENDPOINT_NEEDLES = (
    "images/generations",
    "videos/generations",
    "images/edits",
    "videos/edits",
    "api.x.ai",
    "console.x.ai",
    "grok-imagine",
    "XAI_API_KEY",
    "generativelanguage.googleapis.com",
    "responseModalities",
    "flash-image",
    "gemini-2.5-flash-image",
    "dall-e",
    "dall.e",
    "openai.images",
    "images.generate",
    "api.stability.ai",
    "stability.sd",
    "replicate.com",
    "fal.ai",
    "api.fal.ai",
)

# Module names may appear in core/denied_modules.py so leftover files cannot load.
_MODULE_NEEDLES = (
    "core.imagine",
    "tools_asset",
    "cogs.asset",
    "cogs.flash",
    "asset_message",
    "plan_asset",
    "?asset",
    "is_asset_command",
)
_MODULE_NAME_OK = {
    ROOT / "core" / "denied_modules.py",
}
# The minors matcher still recognizes a leftover "?asset …" prompt. That is
# not a generation client.
_MODULE_NEEDLE_OK = {
    ROOT / "core" / "blocklist.py": {"?asset"},
}

# Comments in secrets.py may name dropped keys so leftover install files
# are ignored. Those names must not appear in loadable key maps.
_COMMENT_OK = {
    ROOT / "core" / "secrets.py",
}


def _iter_source_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".txt", ".spec", ".json"}:
            continue
        yield path


def _import_fails(name: str) -> bool:
    try:
        importlib.import_module(name)
        return False
    except ModuleNotFoundError:
        return True


class NoMediaGenerationTests(unittest.TestCase):
    def test_generation_modules_are_gone(self):
        for name in (
            "core.imagine",
            "core.tools_asset",
            "core.asset_message",
            "cogs.asset",
            "cogs.flash",
        ):
            self.assertTrue(_import_fails(name), f"{name} should not be importable")

    def test_tool_registry_has_no_asset(self):
        # Import side-effect modules so every remaining tool is registered.
        from core import tools  # noqa: F401
        from core import tools_admin, tools_context, tools_duckduckgo, tools_thread  # noqa: F401

        self.assertNotIn("asset", tools.registered_names())
        self.assertNotIn("asset", {name for name, _ in tools.PROMPT_TOOLS})

    def test_config_maps_drop_imagine_keys(self):
        from core import secrets

        self.assertNotIn("xai_api_key", secrets.SECRET_KEYS)
        self.assertNotIn("XAI_API_KEY", secrets.ENV_MAP.values())
        self.assertNotIn("xai_api_key", secrets.ENV_MAP)
        self.assertNotIn("tool_asset", secrets.PUBLIC_KEYS)
        self.assertNotIn("asset_owner_only", secrets.PUBLIC_KEYS)

    def test_generation_modules_include_flash(self):
        self.assertTrue(_import_fails("cogs.flash"), "cogs.flash should not be importable")

    def test_setup_hook_has_no_media_cog(self):
        src = (ROOT / "core" / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn('"cogs.flash"', src)
        self.assertNotIn("'cogs.flash'", src)
        self.assertNotIn('"cogs.asset"', src)

    def test_production_source_has_no_generation_endpoints(self):
        hits = []
        for path in _iter_source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in _ENDPOINT_NEEDLES:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)}: {needle}")
            if path in _MODULE_NAME_OK:
                continue
            allowed = _MODULE_NEEDLE_OK.get(path, set())
            for needle in _MODULE_NEEDLES:
                if needle in text and needle not in allowed:
                    hits.append(f"{path.relative_to(ROOT)}: {needle}")
        self.assertEqual(hits, [], "generation markers found in production source")

    def test_production_ast_has_no_imagine_imports(self):
        found = []
        for path in _iter_source_files():
            if path.suffix != ".py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"core.imagine", "imagine"} or alias.name.endswith(".imagine"):
                            found.append(f"{path.relative_to(ROOT)} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    names = {a.name for a in node.names}
                    if mod in {"core.imagine", "core.tools_asset", "core.asset_message", "cogs.asset"}:
                        found.append(f"{path.relative_to(ROOT)} from {mod}")
                    if "imagine" in names or "tools_asset" in names or "asset_message" in names:
                        if mod in {"core", "cogs", ""}:
                            found.append(f"{path.relative_to(ROOT)} from {mod} import {names}")
        self.assertEqual(found, [], "generation imports found in production source")


if __name__ == "__main__":
    unittest.main()
