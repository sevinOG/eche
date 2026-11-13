# tests/test_no_media_gen.py
# On this branch, only the official Grok Imagine / xAI backend may exist,
# and only through the asset choke point. Every other generator stays
# banned. ?flash stays deleted. ?image (Unsplash) is unchanged.
from __future__ import annotations

import ast
import asyncio
import importlib
import tempfile
import unittest
from unittest import mock
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

# Official Imagine choke-point modules. Endpoints and names may appear here
# only. Every other production file is still scanned for generation markers.
_OFFICIAL_IMAGINE = {
    ROOT / "core" / "imagine.py",
    ROOT / "core" / "asset_safety.py",
    ROOT / "core" / "tools_asset.py",
    ROOT / "core" / "asset_filter.py",
    ROOT / "core" / "asset_backend.py",
    ROOT / "core" / "asset_review.py",
    ROOT / "core" / "asset_limits.py",
    ROOT / "cogs" / "asset.py",
}

# Module names may appear in the denylist and the load-time scanner.
_MODULE_NEEDLES = (
    "core.imagine",
    "tools_asset",
    "cogs.asset",
    "cogs.flash",
    "plan_asset",
    "?asset",
    "is_asset_command",
)
_MODULE_NAME_OK = {
    ROOT / "core" / "denied_modules.py",
    ROOT / "core" / "media_scan.py",
    ROOT / "core" / "app_update.py",
    ROOT / "core" / "bot.py",
    ROOT / "cogs" / "events" / "on_message.py",
    ROOT / "core" / "tools.py",
    ROOT / "core" / "prompt_text.py",
    ROOT / "core" / "secrets.py",
    ROOT / "gui" / "widgets" / "settingswindow.py",
    ROOT / "gui" / "widgets" / "settings_help.py",
    *_OFFICIAL_IMAGINE,
}
_ENDPOINT_OK = {
    ROOT / "core" / "media_scan.py",
    ROOT / "core" / "secrets.py",
    ROOT / "gui" / "widgets" / "settings_help.py",
    *_OFFICIAL_IMAGINE,
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
    def test_only_official_imagine_modules_exist(self):
        for name in (
            "core.imagine",
            "core.tools_asset",
            "core.asset_safety",
            "core.asset_filter",
            "core.asset_backend",
            "core.asset_review",
            "core.asset_limits",
            "cogs.asset",
        ):
            self.assertFalse(_import_fails(name), f"{name} must be importable on this branch")
        self.assertTrue(_import_fails("cogs.flash"), "cogs.flash should not be importable")

    def test_tool_registry_has_only_official_asset(self):
        from core import tools  # noqa: F401
        from core import tools_admin, tools_context, tools_duckduckgo, tools_thread  # noqa: F401
        import core.tools_asset  # noqa: F401

        self.assertIn("asset", tools.registered_names())
        self.assertIn("asset", {name for name, _ in tools.PROMPT_TOOLS})
        self.assertNotIn("flash", tools.registered_names())
        self.assertNotIn("image_gen", tools.registered_names())

    def test_config_maps_include_imagine_keys(self):
        from core import secrets

        # Config only: never export XAI_API_KEY through ENV_MAP / apply_to_environ.
        self.assertIn("xai_api_key", secrets.SECRET_KEYS)
        self.assertNotIn("XAI_API_KEY", secrets.ENV_MAP.values())
        self.assertNotIn("xai_api_key", secrets.ENV_MAP)
        self.assertIn("tool_asset", secrets.PUBLIC_KEYS)
        self.assertIn("asset_owner_only", secrets.PUBLIC_KEYS)
        self.assertIn("asset_enabled", secrets.PUBLIC_KEYS)
        self.assertIn("asset_adult_enabled", secrets.PUBLIC_KEYS)
        self.assertIn("asset_approval_enabled", secrets.PUBLIC_KEYS)

    def test_generation_modules_include_flash(self):
        self.assertTrue(_import_fails("cogs.flash"), "cogs.flash should not be importable")

    def test_setup_hook_has_no_media_cog(self):
        src = (ROOT / "core" / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn('"cogs.flash"', src)
        self.assertNotIn("'cogs.flash'", src)
        self.assertIn('"cogs.asset"', src)

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
            if path in _MODULE_NAME_OK or path in _OFFICIAL_IMAGINE:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"core.imagine", "imagine"} or alias.name.endswith(".imagine"):
                            found.append(f"{path.relative_to(ROOT)} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    names = {a.name for a in node.names}
                    if mod in {"core.imagine", "core.tools_asset", "core.asset_safety", "cogs.asset"}:
                        found.append(f"{path.relative_to(ROOT)} from {mod}")
                    if "imagine" in names or "tools_asset" in names or "asset_safety" in names:
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
        from core.media_scan import is_image_model_id, reject_image_model, usable_chat_model

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
            "google/veo-3",
            "openai/sora-2",
            "kling-video",
            "wan2.2-t2v",
            "minimax/hailuo-video",
            "some-i2v-model",
            "v-e-o-3",
            "s.o.r.a",
            "kl1ng",
            "hai_luo",
            "ѕora",
            "veo\u200b3",
            "sora\u00ad2",
            "runway/gen-4",
            "luma/ray-2",
            "pika-2.2",
            "seedance-1-pro",
            "wan-2.5",
            "mochi-1",
            "hunyuanvideo",
            "gemini-nano-banana",
            "nano-banana",
            "gemini-omni",
            "midjourney",
            "seedream-4",
            "1mage",
            "gpt_1mage",
            "cogvideox",
            "veo3fast",
            "xsora2",
            "rayflash",
            "sora2pro",
            "text2video",
            "img2vid",
            "sd3.5",
            "ideogram-v3",
            "recraft-v3",
            "mj-v7",
            "v\u1d07o",
            "\ua731ora",
            "pik@",
            "$ora",
            "pikalabs",
            "s@o@r@a",
            "sora2ultra",
            "veomini",
            "k1ing",
            "lmage",
            "kandinsky",
            "hidream",
            "pixverse",
            "gen-3-alpha",
            "kl!ng",
            "kIing",
            "ꓢora",
            "ꜱᴏʀᴀ",
            "opensora",
            "wanx",
            "vidu",
            "krea",
            "firefly",
            "nova-reel",
            "cogview",
            "ltxv",
            "ltx-2",
            "zeroscope",
            "svd",
            "sadtalker",
            "heygen",
            "s\u03c3ra",
            "s\u0e50ra",
            "\ua730lux",
            "\u0192lux",
            "\uaba9eo",
            "\ua4dcidjourney",
            "thesora",
            "oursora",
            "kllng",
            "s\u04e9ra",
            "\u13d9EO",
            "mjv6",
            "niji",
            "stable-cascade",
            "deepfloyd-if",
            "openai/dall",
            "synthesia",
            "talking-head",
            "fl0x",
            "auraflow",
            "wan3",
            "sd4",
            "leonardo-phoenix",
            "sana-1.5",
            "lumiere",
            "hedra-character",
            "\u0455\u057fable-diffusion",
            "wav2lip",
            "liveportrait",
            "i2vgen-xl",
            "gpt-image-1-video-llava",
            "videollama-sora-2",
            "veo-3-videollava",
            "midjourney-videollama3",
            "dall-e-3 (llava-video)",
        ):
            self.assertTrue(is_image_model_id(model), model)
            # Saved settings are kept; only usable_chat_model swaps in a fallback.
            self.assertEqual(reject_image_model(model, "llama3"), model)
            self.assertEqual(usable_chat_model(model, "llama3"), "llama3")
        allowed = [
            "llama3",
            "qwen/qwen3.8-27b",
            "gpt-4o",
            "llama-3.2-11b-vision-preview",
            "llama-4-scout",
            "qwen2.5-vl",
            "qwen2.5-vl-72b",
            "meta-llama/llama-4-maverick",
            "openai/gpt-oss-120b",
            "deepseek/deepseek-v3",
            "deepseek/deepseek-chat-v3-0324",
            "thinkingmachines/inkling",
            "thinkingmachines/inkling-preview",
            "thinkingmachines/inkling-2",
            "thinkingmachines/inkling-2-small",
            "inkling-2-small",
            "thinkingmachines/inkling-small",
            "deepseek/deepseek-v3.1",
            "qwen2.5vl",
            "kimi-k2",
            "Video-LLaVA",
            "LLaVA-Video",
            "VideoLLaMA2",
            "llava-video-7b",
            "video-llava-7b",
            "LanguageBind/Video-LLaVA",
            "DAMO-NLP-SG/VideoLLaMA2",
            "videochat2",
            "video-chatgpt",
            "LLaVA-NeXT-Video",
            "LLaVA-NeXT-Video-7B",
            "video-xl",
        ]
        for model in allowed:
            self.assertFalse(is_image_model_id(model), model)

        with tempfile.TemporaryDirectory(prefix="eche-model-") as raw:
            root = Path(raw)
            (root / "config").mkdir()
            with self.assertLogs("eche.media_scan", level="WARNING") as captured:
                secrets.save_all(
                    {
                        "provider_backend": "cloud",
                        "cloud_model": "black-forest-labs/flux-dev",
                        "groq_model": "black-forest-labs/flux-dev",
                    },
                    root=str(root),
                )
            self.assertTrue(any("keeping the saved setting" in line for line in captured.output))
            loaded = secrets.load_all(str(root))
            self.assertEqual(loaded.get("cloud_model"), "black-forest-labs/flux-dev")
            self.assertEqual(loaded.get("groq_model"), "black-forest-labs/flux-dev")
            self.assertTrue(is_image_model_id(loaded.get("cloud_model") or ""))
            self.assertTrue(is_image_model_id(loaded.get("groq_model") or ""))

        self._assert_openrouter_chat_models_allowed()

    def test_safe_chat_model_falls_back_when_filter_fails(self):
        from core import client

        with mock.patch.object(client, "_chat_fallback", return_value="fallback-chat"),                 mock.patch("core.media_scan.usable_chat_model", side_effect=RuntimeError("load failed")):
            self.assertEqual(client._safe_chat_model("openai/gpt-image-1"), "fallback-chat")
            self.assertEqual(client._safe_chat_model("llama3"), "fallback-chat")

    def _assert_openrouter_chat_models_allowed(self):
        from core.media_scan import is_image_model_id

        roots = [
            Path("/workspace/eche-review-pr10-12"),
            Path("/workspace/eche-review-pr10-12/repro"),
            Path(__file__).resolve().parent,
        ]
        paths = []
        for root in roots:
            if root.is_dir():
                paths.extend(root.rglob("or_models.json"))
            elif root.name == "or_models.json" and root.is_file():
                paths.append(root)
        if not paths:
            return
        import json
        blocked = []
        for path in paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            models = raw.get("data", raw) if isinstance(raw, dict) else raw
            if isinstance(models, dict) and "models" in models:
                models = models["models"]
            if not isinstance(models, list):
                continue
            for item in models:
                if isinstance(item, str):
                    model_id = item
                    modality = ""
                elif isinstance(item, dict):
                    model_id = item.get("id") or item.get("name") or ""
                    arch = item.get("architecture") or {}
                    modality = " ".join([
                        str(item.get("modality") or ""),
                        str(arch.get("modality") or ""),
                        " ".join(arch.get("output_modalities") or []),
                        " ".join(item.get("output_modalities") or []),
                    ]).lower()
                else:
                    continue
                if not model_id:
                    continue
                if any(tag in modality for tag in ("image", "video")) and "text" not in modality:
                    continue
                # Skip known image/video generators in the catalog; chat models must pass.
                if is_image_model_id(model_id) and any(
                    tag in (modality or model_id).lower()
                    for tag in ("image", "video")
                ):
                    continue
                if is_image_model_id(model_id):
                    # If the catalog entry is a chat/text model, this is a regression.
                    if not modality or "text" in modality or "chat" in str(item).lower():
                        blocked.append(model_id)
        self.assertEqual(blocked, [], "chat models wrongly rejected: " + repr(blocked[:20]))

    def test_payload_and_summarizer_reject_image_model_ids(self):
        import os
        from core.client import _build_payload
        from core.media_scan import is_image_model_id
        from core.summarizer_prompt import get_summarizer_model

        def payload(*args, **kwargs):
            result = _build_payload(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return asyncio.run(result)
            return result

        body = payload(
            [{"role": "user", "content": "hi"}],
            "openai/gpt-image-1",
            128,
            0.2,
        )
        self.assertFalse(is_image_model_id(body["model"]), body["model"])
        self.assertTrue(body["model"])

        with mock.patch.dict(
            os.environ,
            {
                "GROQ_MODEL": "google/gemini-2.5-flash-image",
                "SUMMARIZER_MODEL": "openai/gpt-image-1",
            },
            clear=False,
        ):
            summary = get_summarizer_model()
            self.assertFalse(is_image_model_id(summary), summary)
            self.assertTrue(summary)
            again = payload(
                [{"role": "user", "content": "hi"}],
                os.environ["GROQ_MODEL"],
                128,
                0.2,
            )
            self.assertFalse(is_image_model_id(again["model"]), again["model"])

    def test_media_scan_allows_official_imagine_only(self):
        from core.media_scan import is_official_imagine_module, module_is_blocked

        for name in (
            "core.imagine",
            "core.tools_asset",
            "core.asset_safety",
            "core.asset_filter",
            "core.asset_backend",
            "core.asset_review",
            "core.asset_limits",
            "cogs.asset",
        ):
            self.assertTrue(is_official_imagine_module(name), name)
            self.assertEqual(module_is_blocked(name, source_root=ROOT), "")
        self.assertFalse(is_official_imagine_module("cogs.flash"))
        with tempfile.TemporaryDirectory(prefix="eche-scan-") as raw:
            root = Path(raw)
            (root / "cogs").mkdir()
            sneak = root / "cogs" / "sneaky.py"
            sneak.write_text(
                "import requests\nrequests.post('https://api.x.ai/v1/images/generations')\n",
                encoding="utf-8",
            )
            reason = module_is_blocked("cogs.sneaky", source_root=root)
            self.assertIn("generation marker", reason)

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

    def test_chat_lookalikes_are_not_rejected_as_generators(self):
        from core.media_scan import is_image_model_id
        for model in ("didi-chat", "d-idea", "nijinsky-chat"):
            self.assertFalse(is_image_model_id(model), model)


if __name__ == "__main__":
    unittest.main()
