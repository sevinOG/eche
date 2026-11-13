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
    "kandinsky",
    "hidream",
    "lmage",
    "krea",
    "firefly",
    "cogview",
    "craiyon",
    "playground",
    "pixart",
    "janus-pro",
    "januspro",
    "sd-turbo",
    "sdturbo",
    "nova-canvas",
    "novacanvas",
    "gpt-img",
    "gptimg",
    "dal-e",
    "dale",
    "stable-cascade",
    "stablecascade",
    "deepfloyd",
    "deep-floyd",
    "niji",
    "mjv6",
    "mj-v6",
    "auraflow",
    "flox",
    "fl0x",
    "leonardo-phoenix",
    "leonardophoenix",
    "sana-1",
    "sana1",
    "lumiere",
    "sd4",
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
    "pixverse",
    "gen-3-alpha",
    "gen3alpha",
    "gen3a",
    "opensora",
    "wanx",
    "vidu",
    "novareel",
    "nova-reel",
    "ltxv",
    "ltx-2",
    "ltx2",
    "zeroscope",
    "sadtalker",
    "heygen",
    "wav2lip",
    "liveportrait",
    "hallo2",
    "hallo-2",
    "omnihuman",
    "i2vgen",
    "pyramid-flow",
    "pyramidflow",
    "dynamicrafter",
    "genmo",
    "aurora",
    "kllng",
    "synthesia",
    "talkinghead",
    "talking-head",
    "lipsync",
    "lip-sync",
    "d-id",
    "wan3",
    "hedra",
    "hedra-character",
    "hedracharacter",
)

# Lookalike letters that show up in evasive model ids (Cyrillic ѕora, etc.).
_MODEL_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h", "ѵ": "v",
    "ԁ": "d", "ԁ": "d",
    "Α": "a", "А": "a", "Е": "e", "О": "o", "Р": "p", "С": "c",
    "Ѕ": "s",
    "ꜱ": "s", "ᴇ": "e", "ᴏ": "o", "ʀ": "r", "ᴀ": "a", "ᴋ": "k",
    "ɴ": "n", "ɪ": "i", "ʟ": "l", "ᴜ": "u", "ɢ": "g", "ᴛ": "t",
    "ν": "v", "Ν": "v",
    "к": "k", "К": "k",
    "Ι": "i", "ı": "i",
    "ꓢ": "s", "ꮪ": "s", "ꓦ": "v", "ꓗ": "k",
    "๐": "o", "ƒ": "f", "ꮩ": "v", "ꓜ": "m",
    "ө": "o", "Ө": "o", "Ꮩ": "v", "໐": "o", "ꓡ": "l", "տ": "t",
    "$": "s",
})
def _load_model_confusables() -> dict[str, str]:
    """Extra letters from core/confusables.txt, layered on _MODEL_CONFUSABLES."""
    mapping: dict[str, str] = {}
    path = Path(__file__).with_name("confusables.txt")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return mapping
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and len(parts[0]) == 1 and parts[1]:
            mapping[parts[0]] = parts[1][0] if parts[1] else parts[1]
    return mapping


_FILE_CONFUSABLES = str.maketrans(_load_model_confusables())

_MODEL_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
})


# Short markers must match a whole token, not a substring of a compact id
# ("deepseek-chat-v3-0324" must not become veo via leet on version digits;
# "inkling" must not match kling).
_SHORT_MODEL_MARKERS = frozenset({"veo", "sora", "ray", "pika", "t2v", "i2v", "svd", "flux"})
_TOKEN_SPLIT = re.compile(r"[-_/./@]+")


def normalize_model_id(model: str) -> str:
    """Lowercase, NFKC, fold confusables, keep letters and digits only."""
    text = unicodedata.normalize("NFKC", model or "")
    text = text.translate(_MODEL_CONFUSABLES).translate(_FILE_CONFUSABLES).lower()
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
    text = text.translate(_MODEL_CONFUSABLES).translate(_FILE_CONFUSABLES).lower()
    text = unicodedata.normalize("NFD", text)
    kept: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in {"Mn", "Cf", "Cc"}:
            continue
        if ch == "@":
            kept.append("-")
        elif ch.isalnum() or ch in "-_/.":
            kept.append(ch)
        elif cat in {"Zs", "Pd", "Po", "Ps", "Pe", "Sk"}:
            kept.append("-")
    return "".join(kept)


def _tokens(model: str) -> list[str]:
    text = _folded_for_tokens(model)
    return [tok for tok in _TOKEN_SPLIT.split(text) if tok]


_MODEL_LEET_L = str.maketrans({
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t",
})


def _leet_token(token: str) -> str:
    """Leet-fold a token only when it already contains letters, never a version number."""
    if not token or not any(ch.isalpha() for ch in token):
        return token
    return token.translate(_MODEL_LEET)


def _leet_token_forms(token: str) -> list[str]:
    if not token or not any(ch.isalpha() for ch in token):
        return [token]
    forms = [token, token.translate(_MODEL_LEET)]
    alt = token.translate(_MODEL_LEET_L)
    if alt not in forms:
        forms.append(alt)
    return forms


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
    return list(dict.fromkeys(_leet_token_forms(token)))


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


_SHORT_SUFFIXES = ("pro", "fast", "turbo", "flash", "lite", "ultra", "mini", "max", "hd", "labs")
_SHORT_PREFIXES = ("super", "pro", "my", "x", "the", "our")


def _suffix_is_version_or_tier(text: str) -> bool:
    """Empty, digits, and/or a known tier; leftover letters after those are ok.

    sora2ultra, veomini, pikalabs, veo3max.
    """
    s = text or ""
    consumed = False
    while s:
        if s[0].isdigit():
            s = s.lstrip("0123456789")
            consumed = True
            continue
        hit = next((suf for suf in _SHORT_SUFFIXES if s.startswith(suf)), None)
        if hit:
            s = s[len(hit):]
            consumed = True
            continue
        if s.isalpha() and consumed:
            return True
        return False
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


# These compact forms are too short to use as a start-of-token prefix.
# "niji" must not reject nijinsky-chat; "did" (from d-id) must not reject
# didi-chat or d-idea.
_BOUNDED_MARKERS = frozenset({"niji", "did"})


def _bounded_token_match(token: str, marker: str) -> bool:
    """Exact token, or the marker plus a version/tier suffix only."""
    if not token or not marker:
        return False
    if token == marker:
        return True
    return token.startswith(marker) and _suffix_is_version_or_tier(token[len(marker):])


def _long_token_match(token: str, marker: str) -> bool:
    if marker in _BOUNDED_MARKERS:
        return _bounded_token_match(token, marker)
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
            leet_l = [_leet_token_forms(tok)[-1] if "1" in tok else _leet_token(tok) for tok in tokens[i:j + 1]]
            for span in ("".join(plain), "".join(leet), "".join(leet_l)):
                if span and span not in out:
                    out.append(span)
    return out


_VIDEO_CHAT_ALLOW = (
    "videollava", "llavavideo", "videollama",
    "videochatgpt", "videochat", "llavanextvideo", "videoxl",
)
_VIDEO_CHAT_TAG_RE = re.compile(
    r"(?i)(?:video[-_.]?llava|llava[-_.]?video|video[-_.]?llama|"
    r"video[-_.]?chatgpt|video[-_.]?chat|"
    r"llava[-_.]?next[-_.]?video|video[-_.]?xl)"
)
_IMAGE_SPELLING_RE = re.compile(r"(?:image|lmage|imaqe)", re.I)


def _strip_video_chat_tags(model: str) -> str:
    """Remove video-understanding tags, then the remainder can still be checked."""
    return _VIDEO_CHAT_TAG_RE.sub("-", model or "")


def _is_image_model_id_folded(model: str) -> bool:
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
        if _IMAGE_SPELLING_RE.search(span):
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


def _fold_lookalikes(model: str) -> list[str]:
    """Original plus I→l and !/| → i/l forms, before lowercasing."""
    raw = model or ""
    forms = [raw]
    as_l = raw.replace("I", "l").replace("Ι", "l")
    if as_l not in forms:
        forms.append(as_l)
    punct_i = raw.replace("!", "i").replace("|", "i")
    if punct_i not in forms:
        forms.append(punct_i)
    punct_l = raw.replace("!", "l").replace("|", "l").replace("I", "l").replace("Ι", "l")
    if punct_l not in forms:
        forms.append(punct_l)
    return forms


def is_image_model_id(model: str) -> bool:
    """True for known image/video generators, matched at the start of a token."""
    # Strip video-understanding tags first, then run the full check on
    # what remains, so gpt-image-1-video-llava and videollama-sora-2
    # are still rejected.
    remainder = _strip_video_chat_tags(model)
    if not remainder.strip("-_/.()[] 	"):
        return False
    if re.search(r"(?i)(?:^|/)dall(?:[-_]?e|\b)", remainder):
        return True
    for form in _fold_lookalikes(remainder):
        if _is_image_model_id_folded(form):
            return True
        if "@" in form and _is_image_model_id_folded(form.replace("@", "a")):
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


# Official Imagine choke-point modules. Exact dotted names only — a file
# named imagine.py under another package is not official and is scanned.
# Hashes are pinned in official_imagine.sha256, shipped with the build.
OFFICIAL_IMAGINE_MODULES = frozenset({
    "cogs.asset",
    "core.imagine",
    "core.tools_asset",
    "core.asset_safety",
    "core.asset_filter",
    "core.asset_backend",
    "core.asset_review",
    "core.asset_limits",
    "core.media_scan",
})

_OFFICIAL_MANIFEST_NAME = "official_imagine.sha256"
_OFFICIAL_MODULE_FILES = {
    "cogs.asset": "cogs/asset.py",
    "core.imagine": "core/imagine.py",
    "core.tools_asset": "core/tools_asset.py",
    "core.asset_safety": "core/asset_safety.py",
    "core.asset_filter": "core/asset_filter.py",
    "core.asset_backend": "core/asset_backend.py",
    "core.asset_review": "core/asset_review.py",
    "core.asset_limits": "core/asset_limits.py",
    "core.media_scan": "core/media_scan.py",
}


def is_official_imagine_module(module: str) -> bool:
    from core.denied_modules import normalize_module

    return normalize_module(module) in OFFICIAL_IMAGINE_MODULES


def official_imagine_manifest_path(source_root: str | Path | None = None) -> Path:
    root = Path(source_root) if source_root else Path(__file__).resolve().parent.parent
    return root / "core" / _OFFICIAL_MANIFEST_NAME


def load_official_imagine_hashes(source_root: str | Path | None = None) -> dict[str, str]:
    path = official_imagine_manifest_path(source_root)
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            digest, rel = parts[0], parts[1]
            out[rel.replace("\\", "/")] = digest.lower()
        elif len(parts) == 1:
            continue
    return out


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def official_hash_ok(module: str, path: Path | None, source_root: str | Path | None = None) -> bool:
    from core.denied_modules import normalize_module

    name = normalize_module(module)
    rel = _OFFICIAL_MODULE_FILES.get(name)
    if not rel:
        return False
    pinned = load_official_imagine_hashes(source_root).get(rel)
    if not pinned or path is None or not path.is_file():
        return False
    return file_sha256(path) == pinned


# The pin file authenticates itself: a `# manifest-sha256: <hex>` line
# stores the digest of the file with that hex zeroed. This covers the
# pin file without living in an unpinned Python module, and lets
# core/media_scan.py itself be pinned.
_MANIFEST_HASH_RE = re.compile(r"(?im)^(# manifest-sha256: )([0-9a-f]{64})\s*$")
_MANIFEST_HASH_ZERO = "0" * 64


def _manifest_canonical_bytes(text: str) -> bytes:
    if not _MANIFEST_HASH_RE.search(text or ""):
        return b""
    return _MANIFEST_HASH_RE.sub(
        lambda m: m.group(1) + _MANIFEST_HASH_ZERO, text, count=1
    ).encode("utf-8")


def embedded_manifest_sha256(source_root: str | Path | None = None) -> str:
    path = official_imagine_manifest_path(source_root)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    match = _MANIFEST_HASH_RE.search(text)
    return (match.group(2).lower() if match else "")


def manifest_file_sha256(source_root: str | Path | None = None) -> str:
    path = official_imagine_manifest_path(source_root)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    blob = _manifest_canonical_bytes(text)
    if not blob:
        return ""
    import hashlib
    return hashlib.sha256(blob).hexdigest()


def official_pins_ok(source_root: str | Path | None = None) -> str:
    """Why the official Imagine pins failed, or '' if every pin matches."""
    digest = manifest_file_sha256(source_root)
    stored = embedded_manifest_sha256(source_root)
    if not digest or not stored or digest != stored:
        return "official manifest hash mismatch"
    if len(_OFFICIAL_MODULE_FILES) != len(OFFICIAL_IMAGINE_MODULES):
        return "official pin set mismatch"
    for name, rel in _OFFICIAL_MODULE_FILES.items():
        path = module_source_path(name, source_root)
        if not official_hash_ok(name, path, source_root):
            return f"official hash mismatch: {rel}"
    return ""



def __getattr__(name: str):
    """EMBEDDED_MANIFEST_SHA256 reads the self-hash stored in the pin file."""
    if name == "EMBEDDED_MANIFEST_SHA256":
        return embedded_manifest_sha256()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def module_is_blocked(module: str, source_root: str | Path | None = None) -> str:
    """Why this module must not load, or '' if it is fine."""
    from core.denied_modules import is_denied_module, normalize_module

    if is_denied_module(module):
        return "denied media module"
    path = module_source_path(module, source_root)
    name = normalize_module(module)
    if is_official_imagine_module(module) or name == "cogs.asset":
        reason = official_pins_ok(source_root)
        if reason:
            return reason
        if official_hash_ok(name, path, source_root):
            return ""
        return "official hash mismatch"
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
