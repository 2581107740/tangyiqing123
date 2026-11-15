"""Provider-neutral OpenAI-compatible text adapter for Laai API."""
import json
import os
from urllib import error, request

API_ROOT = ""
DEFAULT_MODEL = ""

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


def extract_chat_stream(raw: bytes) -> str:
    """Extract assistant text from Chat Completions SSE events."""
    pieces = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        event_str = line[5:].strip()
        if not event_str or event_str == "[DONE]":
            continue
        try:
            event = json.loads(event_str)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("error"):
            raise RuntimeError("Upstream Chat Completions stream reported failure")
        choices = event.get("choices", [])
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str):
                pieces.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        pieces.append(part["text"])
    if pieces:
        return "".join(pieces)
    raise RuntimeError("Upstream Chat Completions stream contained no text")


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


def query_laai(prompt: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be nonempty")
    if len(prompt) > 8000:
        raise ValueError("prompt too long (8000 character limit)")
    key = os.environ.get("LAAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LAAI_API_KEY is not configured on the server")
    root = os.environ.get("LAAI_BASE_URL", API_ROOT).rstrip("/")
    if not root.startswith("https://"):
        raise ValueError("LAAI_BASE_URL must be HTTPS")
    api_mode = os.environ.get("LAAI_API_MODE", "chat_completions").strip().lower()
    if api_mode == "chat_completions":
        endpoint = root + "/chat/completions"
        payload = {
            "model": os.environ.get("LAAI_MODEL", DEFAULT_MODEL),
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
    elif api_mode == "responses":
        endpoint = root + "/responses"
        payload = {
            "model": os.environ.get("LAAI_MODEL", DEFAULT_MODEL),
            "instructions": "Answer the user's request accurately and concisely.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "stream": False,
            "store": False,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 1024,
        }
    else:
        raise ValueError("LAAI_API_MODE must be responses or chat_completions")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(endpoint, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
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
        if "text/event-stream" in (response.headers.get("Content-Type") or "").lower() or raw.lstrip().startswith((b"event:", b"data:")):
            if api_mode == "chat_completions":
                return extract_chat_stream(raw)[:40000]
            return extract_stream(raw)[:40000]
        payload = json.loads(raw)
        if api_mode == "chat_completions":
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("Chat Completions response has no choices")
            msg = choices[0].get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, str) and content.strip():
                return content[:40000]
            if isinstance(content, list):
                parts = [v.get("text") for v in content if isinstance(v, dict) and isinstance(v.get("text"), str)]
                if parts:
                    return "".join(parts)[:40000]
            raise ValueError("Chat Completions response has no text content")
        return extract_text(payload)[:40000]
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Upstream API response did not contain usable text") from exc


def probe_model_catalog_on_startup() -> None:
    """Check upstream access safely without recording secrets or response bodies."""
    import logging
    log = logging.getLogger("uvicorn.error")
    key = os.environ.get("LAAI_API_KEY", "").strip()
    root = os.environ.get("LAAI_BASE_URL", API_ROOT).rstrip("/")
    if not key or not root.startswith("https://"):
        log.warning("LAAI_MODEL_CATALOG_CHECK configured=False")
        return
    req = request.Request(root + "/models", headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
    })
    try:
        with request.urlopen(req, timeout=12) as response:
            payload = response.read(300000)
            code = response.status
        try:
            model_data = json.loads(payload)
            ids = {item.get("id") for item in model_data.get("data", [])
                   if isinstance(item, dict)} if isinstance(model_data, dict) else set()
            log.warning("LAAI_MODEL_CATALOG_CHECK status=%d valid_model_list=%s requested_model_listed=%s",
                        code, bool(ids), os.environ.get("LAAI_MODEL", DEFAULT_MODEL) in ids)
        except (ValueError, TypeError, AttributeError):
            log.warning("LAAI_MODEL_CATALOG_CHECK status=%d response_json_parse_failed=True", code)
    except error.HTTPError as exc:
        log.warning("LAAI_MODEL_CATALOG_CHECK status=%d", exc.code)
    except Exception:
        log.warning("LAAI_MODEL_CATALOG_CHECK network_or_protocol_failure=True")


probe_model_catalog_on_startup()
