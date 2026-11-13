# core/media_scan.py
# Reject leftover or user-dropped modules that still call an image/video
# generation API. The needles live here so loaders can scan source; they
# must not appear in other production modules.

from __future__ import annotations

import unicodedata
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
# After normalize_model_id(), hyphens are gone, so also list compact forms
# (gen4, wan2) for ids like runway/gen-4 and wan-2.5.
VIDEO_MODEL_MARKERS = (
    "video",
    "veo",
    "sora",
    "t2v",
    "i2v",
    "kling",
    "hailuo",
    "runway",
    "gen-4",
    "gen4",
    "ray-",
    "ray",
    "pika",
    "seedance",
    "wan-2",
    "wan2",
    "mochi",
)

# Lookalike letters that show up in evasive model ids (Cyrillic ѕora, etc.).
_MODEL_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ѵ": "v",
    "ԁ": "d", "ԁ": "d",
    "Α": "a", "А": "a", "Е": "e", "О": "o", "Р": "p", "С": "c",
    "Ѕ": "s",
})
_MODEL_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
})


def normalize_model_id(model: str) -> str:
    """Lowercase, NFKC, fold confusables, keep letters and digits only."""
    text = unicodedata.normalize("NFKC", model or "")
    text = text.translate(_MODEL_CONFUSABLES).lower()
    text = unicodedata.normalize("NFD", text)
    kept: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in {"Mn", "Cf", "Cc", "Zs", "Pd", "Po", "Ps", "Pe", "Sk"}:
            continue
        if ch.isalnum():
            kept.append(ch)
    return "".join(kept)


def _model_id_forms(model: str) -> list[str]:
    compact = normalize_model_id(model)
    forms = []
    raw = (model or "").strip().lower()
    if raw:
        forms.append(raw)
    if compact and compact not in forms:
        forms.append(compact)
    leet = compact.translate(_MODEL_LEET)
    if leet and leet not in forms:
        forms.append(leet)
    return forms


def _marker_forms(marker: str) -> list[str]:
    forms = [marker.lower()]
    compact = normalize_model_id(marker)
    if compact and compact not in forms:
        forms.append(compact)
    return forms


def is_image_model_id(model: str) -> bool:
    """True for known image/video generators and ids containing those markers."""
    forms = _model_id_forms(model)
    if not forms:
        return False
    markers = []
    for marker in IMAGE_MODEL_MARKERS + VIDEO_MODEL_MARKERS:
        markers.extend(_marker_forms(marker))
    for text in forms:
        if "image" in text:
            return True
        if any(marker in text for marker in markers):
            return True
    return False


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
