"""Single-owner OAuth authorization code + PKCE for a private MCP bridge.

Only for personal use. Never store provider keys or login passwords in GitHub.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

def base_url():
    value = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    return value if value.startswith("https://") and urlparse(value).hostname else ""

def oauth_ready():
    key = os.getenv("OPENAI_API_KEY", "")
    password = os.getenv("OAUTH_LOGIN_PASSWORD", "")
    return len(key) >= 24 and len(password) >= 16 and bool(base_url())

# Report only pass/fail flags to private Render service logs. Never log values.
import logging
logging.getLogger("uvicorn.error").warning(
    "DORO_CONFIG_CHECK api_key_length_ok=%s oauth_password_length_ok=%s https_base_url_ok=%s",
    len(os.getenv("OPENAI_API_KEY", "")) >= 24,
    len(os.getenv("OAUTH_LOGIN_PASSWORD", "")) >= 16,
    bool(base_url()),
)

def key_bytes():
    # Distinct signing key derived using a domain-specific HMAC label.
    key = os.environ.get("OPENAI_API_KEY", "")
    if len(key) < 24:
        raise ValueError("not configured")
    return hmac.new(key.encode(), b"doro-mcp-oauth-signatures-v1", hashlib.sha256).digest()

def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def unb64(value):
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))

def sign(payload):
    blob = b64(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    mac = b64(hmac.new(key_bytes(), blob.encode(), hashlib.sha256).digest())
    return blob + "." + mac

def verify(value, kind, max_bytes=8192):
    if not isinstance(value, str) or len(value) > max_bytes:
        return None
    try:
        blob, mac = value.split(".", 1)
        expected = b64(hmac.new(key_bytes(), blob.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, mac):
            return None
        data = json.loads(unb64(blob))
        if data.get("kind") != kind or data.get("exp", 0) <= int(time.time()):
            return None
        return data
    except (ValueError, TypeError, KeyError):
        return None

def valid_uri(uri):
    if not isinstance(uri, str) or len(uri) > 1200:
        return False
    try:
        u = urlparse(uri)
        return bool(u.hostname and u.username is None and u.password is None and not u.fragment and
                    (u.scheme == "https" or (u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1", "::1"})))
    except ValueError:
        return False

def problem(message, status=400):
    return JSONResponse({"error": message}, status_code=status, headers={"Cache-Control": "no-store"})

def not_ready():
    return problem("Server administrator must configure OPENAI_API_KEY and OAUTH_LOGIN_PASSWORD (16+ characters)", 503)

def issuer():
    return base_url()

async def health(request: Request):
    return JSONResponse({"status": "ready" if oauth_ready() else "setup_required",
                         "type": "doro-oauth-mcp"}, headers={"Cache-Control": "no-store"})

async def oauth_metadata(request: Request):
    if not base_url():
        return not_ready()
    root = issuer()
    return JSONResponse({
        "issuer": root,
        "authorization_endpoint": root + "/authorize",
        "token_endpoint": root + "/token",
        "registration_endpoint": root + "/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["doro:ask"],
        "client_id_metadata_document_supported": False,
    }, headers={"Cache-Control": "no-store"})

async def resource_metadata(request: Request):
    if not base_url():
        return not_ready()
    return JSONResponse({
        "resource": issuer() + "/mcp",
        "authorization_servers": [issuer()],
        "scopes_supported": ["doro:ask"],
        "bearer_methods_supported": ["header"],
    }, headers={"Cache-Control": "no-store"})

async def register(request: Request):
    if not oauth_ready():
        return not_ready()
    try:
        data = await request.json()
    except Exception:
        return problem("invalid_client_metadata")
    if not isinstance(data, dict):
        return problem("invalid_client_metadata")
    uris = data.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 8 or any(not valid_uri(u) for u in uris):
        return problem("invalid_redirect_uri")
    if data.get("token_endpoint_auth_method", "none") != "none":
        return problem("unsupported_token_endpoint_auth_method")
    client_id = "mcpclient_" + sign({"kind": "client", "redirect_uris": uris, "exp": int(time.time()) + 365 * 86400})
    return JSONResponse({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "token_endpoint_auth_method": "none",
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, status_code=201, headers={"Cache-Control": "no-store"})

def authorization_data(q):
    client_id = q.get("client_id", "")
    client = verify(client_id.removeprefix("mcpclient_"), "client") if client_id.startswith("mcpclient_") else None
    redirect = q.get("redirect_uri", "")
    challenge = q.get("code_challenge", "")
    if (not client or redirect not in client.get("redirect_uris", [])
        or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256"
        or not re.fullmatch(r"[a-zA-Z0-9_-]{43,128}", challenge)
        or q.get("scope", "doro:ask") not in ("", "doro:ask")
        or (q.get("resource") and q.get("resource").rstrip("/") != issuer() + "/mcp")):
        return None
    return {"kind": "pending", "client_id": client_id, "redirect_uri": redirect,
            "challenge": challenge, "state": q.get("state", ""),
            "exp": int(time.time()) + 300}

async def authorize(request: Request):
    if not oauth_ready():
        return not_ready()
    data = authorization_data(dict(request.query_params))
    if not data:
        return problem("invalid_request")
    pending = sign(data)
    return HTMLResponse("""<!doctype html><html lang='zh'><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Doro MCP 授权</title><style>body{font:17px system-ui;background:#12151b;color:#fff;max-width:440px;
margin:40px auto;padding:18px}input,button{box-sizing:border-box;width:100%;padding:14px;margin:12px 0;
font:inherit;border-radius:8px}button{background:#16a085;color:white;border:0}</style>
<h2>授权访问Doro AI</h2><p>输入你自己在 Render 中设置的 MCP 登录密码。</p>
<p>授权后该客户端可以将提交给 ask_doro 的文本发送给第三方 API，并可能产生费用。</p>
<form method='POST' action='/login'><input type='hidden' name='pending' value='""" + html.escape(pending, quote=True) + """'>
<input type='password' name='password' required autocomplete='current-password' placeholder='MCP 登录密码'>
<button type='submit'>登录并授权</button></form></html>""", headers={"Cache-Control": "no-store"})

_failed_attempts = {}
_redeemed_codes = {}

async def login(request: Request):
    if not oauth_ready():
        return not_ready()
    body = await request.body()
    if len(body) > 15000:
        return problem("invalid_request")
    vals = parse_qs(body.decode("utf-8", "replace"), max_num_fields=8)
    pending = verify(vals.get("pending", [""])[0], "pending")
    if not pending:
        return problem("authorization request expired; please try again")
    remote = request.client.host if request.client else "unknown"
    now = int(time.time())
    hits = [t for t in _failed_attempts.get(remote, []) if t >= now - 900]
    if len(hits) >= 8:
        return problem("too_many_attempts", 429)
    supplied = vals.get("password", [""])[0]
    expected = os.environ.get("OAUTH_LOGIN_PASSWORD", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        _failed_attempts[remote] = hits + [now]
        return problem("invalid_password", 401)
    _failed_attempts.pop(remote, None)
    code = sign({"kind": "code", "client_id": pending["client_id"],
                 "redirect_uri": pending["redirect_uri"], "challenge": pending["challenge"],
                 "jti": secrets.token_hex(16), "exp": now + 90})
    p = {"code": code}
    if pending["state"]:
        p["state"] = pending["state"]
    sep = "&" if "?" in pending["redirect_uri"] else "?"
    return RedirectResponse(pending["redirect_uri"] + sep + urlencode(p), status_code=302,
                            headers={"Cache-Control": "no-store"})

def issue_tokens(client_id):
    now = int(time.time())
    t = sign({"kind": "access", "client_id": client_id, "scope": "doro:ask",
              "aud": issuer() + "/mcp", "exp": now + 3600})
    r = sign({"kind": "refresh", "client_id": client_id, "scope": "doro:ask", "exp": now + 30 * 86400})
    return JSONResponse({"access_token": t, "token_type": "Bearer", "expires_in": 3600,
                         "refresh_token": r, "scope": "doro:ask"}, headers={"Cache-Control": "no-store"})

async def token(request: Request):
    if not oauth_ready():
        return not_ready()
    body = await request.body()
    if len(body) > 18000:
        return problem("invalid_request")
    vals = parse_qs(body.decode("utf-8", "replace"), max_num_fields=12)
    def get(key): return vals.get(key, [""])[0]
    grant = get("grant_type")
    if grant == "refresh_token":
        old = verify(get("refresh_token"), "refresh")
        if not old or old.get("client_id") != get("client_id"):
            return problem("invalid_grant", 400)
        return issue_tokens(old["client_id"])
    if grant != "authorization_code":
        return problem("unsupported_grant_type", 400)
    data = verify(get("code"), "code")
    if (not data or get("client_id") != data.get("client_id")
        or get("redirect_uri") != data.get("redirect_uri")):
        return problem("invalid_grant", 400)
    verifier = get("code_verifier")
    if not re.fullmatch(r"[a-zA-Z0-9~._-]{43,128}", verifier):
        return problem("invalid_grant", 400)
    actual = b64(hashlib.sha256(verifier.encode()).digest())
    if not hmac.compare_digest(actual, data["challenge"]):
        return problem("invalid_grant", 400)
    now = int(time.time())
    for k, exp in list(_redeemed_codes.items()):
        if exp <= now:
            _redeemed_codes.pop(k, None)
    if data["jti"] in _redeemed_codes:
        return problem("invalid_grant", 400)
    _redeemed_codes[data["jti"]] = data["exp"]
    return issue_tokens(data["client_id"])

def access_valid(value):
    if not oauth_ready() or not value.startswith("Bearer "):
        return False
    payload = verify(value[7:], "access")
    return bool(payload and payload.get("aud") == issuer() + "/mcp"
                and payload.get("scope") == "doro:ask")

def routes():
    return [
        Route("/", health, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", oauth_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", resource_metadata, methods=["GET"]),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/token", token, methods=["POST"]),
    ]
