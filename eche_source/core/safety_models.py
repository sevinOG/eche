# core/safety_models.py
# Historical model names. The live checker models are hardcoded in
# core/classifier.py (Grok on the xAI key first, local qwen3-vl:4b-instruct
# as the backup; no Llama Guard). Nothing here is a setting.

from __future__ import annotations

CHILD_CHECK_DEFAULT_GROQ = "llama-3.1-8b-instant"
CHILD_CHECK_DEFAULT_OPENROUTER = "meta-llama/llama-3.1-8b-instruct"
AGE_VISION_DEFAULT_GROQ = "meta-llama/llama-4-scout-17b-16e-instruct"
AGE_VISION_DEFAULT_OPENROUTER = "meta-llama/llama-4-scout"

# Reviewed instruction / vision models only. Guard, image, auto, and
# unknown ids are never used.
_CHILD_CHECK_ALLOWED = frozenset({
    CHILD_CHECK_DEFAULT_GROQ,
    CHILD_CHECK_DEFAULT_OPENROUTER,
    "meta-llama/llama-3.1-8b-instant",
    "llama-3.1-8b-instruct",
})
_AGE_VISION_ALLOWED = frozenset({
    AGE_VISION_DEFAULT_GROQ,
    AGE_VISION_DEFAULT_OPENROUTER,
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-scout",
})


def _is_openrouter(url: str) -> bool:
    return "openrouter" in (url or "").lower()


def child_check_model(url: str = "") -> str:
    """Groq or OpenRouter 8B instruct. No env or settings override."""
    if _is_openrouter(url):
        return CHILD_CHECK_DEFAULT_OPENROUTER
    return CHILD_CHECK_DEFAULT_GROQ


def age_vision_model(url: str = "") -> str:
    """Groq or OpenRouter Llama 4 Scout. No env or settings override."""
    if _is_openrouter(url):
        return AGE_VISION_DEFAULT_OPENROUTER
    return AGE_VISION_DEFAULT_GROQ


def is_vetted_child_check_model(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    low = text.lower()
    if "guard" in low or "image" in low or low.endswith("/auto") or "/auto" in low:
        return False
    return text in _CHILD_CHECK_ALLOWED


def is_vetted_age_vision_model(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    low = text.lower()
    if "guard" in low or low.endswith("/auto") or "/auto" in low:
        return False
    try:
        from core.media_scan import is_image_model_id
        if is_image_model_id(text) and "scout" not in low:
            return False
    except Exception:
        pass
    return text in _AGE_VISION_ALLOWED
