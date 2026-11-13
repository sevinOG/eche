# core/media_scan.py
# Reject leftover or user-dropped modules that still call an image/video
# generation API. The needles live here so loaders can scan source; they
# must not appear in other production modules.

from __future__ import annotations

from pathlib import Path

# Hosts, paths, SDKs, and response shapes used by image/video generators.
GENERATION_NEEDLES = (
    "images/generations",
    "videos/generations",
    "images/edits",
    "videos/edits",
    "api.x.ai",
    "console.x.ai",
    "grok-imagine",
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
    "luma-ai",
    "klingai",
    "midjourney",
    "comfyui",
    "/prompt",  # too broad if used alone — see COMBINED below
)

# Path fragments that only count next to an HTTP client or known host.
GENERATION_PATH_FRAGMENTS = (
    "images/generations",
    "videos/generations",
    "images/edits",
    "videos/edits",
    "sdapi/v1/txt2img",
    "v1/images",
    "v1/videos",
)

IMAGE_MODEL_MARKERS = (
    "flash-image",
    "flux",
    "sdxl",
    "stable-diffusion",
    "stable_diffusion",
    "imagine",
    "dall-e",
    "dall_e",
    "dalle",
    "grok-imagine",
    "text-to-image",
    "txt2img",
    "image-gen",
    "imagen",
)


def source_has_generation(text: str) -> str:
    """Return the first generation marker in text, or ''."""
    raw = text or ""
    low = raw.lower()
    for needle in GENERATION_NEEDLES:
        if needle == "/prompt":
            continue
        if needle.lower() in low:
            return needle
    if "comfy" in low and "/prompt" in low:
        return "comfyui /prompt"
    return ""


# Video generators. Matched as substrings of a model id, same as 'image'.
VIDEO_MODEL_MARKERS = (
    "video",
    "veo",
    "sora",
    "t2v",
    "i2v",
    "kling",
    "hailuo",
)


def is_image_model_id(model: str) -> bool:
    """True for known image/video generators and ids containing those markers."""
    text = (model or "").strip().lower()
    if not text:
        return False
    if "image" in text:
        return True
    if any(marker in text for marker in VIDEO_MODEL_MARKERS):
        return True
    return any(marker in text for marker in IMAGE_MODEL_MARKERS)


def file_has_generation(path: str | Path) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return source_has_generation(text)


def module_source_path(module: str, source_root: str | Path | None = None) -> Path | None:
    """Best-effort path of a cogs.* or core.* module under the app source."""
    name = (module or "").strip()
    if name.endswith(".py"):
        name = name[:-3]
    name = name.replace("\\", ".").replace("/", ".")
    if name.startswith("eche_ecosystem."):
        name = name[len("eche_ecosystem."):]
    if not name.startswith(("cogs.", "core.")):
        name = "cogs." + name
    rel = Path(*name.split(".")).with_suffix(".py")
    roots: list[Path] = []
    if source_root:
        roots.append(Path(source_root))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def module_is_blocked(module: str, source_root: str | Path | None = None) -> str:
    """Why this module must not load, or '' if it is fine."""
    from core.denied_modules import is_denied_module

    if is_denied_module(module):
        return "denied media module"
    path = module_source_path(module, source_root)
    if path is None:
        return ""
    hit = file_has_generation(path)
    if hit:
        return f"generation marker {hit}"
    return ""


def reject_image_model(model: str, default: str = "") -> str:
    """Return default when model is an image/video generator id."""
    value = (model or "").strip()
    if not value or is_image_model_id(value):
        return default
    return value
