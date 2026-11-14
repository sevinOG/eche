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
from dataclasses import dataclass, replace as _dc_replace

ADULT_RULE = (
    "All characters are adults 21 years or older. "
    "Do not depict children, minors, or anyone who looks under 21."
)
ADULT_RULE_STRICT = (
    "All characters are clearly mature adults, 25 years or older, with adult "
    "proportions, and look like a non-stylized, non-identifiable person. "
    "Do not depict children, minors, anyone who looks under 21, stylized, "
    "illustrated, anime, cartoon, or identifiable people."
)
MODE_CHANGED_REFUSAL = (
    "The asset safety settings changed during this request, so it was not posted."
)
CHANNEL_CHANGED_REFUSAL = (
    "18+ asset generation is only available in age-restricted channels."
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
SOURCE_REFUSAL = "The picture or video you replied to did not pass the safety check, so it was not edited."
SOURCE_UNCHECKED = "I could not check the file you replied to, so nothing was edited."
MEDIA_FILTER_REFUSAL = OUTPUT_REFUSAL

FRAME_FPS = 8
LAST_FRAME_EPSILON = 0.05


@dataclass
class PreparedPrompt:
    user_prompt: str
    sent_prompt: str
    footer_hash: str



# Explicit disable tokens for default-ON safety flags (approval, owner-only).
_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})
_ENABLE_TOKENS = frozenset({"1", "true", "yes", "on"})

_LAST_SEEN_MODE = None
_LAST_INVALID: set[str] = set()


def parse_safety_flag(stored, *, default: bool) -> tuple[bool, bool]:
    """Return (value, invalid).

    Default-ON flags (approval, owner-only) turn off only for an explicit
    0/false/no/off. Anything unrecognised keeps the safe default.
    Default-OFF flags turn on only for an explicit 1/true/yes/on.
    """
    text = str(stored if stored is not None else "").strip().lower()
    if not text:
        return default, False
    if text in _DISABLE_TOKENS:
        return False, False
    if text in _ENABLE_TOKENS:
        return True, False
    return default, True


@dataclass(frozen=True)
class AssetMode:
    """One immutable snapshot of the asset safety toggles for a request."""

    enabled: bool
    adult: bool
    approval: bool
    owner_only: bool
    user_cap: int
    guild_cap: int
    video: bool
    # Extra hourly video caps (user, guild). None means videos only count
    # toward user_cap / guild_cap. Set while approval is off.
    video_user_cap: int | None = None
    video_guild_cap: int | None = None


def _mode_from_store(store: dict | None, *, pins_ok: bool = True) -> tuple[AssetMode, list[tuple[str, str]]]:
    data = store or {}
    enabled, en_bad = parse_safety_flag(data.get("asset_enabled"), default=False)
    if not pins_ok:
        enabled = False
    adult, ad_bad = parse_safety_flag(data.get("asset_adult_enabled"), default=False)
    approval, ap_bad = parse_safety_flag(data.get("asset_approval_enabled"), default=True)
    owner, ow_bad = parse_safety_flag(data.get("asset_owner_only"), default=True)
    # 18+ and approval are independent. 18+ is not owner-only: any member of
    # an age-restricted channel may use it while the toggle is on, with or
    # without owner approval. Every minors check stays on either way.
    if not approval and not adult:
        # Approval off on the ordinary (non-18+) path stays owner-only.
        owner = True
    if approval:
        user_cap, guild_cap = 10, 30
        try:
            from core.asset_limits import GUILD_HOURLY_CAP, USER_HOURLY_CAP
            user_cap, guild_cap = USER_HOURLY_CAP, GUILD_HOURLY_CAP
        except Exception:
            pass
        video = True
        video_caps = (None, None)
    else:
        user_cap, guild_cap = APPROVAL_OFF_USER_CAP, APPROVAL_OFF_GUILD_CAP
        # Video is allowed with approval off (owner request 2026-11-13
        # 21:39 ET), under its own lower cap on top of the shared one.
        video = True
        video_caps = (APPROVAL_OFF_VIDEO_USER_CAP, APPROVAL_OFF_VIDEO_GUILD_CAP)
    invalid = []
    if en_bad:
        invalid.append(("asset_enabled", str(data.get("asset_enabled") or "")))
    if ad_bad:
        invalid.append(("asset_adult_enabled", str(data.get("asset_adult_enabled") or "")))
    if ap_bad:
        invalid.append(("asset_approval_enabled", str(data.get("asset_approval_enabled") or "")))
    if ow_bad:
        invalid.append(("asset_owner_only", str(data.get("asset_owner_only") or "")))
    mode = AssetMode(
        enabled=enabled,
        adult=adult,
        approval=approval,
        owner_only=owner,
        user_cap=user_cap,
        guild_cap=guild_cap,
        video=video,
        video_user_cap=video_caps[0],
        video_guild_cap=video_caps[1],
    )
    return mode, invalid


def _notify_mode_change(previous: AssetMode, current: AssetMode) -> None:
    lines = ["An ?asset safety toggle changed."]
    if previous.adult != current.adult:
        lines.append(f"18+ mode changed from {int(previous.adult)} to {int(current.adult)}.")
    if previous.approval != current.approval:
        lines.append(f"Owner approval changed from {int(previous.approval)} to {int(current.approval)}.")
    if previous.owner_only != current.owner_only:
        lines.append(f"Owner only changed from {int(previous.owner_only)} to {int(current.owner_only)}.")
    if previous.enabled != current.enabled:
        lines.append(f"Asset generation changed from {int(previous.enabled)} to {int(current.enabled)}.")
    try:
        from core.audit import record
        record(
            "asset-toggles",
            outcome="success",
            adult_old="1" if previous.adult else "0",
            adult_new="1" if current.adult else "0",
            approval_old="1" if previous.approval else "0",
            approval_new="1" if current.approval else "0",
            owner_only_old="1" if previous.owner_only else "0",
            owner_only_new="1" if current.owner_only else "0",
            enabled_old="1" if previous.enabled else "0",
            enabled_new="1" if current.enabled else "0",
        )
    except Exception:
        pass
    try:
        from core.secrets import _notify_asset_toggle_owners
        _notify_asset_toggle_owners(lines)
    except Exception:
        pass


def _note_invalid_settings(invalid: list[tuple[str, str]]) -> None:
    global _LAST_INVALID
    current = {key for key, _raw in invalid}
    for key, raw in invalid:
        if key in _LAST_INVALID:
            continue
        try:
            from core.audit import record
            record("settings-invalid", outcome="error", key=key, value=raw, safety=True)
        except Exception:
            pass
    _LAST_INVALID = current


def observe_asset_mode(mode: AssetMode) -> AssetMode:
    """Compare to the last mode the bot saw. Audit and DM owners on a change."""
    global _LAST_SEEN_MODE
    previous = _LAST_SEEN_MODE
    _LAST_SEEN_MODE = mode
    if previous is not None and previous != mode:
        _notify_mode_change(previous, mode)
    return mode


def reset_observed_asset_mode(mode: AssetMode | None = None) -> None:
    """Tests only: clear or seed the last-seen snapshot."""
    global _LAST_SEEN_MODE, _LAST_INVALID
    _LAST_SEEN_MODE = mode
    _LAST_INVALID = set()


def read_asset_mode(root: str | None = None, *, observe: bool = True) -> AssetMode:
    """Read one immutable snapshot from settings.json."""
    pins_ok = True
    try:
        from core.media_scan import official_pins_ok
        pins_ok = not official_pins_ok()
    except Exception:
        pins_ok = False
    store: dict = {}
    try:
        from core.secrets import load_all
        store = load_all(root) or {}
    except Exception:
        store = {}
    mode, invalid = _mode_from_store(store, pins_ok=pins_ok)
    if invalid:
        _note_invalid_settings(invalid)
    if observe:
        observe_asset_mode(mode)
    return mode


def confirm_asset_delivery(
    mode: AssetMode,
    channel=None,
    *,
    nsfw: bool | None = None,
    user_id=None,
    guild_id=None,
    channel_id=None,
    request_id: str = "",
    response_id: str = "",
) -> AssetMode:
    """Re-read toggles and the NSFW gate before a review DM or a post. Abort on change."""
    from core.imagine import ImagineError

    ids = dict(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        request_id=request_id,
        response_id=response_id,
    )
    stored = read_asset_mode()
    if mode.adult and not channel_is_age_restricted(channel, nsfw=nsfw):
        _audit("refusal", category="adult-channel-changed", mode=stored, **ids)
        raise ImagineError(CHANNEL_CHANGED_REFUSAL)
    latest = mode_for_channel(stored, channel, nsfw=nsfw)
    if latest != mode:
        _audit("refusal", category="asset-mode-changed", mode=latest, **ids)
        raise ImagineError(MODE_CHANGED_REFUSAL)
    return latest


def _live_mode() -> AssetMode:
    """Best-effort flags from public_value, for call sites without a snapshot."""
    pins_ok = True
    try:
        from core.media_scan import official_pins_ok
        pins_ok = not official_pins_ok()
    except Exception:
        pins_ok = False
    store = {}
    try:
        from core.secrets import public_value
        for key in (
            "asset_enabled",
            "asset_adult_enabled",
            "asset_approval_enabled",
            "asset_owner_only",
        ):
            store[key] = public_value(key)
    except Exception:
        store = {}
    mode, _invalid = _mode_from_store(store, pins_ok=pins_ok)
    return mode


def asset_enabled(mode: AssetMode | None = None) -> bool:
    """Owner kill switch. Blank or missing is OFF.

    Any official pin failure also returns False, so the LLM asset tool
    (which already consults this function) is disabled with ?asset.
    """
    if mode is not None:
        return mode.enabled
    return _live_mode().enabled


def asset_adult_enabled(mode: AssetMode | None = None) -> bool:
    """18+ mode. Settings-only, default off. Blank or missing is OFF."""
    if mode is not None:
        return mode.adult
    return _live_mode().adult


def asset_approval_stored() -> bool:
    """Stored approval toggle. Blank or missing is ON."""
    try:
        from core.secrets import public_value
        value, _invalid = parse_safety_flag(public_value("asset_approval_enabled"), default=True)
        return value
    except Exception:
        return True


def asset_approval_enabled(mode: AssetMode | None = None) -> bool:
    """Effective approval flag. 18+ does not change it."""
    if mode is not None:
        return mode.approval
    return _live_mode().approval


def approval_required(mode: AssetMode | None = None) -> bool:
    return asset_approval_enabled(mode)


def owner_only_required(mode: AssetMode | None = None) -> bool:
    """Owner-only flag. Approval-off without 18+ forces it; 18+ never does."""
    if mode is not None:
        return mode.owner_only
    return _live_mode().owner_only


# Lower caps while approval is off, with or without 18+. Videos count
# toward these and also toward their own lower hourly cap.
APPROVAL_OFF_USER_CAP = 3
APPROVAL_OFF_GUILD_CAP = 10
APPROVAL_OFF_VIDEO_USER_CAP = 1
APPROVAL_OFF_VIDEO_GUILD_CAP = 3


def video_caps(mode: AssetMode | None = None) -> tuple[int, int] | None:
    """Extra (user, guild) hourly video caps, or None when there are none."""
    snap = mode if mode is not None else _live_mode()
    if snap.video_user_cap is None and snap.video_guild_cap is None:
        return None
    return (
        snap.video_user_cap if snap.video_user_cap is not None else snap.user_cap,
        snap.video_guild_cap if snap.video_guild_cap is not None else snap.guild_cap,
    )


def hourly_caps(mode: AssetMode | None = None) -> tuple[int, int]:
    snap = mode if mode is not None else _live_mode()
    return snap.user_cap, snap.guild_cap


def video_allowed(mode: AssetMode | None = None) -> bool:
    if mode is not None:
        return mode.video
    return _live_mode().video


def channel_is_age_restricted(channel=None, *, nsfw: bool | None = None) -> bool:
    """True only when Discord marks the channel as age-restricted."""
    if nsfw is not None:
        return bool(nsfw)
    if channel is None:
        return False
    fn = getattr(channel, "is_nsfw", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            pass
    return bool(getattr(channel, "nsfw", False))


def mode_for_channel(mode: AssetMode, channel=None, *, nsfw: bool | None = None) -> AssetMode:
    """18+ rules apply only in age-restricted channels.

    With the 18+ toggle on, an ordinary (non-age-restricted) channel is not
    refused: the request runs under the normal SFW rules (no S12 relaxing,
    the normal age check). Audience, approval, caps, and video are unchanged.
    """
    if mode.adult and not channel_is_age_restricted(channel, nsfw=nsfw):
        return _dc_replace(mode, adult=False)
    return mode


# Consensual adult sexual content. Never ignore S4 (child sexual exploitation)
# or any other Llama Guard category.
ADULT_OUTPUT_IGNORE_LG4 = frozenset({"S12"})


def adult_output_ignore_lg4(mode: AssetMode | None = None) -> frozenset[str] | None:
    """S12 is ignored on generated-media checks only, and only in 18+ mode."""
    if (mode.adult if mode is not None else asset_adult_enabled()):
        return ADULT_OUTPUT_IGNORE_LG4
    return None


def require_enabled(mode: AssetMode | None = None, **ids) -> None:
    """Re-check the kill switch. Used before each poll and before posting."""
    from core.imagine import ImagineError

    if mode is not None and not mode.enabled:
        _audit(
            "refusal",
            category="kill-switch",
            mode=mode,
            user_id=ids.get("user_id"),
            guild_id=ids.get("guild_id"),
            channel_id=ids.get("channel_id"),
            request_id=ids.get("request_id") or "",
            response_id=ids.get("response_id") or "",
        )
        raise ImagineError(KILL_SWITCH_OFF)
    if not asset_enabled():
        _audit(
            "refusal",
            category="kill-switch",
            user_id=ids.get("user_id"),
            guild_id=ids.get("guild_id"),
            channel_id=ids.get("channel_id"),
            request_id=ids.get("request_id") or "",
            response_id=ids.get("response_id") or "",
        )
        raise ImagineError(KILL_SWITCH_OFF)


def footer_is_last(sent: str) -> bool:
    from core.safety import SAFETY_FOOTER

    text = sent or ""
    return text.endswith(SAFETY_FOOTER) and text.count(SAFETY_FOOTER) == 1


def build_sent_prompt(user_prompt: str, *, adult: bool = False) -> tuple[str, str]:
    """Adult-only rewrite, then the user prompt, then the footer last."""
    from core.safety import SAFETY_FOOTER, footer_sha256

    body = (user_prompt or "").strip()
    # Cap applies to the user prompt only. The footer must still come last.
    if len(body) > 2000:
        body = body[:2000].rstrip()
    rule = ADULT_RULE_STRICT if adult else ADULT_RULE
    sent = f"{rule}\n\n{body}\n\n{SAFETY_FOOTER}"
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
    mode: AssetMode | None = None,
) -> None:
    try:
        from core.audit import record
        from core.safety import footer_sha256

        digest = footer_hash or footer_sha256()
        safety = outcome in {"refusal", "error", "blocked"} or bool(category)
        snap = mode
        # Never include the prompt text, especially on blocks.
        fields = dict(
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
        if snap is not None:
            fields["adult"] = "1" if snap.adult else "0"
            fields["approval"] = "1" if snap.approval else "0"
        record("asset", **fields)
    except Exception:
        pass


_INFRA_NO_LOCKOUT = frozenset({
    "classifier-error",
    "child-check-error",
    "age-vision-error",
    "frame-extract",
    "unreadable-output",
    "classifier-image-unsupported",
    "footer-order",
})


def _lock_on_block(user_id, category: str = "") -> None:
    """24h lockout after a safety block, not after an infrastructure error."""
    if (category or "").endswith("-error") or category in _INFRA_NO_LOCKOUT:
        return
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
    mode: AssetMode | None = None,
) -> PreparedPrompt:
    """Matcher, media filter, classifier (incl. child check), footer-last. Fail closed."""
    from core.asset_filter import media_prompt_blocked
    from core.blocklist import REFUSAL, is_blocked, note
    from core.classifier import classify_async
    from core.guardrails import BYPASS_REFUSAL, asks_to_bypass_filter
    from core.imagine import ImagineError

    raw = (prompt or "").strip()
    snap = mode if mode is not None else read_asset_mode()
    ids = dict(user_id=user_id, guild_id=guild_id, channel_id=channel_id)

    if asks_to_bypass_filter(raw):
        note("asset prompt bypass")
        _lock_on_block(user_id, "bypass")
        _audit("refusal", category="bypass", mode=snap, **ids)
        raise ImagineError(BYPASS_REFUSAL)
    if is_blocked(raw, user_id=user_id, channel_id=channel_id):
        note("asset prompt")
        _lock_on_block(user_id, "matcher")
        _audit("refusal", category="matcher", mode=snap, **ids)
        raise ImagineError(REFUSAL)
    if media_prompt_blocked(raw, adult=snap.adult):
        note("asset prompt media-filter")
        from core.asset_limits import note_media_filter_block

        if note_media_filter_block(user_id):
            _lock_on_block(user_id, "media-filter")
        _audit("refusal", category="media-filter", mode=snap, **ids)
        raise ImagineError(REFUSAL)

    try:
        verdict = await classify_async(raw)
    except Exception:
        verdict = "classifier-error"
    if verdict:
        note(f"asset-input-{verdict}")
        _lock_on_block(user_id, verdict)
        _audit("refusal", category=verdict, mode=snap, **ids)
        raise ImagineError(REFUSAL)

    sent, digest = build_sent_prompt(raw, adult=snap.adult)
    if not footer_is_last(sent):
        _audit("error", category="footer-order", footer_hash=digest, mode=snap, **ids)
        raise ImagineError("Safety rules could not be applied, so nothing was generated.")
    _audit("success", footer_hash=digest, mode=snap, **ids)
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
        fps_count = len(frames)
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
        last_ok = False
        if last_result.returncode == 0 and os.path.isfile(last):
            with open(last, "rb") as fh:
                blob = fh.read()
            if blob:
                last_ok = True
                # Keep a unique last frame for the classifier, but do not
                # let a byte-identical last frame shrink the fps count.
                if not frames or blob != frames[-1]:
                    frames.append(blob)
        if not frames:
            return []
        if not animated:
            needed = max(1, math.floor(duration * FRAME_FPS))
            if fps_count < needed or not last_ok:
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
    mode: AssetMode | None = None,
    stage: str = "output",
) -> None:
    from core.blocklist import note
    from core.classifier import age_vision_async, classify_media_async
    from core.imagine import ImagineError

    snap = mode if mode is not None else read_asset_mode(observe=False)
    ids = dict(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        footer_hash=footer_hash,
        request_id=request_id,
        response_id=response_id,
        mode=snap,
    )
    source = stage == "source"
    refused = SOURCE_REFUSAL if source else OUTPUT_REFUSAL
    unchecked = SOURCE_UNCHECKED if source else OUTPUT_UNCHECKED
    tag = "source-" if source else ""
    part = _image_part(data, mime)
    if part is None:
        _audit("refusal", category=f"unreadable-{stage}", **ids)
        raise ImagineError(unchecked)
    # Pixels only. The text checkers (Llama Guard 3 is text-only) must not
    # judge a fixed instruction string: it names minors, so it was flagged
    # S1/S4 on every finished image, even a car.
    try:
        verdict = await classify_media_async(
            [part],
            ignore_lg4=adult_output_ignore_lg4(snap),
        )
    except Exception:
        verdict = "classifier-error"
    if verdict == "classifier-image-unsupported":
        note(f"asset-{stage}-vision")
        _audit("refusal", category=tag + verdict, **ids)
        raise ImagineError(VISION_UNAVAILABLE)
    if verdict:
        note(f"asset-{stage}-{verdict}")
        _audit("refusal", category=tag + verdict, **ids)
        raise ImagineError(refused)
    try:
        age = await age_vision_async([part], adult=snap.adult)
    except Exception:
        age = "age-vision-error"
    if age:
        note(f"asset-{stage}-{age}")
        _audit("refusal", category=tag + age, **ids)
        raise ImagineError(refused)


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
    mode: AssetMode | None = None,
    stage: str = "output",
) -> None:
    """Classify one image, or every required frame. Fail closed. No retries."""
    from core.imagine import ImagineError

    snap = mode if mode is not None else read_asset_mode(observe=False)
    ids = dict(
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        footer_hash=footer_hash,
        request_id=request_id,
        response_id=response_id,
        mode=snap,
    )
    kind = (mime or "").lower()
    needs_frames = "video" in kind or _is_animated_image(mime, data)
    if needs_frames:
        frames = extract_video_frames(data, mime)
        if not frames:
            _audit("refusal", category="frame-extract" if stage == "output" else f"{stage}-frame-extract", **ids)
            raise ImagineError(SOURCE_UNCHECKED if stage == "source" else OUTPUT_UNCHECKED)
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
                mode=snap,
                stage=stage,
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
        mode=snap,
        stage=stage,
    )


async def check_source_media(
    data: bytes,
    mime: str,
    *,
    footer_hash: str,
    user_id=None,
    guild_id=None,
    channel_id=None,
    mode: AssetMode | None = None,
) -> None:
    """The same hardcoded checks as a finished file, on an edit source.

    Runs before the source is sent anywhere. Every frame of a video is
    checked. Fails closed. A source block does not lock the requester:
    they may have replied to someone else's post.
    """
    await check_generated_media(
        data,
        mime,
        footer_hash=footer_hash,
        user_id=user_id,
        guild_id=guild_id,
        channel_id=channel_id,
        mode=mode,
        stage="source",
    )
