# core/media_scan.py
# Reject leftover or user-dropped modules that still call an image/video
# generation API. The needles live here so loaders can scan source; they
# must not appear in other production modules.

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

_log = logging.getLogger("eche.media_scan")

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
    "gemini-nano-banana",
    "nano-banana",
    "gemini-omni",
    "midjourney",
    "seedream",
    "ideogram",
    "recraft",
    "mj-v7",
    "mjv7",
    "sd3.5",
    "sd35",
    "sd3",
    "imggen",
    "img-gen",
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
    "hunyuanvideo",
    "text2video",
    "text-to-video",
    "txt2vid",
    "img2vid",
    "img2video",
    "cogvideo",
    "stablevideo",
    "stable-video",
    "svd-xt",
    "svdxt",
    "animatediff",
    "animate-diff",
)

# Lookalike letters that show up in evasive model ids (Cyrillic ѕora, etc.).
_MODEL_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ѵ": "v",
    "ԁ": "d", "ԁ": "d",
    "Α": "a", "А": "a", "Е": "e", "О": "o", "Р": "p", "С": "c",
    "Ѕ": "s",
    "ꜱ": "s", "ᴇ": "e",
    "ν": "v", "Ν": "v",
    "к": "k", "К": "k",
    "$": "s", "@": "a",
})
_MODEL_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
})


# Short markers must match a whole token, not a substring of a compact id
# ("deepseek-chat-v3-0324" must not become veo via leet on version digits;
# "inkling" must not match kling).
_SHORT_MODEL_MARKERS = frozenset({"veo", "sora", "ray", "pika", "t2v", "i2v"})
_TOKEN_SPLIT = re.compile(r"[-_/.]+")


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


def _folded_for_tokens(model: str) -> str:
    text = unicodedata.normalize("NFKC", model or "")
    text = text.translate(_MODEL_CONFUSABLES).lower()
    text = unicodedata.normalize("NFD", text)
    kept: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in {"Mn", "Cf", "Cc"}:
            continue
        if ch.isalnum() or ch in "-_/.":
            kept.append(ch)
        elif cat in {"Zs", "Pd", "Po", "Ps", "Pe", "Sk"}:
            kept.append("-")
    return "".join(kept)


def _tokens(model: str) -> list[str]:
    text = _folded_for_tokens(model)
    return [tok for tok in _TOKEN_SPLIT.split(text) if tok]


def _leet_token(token: str) -> str:
    """Leet-fold a token only when it already contains letters, never a version number."""
    if not token or not any(ch.isalpha() for ch in token):
        return token
    return token.translate(_MODEL_LEET)


def _join_singleton_runs(tokens: list[str]) -> list[str]:
    """v, e, o, 3 -> veo, 3. Catches v-e-o-3 and s.o.r.a."""
    out: list[str] = []
    run: list[str] = []
    def flush() -> None:
        if run:
            out.append("".join(run))
            run.clear()
    for tok in tokens:
        if len(tok) == 1 and tok.isalpha():
            run.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return out


def _token_forms(token: str) -> list[str]:
    forms = [token]
    leet = _leet_token(token)
    if leet != token:
        forms.append(leet)
    return forms


def _marker_forms(marker: str) -> list[str]:
    forms = [marker.lower()]
    compact = normalize_model_id(marker)
    if compact and compact not in forms:
        forms.append(compact)
    return forms


def _short_markers() -> set[str]:
    names = set(_SHORT_MODEL_MARKERS)
    for marker in IMAGE_MODEL_MARKERS + VIDEO_MODEL_MARKERS:
        for form in _marker_forms(marker):
            if form in _SHORT_MODEL_MARKERS:
                names.add(form)
    return names


def _long_markers() -> list[str]:
    short = _short_markers()
    out: list[str] = []
    for marker in IMAGE_MODEL_MARKERS + VIDEO_MODEL_MARKERS:
        for form in _marker_forms(marker):
            if form and form not in short and form not in out:
                out.append(form)
    return out


_SHORT_SUFFIXES = ("pro", "fast", "turbo", "flash", "lite")
_SHORT_PREFIXES = ("super", "pro", "my", "x")


def _suffix_is_version_or_tier(text: str) -> bool:
    """Empty, digits, and/or pro/fast/turbo/flash/lite."""
    s = text or ""
    while s:
        if s[0].isdigit():
            s = s.lstrip("0123456789")
            continue
        hit = next((suf for suf in _SHORT_SUFFIXES if s.startswith(suf)), None)
        if not hit:
            return False
        s = s[len(hit):]
    return True


def _prefix_is_short_evasion(text: str) -> bool:
    return (text or "") in ("",) + _SHORT_PREFIXES


def _short_token_match(token: str, marker: str) -> bool:
    """Whole-token match, or a short marker with a version/tier wrapper.

    veo3, sora2pro, veo3fast, rayflash, xsora2, prosora, supersora, mysora.
    """
    if not token or not marker:
        return False
    start = 0
    while True:
        pos = token.find(marker, start)
        if pos < 0:
            return False
        if _prefix_is_short_evasion(token[:pos]) and _suffix_is_version_or_tier(token[pos + len(marker):]):
            return True
        start = pos + 1


def _long_token_match(token: str, marker: str) -> bool:
    return token == marker or token.startswith(marker)


def _adjacent_spans(tokens: list[str]) -> list[str]:
    """Concatenations of adjacent tokens, with and without letter-token leet.

    Digit-only tokens are never leet-folded. The un-leeted join keeps
    ids like mj-v7 (mj + v7) from becoming mjvt.
    """
    n = len(tokens)
    out: list[str] = []
    for i in range(n):
        plain: list[str] = []
        leet: list[str] = []
        for j in range(i, n):
            plain.append(tokens[j])
            leet.append(_leet_token(tokens[j]))
            for span in ("".join(plain), "".join(leet)):
                if span and span not in out:
                    out.append(span)
    return out


def is_image_model_id(model: str) -> bool:
    """True for known image/video generators, matched at the start of a token."""
    raw_tokens = _tokens(model)
    if not raw_tokens and not (model or "").strip():
        return False
    tokens = _join_singleton_runs(raw_tokens)
    short = _short_markers()
    long = _long_markers()
    spans = _adjacent_spans(tokens)
    # Also consider each raw token and its leet form as a span of one.
    for tok in tokens:
        for form in _token_forms(tok):
            if form not in spans:
                spans.append(form)
    for span in spans:
        if "image" in span:
            return True
        for marker in short:
            if _short_token_match(span, marker):
                return True
        for marker in long:
            if _long_token_match(span, marker):
                return True
    if "image" in normalize_model_id(model):
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
    """Return default only when the id is blank.

    A saved image/video generator id is kept and a warning is logged.
    Callers that need a chat model at use time should use usable_chat_model().
    """
    value = (model or "").strip()
    if not value:
        return default
    if is_image_model_id(value):
        _log.warning(
            "Rejected an image/video generator model id; keeping the saved setting."
        )
        return value
    return value


def usable_chat_model(model: str, default: str = "") -> str:
    """For a live chat/completions call only. Does not rewrite saved settings."""
    value = (model or "").strip()
    if not value or is_image_model_id(value):
        if value:
            _log.warning(
                "Not sending a saved image/video generator model id; using a fallback at call time."
            )
        return default
    return value
