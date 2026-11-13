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

_FORBIDDEN_SUBSTRINGS = (
    "images/generations",
    "videos/generations",
    "api.x.ai",
    "console.x.ai",
    "grok-imagine",
    "XAI_API_KEY",
    "core.imagine",
    "tools_asset",
    "cogs.asset",
    "asset_message",
    "plan_asset",
    "?asset",
    "is_asset_command",
)

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

    def test_production_source_has_no_generation_endpoints(self):
        hits = []
        for path in _iter_source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            # Tree comments may name dropped keys; still forbid endpoints.
            allow_key_names = path in _COMMENT_OK
            for needle in _FORBIDDEN_SUBSTRINGS:
                if needle not in text:
                    continue
                if allow_key_names and needle in {
                    "XAI_API_KEY",
                }:
                    # secrets.py should not map the env var; comments shouldn't
                    # need the env name. Fail anyway if it shows up.
                    hits.append(f"{path.relative_to(ROOT)}: {needle}")
                    continue
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
