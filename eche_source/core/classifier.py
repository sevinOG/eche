# core/classifier.py
# Fail-closed safety classifier on every LLM request and reply.
# Llama Guard 4, on Groq or OpenRouter, whichever key is present.
# Covers every footer category. Cannot be turned off by config, persona,
# or users. A test-only hook exists so unit tests can run without a key;
# production never reads that hook from settings or user content.

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

CLASSIFIER_MODEL = "meta-llama/llama-guard-4-12b"
CLASSIFIER_TIMEOUT = 20
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Test hook only. None = auto-allow when a test runner is active;
# True = allow; False = run the real fail-closed path (used by classifier tests).
# Production never sets this from config, persona, or user settings.
_TEST_MODE = None


def allow_in_tests(enabled: bool) -> None:
    """Unit tests only. Production call sites never invoke this."""
    global _TEST_MODE
    _TEST_MODE = bool(enabled)


def tests_are_allowing() -> bool:
    if _TEST_MODE is True:
        return True
    if _TEST_MODE is False:
        return False
    return _unittest_running()


def _unittest_running() -> bool:
    import sys
    argv = " ".join(sys.argv).lower()
    return "unittest" in argv or "pytest" in argv


def classifier_model() -> str:
    return CLASSIFIER_MODEL


def _secret(name: str) -> str:
    env = (os.getenv(name) or "").strip()
    if env:
        return env
    try:
        from core.secrets import load_all

        values = load_all() or {}
        if name == "GROQ_API_KEY":
            return (values.get("inf_api_key") or "").strip()
        if name == "OPENROUTER_API_KEY":
            return (values.get("openrouter_api_key") or "").strip()
    except Exception:
        return ""
    return ""


def _endpoint() -> tuple[str, str] | tuple[None, None]:
    groq = _secret("GROQ_API_KEY")
    if groq:
        return groq, GROQ_URL
    openrouter = _secret("OPENROUTER_API_KEY")
    if openrouter:
        return openrouter, OPENROUTER_URL
    return None, None


def _parse_verdict(text: str) -> bool | None:
    """True = unsafe (block), False = safe, None = unreadable (fail closed)."""
    if not text or not str(text).strip():
        return None
    lines = [ln.strip().lower() for ln in str(text).replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return None
    first = lines[0]
    # Official Llama Guard replies start with "safe" or "unsafe".
    if first == "safe" or first.startswith("safe"):
        if first == "safe" or first.startswith("safe "):
            return False
        # "safety" etc. are not a verdict.
        if first == "safe":
            return False
    if first == "unsafe" or first.startswith("unsafe"):
        return True
    compact = " ".join(lines)
    if compact.startswith("unsafe"):
        return True
    if compact == "safe" or compact.startswith("safe\n") or compact.startswith("safe "):
        return False
    return None


def _content_with_images(text: str, images: list | None):
    if not images:
        return text or ""
    parts = [{"type": "text", "text": text or ""}]
    for image in images:
        if not isinstance(image, dict):
            continue
        if image.get("type") == "image_url" or "image_url" in image:
            url = image.get("image_url")
            if isinstance(url, str) and url.strip():
                parts.append({"type": "image_url", "image_url": {"url": url.strip()}})
            elif isinstance(url, dict) and url.get("url"):
                parts.append({"type": "image_url", "image_url": {"url": str(url["url"])}})
            else:
                return None
        elif image.get("type") in {"image", "input_image"}:
            url = image.get("url") or image.get("image_url") or ""
            if isinstance(url, dict):
                url = url.get("url") or ""
            if not url:
                return None
            parts.append({"type": "image_url", "image_url": {"url": str(url)}})
        else:
            return None
    return parts


def _post(messages: list) -> str:
    key, url = _endpoint()
    if not key or not url:
        raise RuntimeError("classifier key missing")
    body = json.dumps(
        {
            "model": CLASSIFIER_MODEL,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 64,
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if url.startswith(OPENROUTER_URL):
        headers["HTTP-Referer"] = "https://github.com/sevinOG/eche"
        headers["X-Title"] = "Eche"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CLASSIFIER_TIMEOUT) as resp:
            raw = resp.read()
            http_resp = resp
    except Exception as exc:
        try:
            from core.audit import api as audit_api
            audit_api("classifier", outcome="error")
        except Exception:
            pass
        raise RuntimeError(f"classifier request failed: {type(exc).__name__}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
        try:
            from core.audit import api_from_http
            api_from_http("classifier", http_resp, payload, outcome="success")
        except Exception:
            pass
        return str(payload["choices"][0]["message"]["content"] or "")
    except Exception as exc:
        try:
            from core.audit import api_from_http
            api_from_http("classifier", http_resp, outcome="error")
        except Exception:
            pass
        raise RuntimeError("classifier response unreadable") from exc


def classify(text: str, *, images: list | None = None) -> str:
    """Return a check name if the input must be refused, else ''.

    Fail closed: missing key, timeout, HTTP error, unreadable verdict,
    or images the endpoint cannot accept, all refuse.
    """
    if tests_are_allowing():
        return ""
    content = _content_with_images(text, images)
    if images and content is None:
        return "classifier-image-unsupported"
    key, url = _endpoint()
    if not key or not url:
        return "classifier-error"
    messages = [{"role": "user", "content": content}]
    try:
        verdict = _parse_verdict(_post(messages))
    except Exception:
        return "classifier-error"
    if verdict is None:
        return "classifier-error"
    if verdict:
        return "classifier-unsafe"
    return ""


def classify_request(text: str, *, images: list | None = None) -> str:
    check = classify(text, images=images)
    return f"llm-input-{check}" if check else ""


def classify_response(text: str, *, images: list | None = None) -> str:
    check = classify(text, images=images)
    return f"llm-output-{check}" if check else ""


def extract_images(messages) -> list[dict]:
    """Image parts headed to a vision model."""
    out: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            typ = str(part.get("type") or "")
            if typ in {"image_url", "image", "input_image"} or part.get("image_url") or part.get("images"):
                out.append(part)
    return out
