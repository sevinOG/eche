# Shared test helpers for checker routing.
# Legacy-shaped settings keep older injection tests on Llama Guard 4 + 8B YES/NO
# request shapes. New routing tests should not use this patch.

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

LEGACY_SHAPED_SETTINGS = {
    "content_filter_provider": "cloud",
    "content_filter_model": "meta-llama/llama-guard-4-12b",
    "child_check_provider": "cloud",
    "child_check_model": "llama-3.1-8b-instant",
}


def patch_legacy_checker_settings(target="core.classifier._CHECKER_OVERRIDES"):
    """Pin the test-only checker override (production has no settings for it)."""
    return mock.patch.dict(
        target,
        {
            "content_filter": ("cloud", LEGACY_SHAPED_SETTINGS["content_filter_model"]),
            "child_check": ("cloud", LEGACY_SHAPED_SETTINGS["child_check_model"]),
        },
        clear=True,
    )


def patch_image_path_classifier():
    """Run the always-on image-path classifier on otherwise text history tests.

    Vision and the image-content check are stubbed as clear so the existing
    urlopen judges still exercise history quarantine and fail-closed text
    checks. The dummy image only triggers that path.
    """
    from core.classifier import CheckerSpec, _resolve_checker as _orig_resolve

    images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]

    async def _clear(*_a, **_k):
        return ""

    def _resolve(kind):
        if kind == "image_check":
            return CheckerSpec(
                kind="image_check",
                provider="cloud",
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                fmt="llama_guard",
                field="image_check_model",
                problem="",
            )
        return _orig_resolve(kind)

    return (
        mock.patch("core.classifier.extract_images", return_value=images),
        mock.patch("core.classifier._classify_images_async", side_effect=_clear),
        mock.patch("core.classifier.age_vision_async", side_effect=_clear),
        mock.patch("core.classifier._resolve_checker", side_effect=_resolve),
    )


def start_image_path_classifier():
    patches = patch_image_path_classifier()
    for patch in patches:
        patch.start()
    return patches


def stop_image_path_classifier(patches):
    for patch in reversed(patches):
        patch.stop()


@contextmanager
def image_path_classifier():
    patches = start_image_path_classifier()
    try:
        yield
    finally:
        stop_image_path_classifier(patches)
