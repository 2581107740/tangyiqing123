"""Restricted adapter for a Responses-compatible text API."""
import json
import os
from urllib import error, request

API_ROOT = "https://api.doroai.app/v1"
DEFAULT_MODEL = "gpt-6.1-sol"

def extract_text(payload: dict) -> str:
    parts = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                txt = content.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
    if parts:
        return "\n".join(parts)
    fallback = payload.get("output_text")
    if isinstance(fallback, str) and fallback:
        return fallback
    raise ValueError("API returned no output_text. Check upstream model and response format.")

def extract_stream(raw: bytes) -> str:
    """Parse streamed Responses SSE. Ignore reasoning/tool data."""
    deltas = []
    final = None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if not value or value == "[DONE]":
            continue
        try:
            event = json.loads(value)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
            deltas.append(event["delta"])
        elif kind == "response.completed" and isinstance(event.get("response"), dict):
            final = event["response"]
        elif kind == "response.failed":
            raise RuntimeError("Upstream Responses stream reported failure")
    if deltas:
        return "".join(deltas)
    if final:
        return extract_text(final)
    raise RuntimeError("Upstream Responses stream contained no text output")


def error_category(data):
    """Only return an error classification, never upstream secrets or raw messages."""
    if not isinstance(data, dict):
        return "unexpected_error_format"
    err = data.get("error")
    bits = []
    for value in (data.get("message"), data.get("msg"), data.get("code"), err):
        if isinstance(value, str):
            bits.append(value)
        elif isinstance(value, dict):
            for k in ("message", "code", "type"):
                if isinstance(value.get(k), str):
                    bits.append(value[k])
    message = " ".join(bits).lower()
    categories = [
        ("instructions", "missing_or_invalid_instructions"),
        ("stream", "streaming_requirement_or_failure"),
        ("model", "model_permission_or_configuration"),
        ("余额", "insufficient_balance"),
        ("balance", "insufficient_balance"),
        ("quota", "quota_or_usage_limit"),
        ("rate limit", "rate_limited"),
        ("key", "api_key_restriction"),
        ("token", "token_permission_issue"),
        ("permission", "upstream_permission_denied"),
        ("forbidden", "upstream_permission_denied"),
        ("access", "upstream_access_restriction"),
        ("whitelist", "ip_or_client_allowlist"),
        ("ip ", "ip_or_client_allowlist"),
    ]
    for needle, category in categories:
        if needle in message:
            return category
    return "unclassified_json_error"


def query_doro(prompt: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be nonempty")
    if len(prompt) > 8000:
        raise ValueError("prompt too long (8000 character limit)")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured on the server")
    root = os.environ.get("DORO_BASE_URL", API_ROOT).rstrip("/")
    if not root.startswith("https://"):
        raise ValueError("DORO_BASE_URL must be HTTPS")
    endpoint = root + "/responses"
    body = json.dumps({
        "model": os.environ.get("DORO_MODEL", DEFAULT_MODEL),
        "instructions": "Answer the user's request accurately and concisely.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "stream": True,
        "store": False,
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(endpoint, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-openai-actor-authorization": "local-image-extension",
    })
    try:
        with request.urlopen(req, timeout=100) as response:
            raw = response.read(2_000_000)
    except error.HTTPError as exc:
        response_type = (exc.headers.get("Content-Type") or "").lower()
        if "json" in response_type:
            try:
                detail = json.loads(exc.read(4096))
                reason = error_category(detail)
            except (ValueError, TypeError):
                reason = "invalid_json_error"
            raise RuntimeError(f"Upstream API returned HTTP {exc.code}; reason_category={reason}") from None
        if "html" in response_type:
            raise RuntimeError(f"Upstream returned HTTP {exc.code} (HTML; possible gateway/WAF restriction)") from None
        raise RuntimeError(f"Upstream API returned HTTP {exc.code} (non-JSON response)") from None
    except error.URLError:
        raise RuntimeError("Upstream API is not reachable") from None
    try:
        if "text/event-stream" in (response.headers.get("Content-Type") or "").lower() or raw.lstrip().startswith(b"event:"):
            return extract_stream(raw)[:40000]
        payload = json.loads(raw)
        return extract_text(payload)[:40000]
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Upstream API response did not contain usable text") from exc
