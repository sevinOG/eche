# core/asset_backend.py
# Small interface in front of the image/video provider.
# The only implemented backend is Grok Imagine (xAI). Imagen could be
# swapped in later by adding another class and pointing get_backend() at it.

from __future__ import annotations

from typing import Protocol

# Official xAI Imagine models. Edit / image-to-video / video-edit are not
# offered (source media is never sent to the provider).
API = "https://api.x.ai/v1"
IMAGE_MODEL = "grok-imagine-image-2.0"
VIDEO_MODEL = "grok-imagine-video-1.5-lite"


class AssetBackend(Protocol):
    """Request building and response parsing for one media provider."""

    name: str

    def api_base(self) -> str:
        ...

    def build_request(self, plan, prepared) -> tuple[str, dict]:
        ...

    def image_url(self, payload: dict) -> str:
        ...

    def video_start_id(self, payload: dict) -> str:
        ...

    def video_poll(self, payload: dict) -> tuple[str, str]:
        ...


class XaiImagineBackend:
    """Grok Imagine on api.x.ai. Host pinning stays in core.imagine."""

    name = "xai-imagine"

    def api_base(self) -> str:
        return API

    def build_request(self, plan, prepared) -> tuple[str, dict]:
        from core.imagine import ImagineError

        prompt = getattr(prepared, "sent_prompt", None) or ""
        if not prompt.strip():
            raise ImagineError("Safety rules could not be applied, so nothing was generated.")
        mode = getattr(plan, "mode", "") or "image"
        if mode in {"image_edit", "image_video", "video_edit"} or getattr(plan, "source", None) is not None:
            raise ImagineError(
                "Editing, image-to-video and video-edit are not available. "
                "Describe a new picture or video instead."
            )
        if mode == "image":
            body: dict = {"model": IMAGE_MODEL, "prompt": prompt}
            if getattr(plan, "aspect", ""):
                body["aspect_ratio"] = plan.aspect
            return "/images/generations", body
        if mode == "video":
            body = {
                "model": VIDEO_MODEL,
                "prompt": prompt,
                "duration": getattr(plan, "seconds", 6),
                "resolution": "480p",
            }
            if getattr(plan, "aspect", ""):
                body["aspect_ratio"] = plan.aspect
            return "/videos/generations", body
        raise ImagineError("Imagine makes a picture or a video.")

    def image_url(self, payload: dict) -> str:
        from core.imagine import image_url

        return image_url(payload)

    def video_start_id(self, payload: dict) -> str:
        from core.imagine import video_start_id

        return video_start_id(payload)

    def video_poll(self, payload: dict) -> tuple[str, str]:
        from core.imagine import video_poll

        return video_poll(payload)


def get_backend() -> AssetBackend:
    """The active provider. Always xAI Imagine in this build."""
    return XaiImagineBackend()
