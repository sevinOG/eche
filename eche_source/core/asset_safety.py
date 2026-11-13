# core/asset_safety.py
# The single choke point for ?asset and the asset tool.
#
# Every generation prompt is handled here, in order:
#   1. the full minors matcher (including bare boy/girl/kids)
#   2. a media-only size/age/family/stylization filter (~20 languages)
#   3. the Llama Guard classifier, plus the R12-23 child-resemblance check
#   4. the hardcoded safety footer, appended last as explicit rules
#   5. an adult-only rewrite
#
# Generated images, every video frame sampled at 4 fps (plus the last
# frame), and every frame of an animated GIF or WebP, go through Llama
# Guard vision and a fail-closed under-21 appearance check. Unsure and
# errors block. A blocked prompt is never retried. A blocked asset is
# never posted or written to memory. The only backend is Grok Imagine
# (xAI); there is no fallback generator.

from __future__ import annotations

import base64
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

ADULT_RULE = (
    "All characters are adults 21 years or older. "
    "Do not depict children, minors, or anyone who looks under 21."
)
OUTPUT_CLASSIFY_TEXT = (
    "Classify this generated media for sexual or other content involving "
    "minors, child-coded characters, or any listed safety category."
)
AGE_VISION_PROMPT = (
    "Does this contain any human, humanoid, or anthropomorphic character "
    "that could be read as under 21? Answer with only yes, no, or unsure, "
    "on the first line, with no punctuation or explanation."
)
KILL_SWITCH_OFF = (
    "Asset generation is off. An owner can turn it on in Settings → Media."
)
MISSING_KEY = (
    "Asset generation needs an xAI API key in Settings → Media. "
    "There is no fallback generator. An owner must add the key."
)
OUTPUT_REFUSAL = "That one was blocked and was not posted."
OUTPUT_UNCHECKED = "I could not check that file, so it was not posted."
VISION_UNAVAILABLE = "Image checks are unavailable, so the file was not posted."
MEDIA_FILTER_REFUSAL = OUTPUT_REFUSAL

FRAME_FPS = 4
LAST_FRAME_EPSILON = 0.05


@dataclass
class PreparedPrompt:
    user_prompt: str
    sent_prompt: str
    footer_hash: str


def asset_enabled() -> bool:
    """Owner kill switch. Blank or missing is OFF."""
    try:
        from core.secrets import public_value
        from core.tools import prompt_choice

        return prompt_choice(public_value("asset_enabled"), default=False)
    except Exception:
        return False


def require_enabled() -> None:
    """Re-check the kill switch. Used before each poll and before posting."""
    from core.imagine import ImagineError

    if not asset_enabled():
        raise ImagineError(KILL_SWITCH_OFF)


def footer_is_last(sent: str) -> bool:
    from core.safety import SAFETY_FOOTER

    text = sent or ""
    return text.endswith(SAFETY_FOOTER) and text.count(SAFETY_FOOTER) == 1


def build_sent_prompt(user_prompt: str) -> tuple[str, str]:
    """Adult-only rewrite, then the user prompt, then the footer last."""
    from core.safety import SAFETY_FOOTER, footer_sha256

    body = (user_prompt or "").strip()
    # Cap applies to the user prompt only. The footer must still come last.
    if len(body) > 2000:
        body = body[:2000].rstrip()
    sent = f"{ADULT_RULE}\n\n{body}\n\n{SAFETY_FOOTER}"
    return sent, footer_sha256()


def _audit(
    outcome: str,
    *,
    category: str = "",
    footer_hash: str = "",
    request_id: str = "",
    response_id: str = "",
    user_id=None,
    guild_id=None,
    channel_id=None,
    error_type: str = "",
) -> None:
    try:
        from core.audit import record
        from core.safety import footer_sha256

        digest = footer_hash or footer_sha256()
        safety = outcome in {"refusal", "error", "blocked"} or bool(category)
        # Never include the prompt text, especially on blocks.
        record(
            "asset",
            outcome=outcome,
            category=category,
            footer_hash=digest,
            request_id=request_id,
            response_id=response_id,
            error_type=error_type,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            safety=safety,
        )
    except Exception:
        pass


def _lock_on_block(user_id) -> None:
    try:
        from core.asset_limits import lockout_user

        lockout_user(user_id)
    except Exception:
        pass


async def prepare_generation(
    prompt: str,
    *,
    user_id=None,
    guild_id=None,
    channel_id=None,
) -> PreparedPrompt:
    """Matcher, media filter, classifier (incl. child check), footer-last. Fail closed."""
    from core.asset_filter import media_prompt_blocked
    from core.blocklist import REFUSAL, is_blocked, note
    from core.classifier import classify_async
    from core.guardrails import BYPASS_REFUSAL, asks_to_bypass_filter
    from core.imagine import ImagineError

    raw = (prompt or "").strip()
    ids = dict(user_id=user_id, guild_id=guild_id, channel_id=channel_id)

    if asks_to_bypass_filter(raw):
        note("asset prompt bypass")
        _lock_on_block(user_id)
        _audit("refusal", category="bypass", **ids)
        raise ImagineError(BYPASS_REFUSAL)
    if is_blocked(raw, user_id=user_id, channel_id=channel_id):
        note("asset prompt")
        _lock_on_block(user_id)
        _audit("refusal", category="matcher", **ids)
        raise ImagineError(REFUSAL)
    if media_prompt_blocked(raw):
        note("asset prompt media-filter")
        _lock_on_block(user_id)
        _audit("refusal", category="media-filter", **ids)
        raise ImagineError(REFUSAL)

    try:
        verdict = await classify_async(raw)
    except Exception:
        verdict = "classifier-error"
    if verdict:
        note(f"asset-input-{verdict}")
        _lock_on_block(user_id)
        _audit("refusal", category=verdict, **ids)
        raise ImagineError(REFUSAL)

    sent, digest = build_sent_prompt(raw)
    if not footer_is_last(sent):
        _audit("error", category="footer-order", footer_hash=digest, **ids)
        raise ImagineError("Safety rules could not be applied, so nothing was generated.")
    _audit("success", footer_hash=digest, **ids)
    return PreparedPrompt(
        user_prompt=raw,
        sent_prompt=sent,
        footer_hash=digest,
    )


def _run_ffmpeg(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def probe_duration(src: str) -> float | None:
    """Seconds, or None when ffprobe cannot give a real duration (fail closed)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not src:
        return None
    for entries in ("format=duration", "stream=duration"):
        probe = _run_ffmpeg(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", entries,
                "-of", "default=noprint_wrappers=1:nokey=1",
                src,
            ],
            timeout=20,
        )
        text = (probe.stdout or b"").decode("utf-8", "replace").strip()
        if not text or text.upper() == "N/A":
            continue
        # Multiple streams can print several lines; take the first parseable.
        for line in text.splitlines():
            line = line.strip()
            if not line or line.upper() == "N/A":
                continue
            try:
                value = float(line)
            except ValueError:
                continue
            if value > 0 and math.isfinite(value):
                return value
    return None


def _read_jpegs(folder: str, prefix: str = "frame-") -> list[bytes]:
    frames: list[bytes] = []
    names = sorted(
        name for name in os.listdir(folder)
        if name.startswith(prefix) and name.lower().endswith((".jpg", ".jpeg"))
    )
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            blob = fh.read()
        if blob:
            frames.append(blob)
    return frames


def extract_video_frames(data: bytes, mime: str = "video/mp4") -> list[bytes]:
    """JPEG frames at 4 fps in one pass, plus the last frame at duration-epsilon.

    Animated GIF and WebP yield every frame. Unknown duration or too few
    frames returns an empty list (fail closed). Uses real ffmpeg.
    """
    if not data:
        return []
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return []
    kind = (mime or "").lower()
    suffix = ".mp4"
    if "webm" in kind:
        suffix = ".webm"
    elif "quicktime" in kind or kind.endswith("mov") or "mov" in kind:
        suffix = ".mov"
    elif "gif" in kind:
        suffix = ".gif"
    elif "webp" in kind:
        suffix = ".webp"
    animated = "gif" in kind or "webp" in kind
    with tempfile.TemporaryDirectory(prefix="eche-frames-") as raw:
        src = os.path.join(raw, "src" + suffix)
        with open(src, "wb") as fh:
            fh.write(data)
        duration = probe_duration(src)
        if duration is None:
            return []
        pattern = os.path.join(raw, "frame-%04d.jpg")
        if animated:
            args = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-i", src,
                "-vsync", "0",
                "-f", "image2",
                "-y",
                pattern,
            ]
        else:
            args = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-i", src,
                "-vf", f"fps={FRAME_FPS}",
                "-f", "image2",
                "-y",
                pattern,
            ]
        result = _run_ffmpeg(args, timeout=60)
        if result.returncode != 0:
            return []
        frames = _read_jpegs(raw)
        last = os.path.join(raw, "last.jpg")
        last_result = _run_ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-sseof", f"-{LAST_FRAME_EPSILON}",
                "-i", src,
                "-frames:v", "1",
                "-update", "1",
                "-f", "image2",
                "-y",
                last,
            ],
            timeout=30,
        )
        if last_result.returncode == 0 and os.path.isfile(last):
            with open(last, "rb") as fh:
                blob = fh.read()
            if blob and (not frames or blob != frames[-1]):
                frames.append(blob)
        if not frames:
            return []
        if not animated:
            needed = max(1, math.ceil(duration * FRAME_FPS))
            if len(frames) < needed:
                return []
        return frames


def _image_part(data: bytes, mime: str) -> dict | None:
    if not data:
        return None
    kind = (mime or "image/jpeg").split(";")[0].strip() or "image/jpeg"
    if not kind.startswith("image/"):
        kind = "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{kind};base64,{encoded}"}}


def _is_animated_image(mime: str, data: bytes) -> bool:
    kind = (mime or "").lower()
    if "gif" in kind:
        return data.startswith((b"GIF87a", b"GIF89a"))
    if "webp" in kind:
        return b"ANIM" in data[:128] or b"ANMF" in data
    return False


async def _classify_image(
    data: bytes,
    mime: str,
    *,
    footer_hash: str,
    user_id=None,
    guild_id=None,
    channel_id=None,
    request_id: str = "",
    response_id: str = "",
) -> None:
    from core.blocklist import note
    from core.classifier import age_vision_async, classify_async
    from core.imagine import ImagineError

    ids = dict(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        footer_hash=footer_hash,
        request_id=request_id,
        response_id=response_id,
    )
    part = _image_part(data, mime)
    if part is None:
        _lock_on_block(user_id)
        _audit("refusal", category="unreadable-output", **ids)
        raise ImagineError(OUTPUT_UNCHECKED)
    try:
        verdict = await classify_async(OUTPUT_CLASSIFY_TEXT, images=[part])
    except Exception:
        verdict = "classifier-error"
    if verdict == "classifier-image-unsupported":
        note("asset-output-vision")
        _lock_on_block(user_id)
        _audit("refusal", category=verdict, **ids)
        raise ImagineError(VISION_UNAVAILABLE)
    if verdict:
        note(f"asset-output-{verdict}")
        _lock_on_block(user_id)
        _audit("refusal", category=verdict, **ids)
        raise ImagineError(OUTPUT_REFUSAL)
    try:
        age = await age_vision_async([part])
    except Exception:
        age = "age-vision-error"
    if age:
        note(f"asset-output-{age}")
        _lock_on_block(user_id)
        _audit("refusal", category=age, **ids)
        raise ImagineError(OUTPUT_REFUSAL)


async def check_generated_media(
    data: bytes,
    mime: str,
    *,
    footer_hash: str,
    user_id=None,
    guild_id=None,
    channel_id=None,
    request_id: str = "",
    response_id: str = "",
) -> None:
    """Classify one image, or every required frame. Fail closed. No retries."""
    from core.imagine import ImagineError

    ids = dict(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        footer_hash=footer_hash,
        request_id=request_id,
        response_id=response_id,
    )
    kind = (mime or "").lower()
    needs_frames = "video" in kind or _is_animated_image(mime, data)
    if needs_frames:
        frames = extract_video_frames(data, mime)
        if not frames:
            _lock_on_block(user_id)
            _audit("refusal", category="frame-extract", **ids)
            raise ImagineError(OUTPUT_UNCHECKED)
        for frame in frames:
            await _classify_image(
                frame,
                "image/jpeg",
                footer_hash=footer_hash,
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                request_id=request_id,
                response_id=response_id,
            )
        return
    await _classify_image(
        data,
        mime or "image/jpeg",
        footer_hash=footer_hash,
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        request_id=request_id,
        response_id=response_id,
    )
