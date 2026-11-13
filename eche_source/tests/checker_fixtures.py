# Shared test helpers for checker routing.
# Legacy-shaped settings keep older injection tests on Llama Guard 4 + 8B YES/NO
# request shapes. New routing tests should not use this patch.

from __future__ import annotations

from unittest import mock

LEGACY_SHAPED_SETTINGS = {
    "content_filter_provider": "cloud",
    "content_filter_model": "meta-llama/llama-guard-4-12b",
    "child_check_provider": "cloud",
    "child_check_model": "llama-3.1-8b-instant",
}


def patch_legacy_checker_settings(target="core.classifier._checker_setting"):
    return mock.patch(
        target,
        side_effect=lambda key: LEGACY_SHAPED_SETTINGS.get(key, ""),
    )
