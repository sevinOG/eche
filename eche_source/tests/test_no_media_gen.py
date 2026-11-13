# tests/test_no_media_gen.py
# Fails if image or video generation is reintroduced: no Imagine client,
# no generation endpoints or hostnames in production source, no generation
# tool in the registry, and leftover config keys must not be loaded.
from __future__ import annotations

import ast
import importlib
import tempfile
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
    "response_modalities",
    "flash-image",
    "gemini-2.5-flash-image",
    "dall-e",
    "dall.e",
    "openai.images",
    "images.generate",
    "generate_images",
    "api.stability.ai",
    "stability.sd",
    "replicate.com",
    "fal.ai",
    "api.fal.ai",
    "b64_json",
    "text-to-image",
    "sdapi/v1/txt2img",
    "google-genai",
    "google.genai",
    "pollinations",
    "ideogram.ai",
    "leonardo.ai",
    "runwayml",
    "luma.ai",
    "klingai",
    "midjourney",
    "comfyui",
)

# Module names may appear in the denylist and the load-time scanner.
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
    ROOT / "core" / "media_scan.py",
    ROOT / "core" / "app_update.py",
}
_ENDPOINT_OK = {
    ROOT / "core" / "media_scan.py",
}
# The minors matcher still recognizes a leftover "?asset …" prompt. That is
# not a generation client.
_MODULE_NEEDLE_OK = {
    ROOT / "core" / "blocklist.py": {"?asset"},
    ROOT / "core" / "audit.py": {"?asset"},
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
            if path not in _ENDPOINT_OK:
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

    def test_client_never_sends_modalities_and_ignores_message_images(self):
        from core.client import _extract_message, _request_body

        body = _request_body({
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "modalities": ["image", "text"],
            "response_modalities": ["IMAGE"],
            "responseModalities": ["IMAGE"],
        })
        self.assertNotIn("modalities", body)
        self.assertNotIn("response_modalities", body)
        self.assertNotIn("responseModalities", body)
        self.assertEqual(body["model"], "test-model")

        msg = _extract_message({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "here you go",
                    "images": [{"url": "https://example.invalid/gen.png"}],
                }
            }]
        })
        self.assertIsNotNone(msg)
        self.assertNotIn("images", msg)
        self.assertEqual(msg["content"], "here you go")

    def test_image_model_ids_are_rejected(self):
        from core import secrets
        from core.media_scan import is_image_model_id, reject_image_model

        for model in (
            "google/gemini-2.5-flash-image",
            "google/gemini-3-pro-image-preview",
            "openai/gpt-5-image",
            "x/z-image-turbo",
            "black-forest-labs/flux-schnell",
            "stabilityai/sdxl",
            "grok-imagine",
            "openai/dall-e-3",
            "ollama/llava-imagen",
        ):
            self.assertTrue(is_image_model_id(model), model)
            self.assertEqual(reject_image_model(model, "llama3"), "llama3")
        self.assertFalse(is_image_model_id("llama3"))
        self.assertFalse(is_image_model_id("qwen/qwen3.8-27b"))
        self.assertFalse(is_image_model_id("gpt-4o"))

        with tempfile.TemporaryDirectory(prefix="eche-model-") as raw:
            root = Path(raw)
            (root / "config").mkdir()
            secrets.save_all(
                {
                    "provider_backend": "cloud",
                    "cloud_model": "black-forest-labs/flux-dev",
                    "groq_model": "black-forest-labs/flux-dev",
                },
                root=str(root),
            )
            loaded = secrets.load_all(str(root))
            self.assertFalse(is_image_model_id(loaded.get("cloud_model") or ""))
            self.assertFalse(is_image_model_id(loaded.get("groq_model") or ""))
            self.assertNotIn("flux", (loaded.get("cloud_model") or "").lower())

    def test_runtime_urls_do_not_build_generation_paths(self):
        fragments = (
            "images/generations",
            "videos/generations",
            "images/edits",
            "videos/edits",
            "sdapi/v1/txt2img",
        )
        hits = []
        for path in _iter_source_files():
            if path.suffix != ".py" or path in _ENDPOINT_OK:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    text = "".join(
                        part.value
                        for part in node.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    )
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    parts = []
                    cur = node
                    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
                        if isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                            parts.append(cur.right.value)
                        cur = cur.left
                    if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                        parts.append(cur.value)
                    text = "".join(reversed(parts))
                else:
                    continue
                for needle in fragments:
                    if needle in text:
                        hits.append(f"{path.relative_to(ROOT)}: {needle}")
        self.assertEqual(hits, [], "runtime-built generation URLs found")


if __name__ == "__main__":
    unittest.main()
