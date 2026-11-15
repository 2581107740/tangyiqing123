"""Restricted adapter for a Responses-compatible text API."""
import json
import os
from urllib import error, request

API_ROOT = "https://v2.muxueai.pro"
DEFAULT_MODEL = "gpt-5.6-sol"

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

def query_muxue(prompt: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be nonempty")
    if len(prompt) > 8000:
        raise ValueError("prompt too long (8000 character limit)")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured on the server")
    root = os.environ.get("MUXUE_BASE_URL", API_ROOT).rstrip("/")
    if not root.startswith("https://"):
        raise ValueError("MUXUE_BASE_URL must be HTTPS")
    endpoint = root + "/responses"
    body = json.dumps({
        "model": os.environ.get("MUXUE_MODEL", DEFAULT_MODEL),
        "input": prompt,
        "store": False,
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(endpoint, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with request.urlopen(req, timeout=100) as response:
            raw = response.read(2_000_000)
    except error.HTTPError as exc:
        raise RuntimeError(f"Upstream API returned HTTP {exc.code}") from None
    except error.URLError:
        raise RuntimeError("Upstream API is not reachable") from None
    try:
        payload = json.loads(raw)
        return extract_text(payload)[:40000]
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Upstream API response did not contain usable text") from exc
