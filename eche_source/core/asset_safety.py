# core/asset_safety.py
# The single choke point for ?asset and the asset tool.
#
# Every generation prompt is handled here, in order:
#   1. the full minors matcher (including bare boy/girl/kids)
#   2. the Llama Guard classifier on the user prompt
#   3. the hardcoded safety footer, appended last as explicit rules
#   4. an adult-only rewrite, plus a negative prompt for child features
#
# Generated images, and sampled video frames (first, last, and at least
# one per second), are classified with Llama Guard vision. The path fails
# closed on any error, timeout, missing key, or unreadable verdict.
# A blocked prompt is never retried. A blocked asset is never posted or
# written to memory. The only backend is Grok Imagine (xAI); there is no
# fallback generator.

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

ADULT_RULE = (
    "All characters are adults 21 years or older. "
    "Do not depict children, minors, or anyone who looks under 21."
)
NEGATIVE_PROMPT = (
    "child, children, minor, underage, kid, kids, boy, girl, toddler, infant, "
    "loli, shota, schoolchild, baby, teen, teenager, preteen, young child, "
    "childlike, child's body, under 21"
)
OUTPUT_CLASSIFY_TEXT = (
    "Classify this generated media for sexual or other content involving "
    "minors, child-coded characters, or any listed safety category."
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


@dataclass
class PreparedPrompt:
    user_prompt: str
    sent_prompt: str
    negative_prompt: str
    footer_hash: str


def asset_enabled() -> bool:
    """Owner kill switch. Blank or missing is OFF."""
    try:
        from core.secrets import public_value
        from core.tools import prompt_choice

        return prompt_choice(public_value("asset_enabled"), default=False)
    except Exception:
        return False


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


async def prepare_generation(
    prompt: str,
    *,
    user_id=None,
    guild_id=None,
    channel_id=None,
) -> PreparedPrompt:
    """Matcher, then classifier, then footer-last + adult rewrite. Fail closed."""
    from core.blocklist import REFUSAL, is_blocked, note
    from core.classifier import classify_async
    from core.guardrails import BYPASS_REFUSAL, asks_to_bypass_filter
    from core.imagine import ImagineError

    raw = (prompt or "").strip()
    ids = dict(user_id=user_id, guild_id=guild_id, channel_id=channel_id)

    if asks_to_bypass_filter(raw):
        note("asset prompt bypass")
        _audit("refusal", category="bypass", **ids)
        raise ImagineError(BYPASS_REFUSAL)
    if is_blocked(raw, user_id=user_id, channel_id=channel_id):
        note("asset prompt")
        _audit("refusal", category="matcher", **ids)
        raise ImagineError(REFUSAL)

    try:
        verdict = await classify_async(raw)
    except Exception:
        verdict = "classifier-error"
    if verdict:
        note(f"asset-input-{verdict}")
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
        negative_prompt=NEGATIVE_PROMPT,
        footer_hash=digest,
    )


def video_sample_times(duration: float) -> list[float]:
    """First frame, last frame, and at least one timestamp per second."""
    try:
        length = float(duration)
    except (TypeError, ValueError):
        length = 0.0
    if length <= 0:
        return [0.0]
    times = [0.0]
    step = 1.0
    t = step
    while t < length - 1e-6:
        times.append(round(t, 6))
        t += step
    if times[-1] < length - 1e-6:
        times.append(length)
    return times


def _run_ffmpeg(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def extract_video_frames(data: bytes, mime: str = "video/mp4") -> list[bytes]:
    """JPEG frames at the required timestamps. Empty means extraction failed."""
    if not data:
        return []
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return []
    suffix = ".mp4"
    if "webm" in (mime or ""):
        suffix = ".webm"
    elif "quicktime" in (mime or "") or "mov" in (mime or ""):
        suffix = ".mov"
    with tempfile.TemporaryDirectory(prefix="eche-frames-") as raw:
        src = os.path.join(raw, "src" + suffix)
        with open(src, "wb") as fh:
            fh.write(data)
        probe = _run_ffmpeg(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                src,
            ],
            timeout=20,
        )
        try:
            duration = float((probe.stdout or b"").decode("utf-8", "replace").strip() or "0")
        except ValueError:
            duration = 0.0
        frames: list[bytes] = []
        for i, ts in enumerate(video_sample_times(duration)):
            dest = os.path.join(raw, f"frame-{i:04d}.jpg")
            result = _run_ffmpeg(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel", "error",
                    "-ss", f"{ts:.3f}",
                    "-i", src,
                    "-frames:v", "1",
                    "-f", "image2",
                    "-y",
                    dest,
                ],
                timeout=20,
            )
            if result.returncode != 0 or not os.path.isfile(dest):
                return []
            with open(dest, "rb") as fh:
                blob = fh.read()
            if not blob:
                return []
            frames.append(blob)
        return frames


def _image_part(data: bytes, mime: str) -> dict | None:
    if not data:
        return None
    kind = (mime or "image/jpeg").split(";")[0].strip() or "image/jpeg"
    if not kind.startswith("image/"):
        kind = "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{kind};base64,{encoded}"}}


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
    from core.blocklist import REFUSAL, note
    from core.classifier import classify_async
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
        _audit("refusal", category="unreadable-output", **ids)
        raise ImagineError(OUTPUT_UNCHECKED)
    try:
        verdict = await classify_async(OUTPUT_CLASSIFY_TEXT, images=[part])
    except Exception:
        verdict = "classifier-error"
    if verdict == "classifier-image-unsupported":
        note("asset-output-vision")
        _audit("refusal", category=verdict, **ids)
        raise ImagineError(VISION_UNAVAILABLE)
    if verdict:
        note(f"asset-output-{verdict}")
        _audit("refusal", category=verdict, **ids)
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
    """Classify one image, or sampled video frames. Fail closed. No retries."""
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
    if "video" in kind:
        frames = extract_video_frames(data, mime)
        if not frames:
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
