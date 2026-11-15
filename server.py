"""ChatGPT MCP gateway with OAuth authorization code + PKCE login."""
import asyncio

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from bridge import query_muxue
from oauth_app import access_valid, base_url, oauth_ready, routes

mcp = FastMCP(
    "Muxue AI Text Tool",
    instructions=("Use this external model only if user explicitly requests it. "
                  "Do not forward private data, API keys, passwords or full histories."),
    host="0.0.0.0", stateless_http=True, json_response=True,
)

@mcp.tool()
async def ask_muxue(prompt: str) -> str:
    """Request one text answer from the external Muxue AI model on explicit instruction."""
    try:
        return await asyncio.to_thread(query_muxue, prompt)
    except Exception as exc:
        return "Muxue API request failed: " + str(exc)

class OAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.rstrip("/") == "/mcp":
            if not oauth_ready():
                return JSONResponse({"error": "setup_required"}, status_code=503)
            if not access_valid(request.headers.get("Authorization", "")):
                resource_meta = base_url() + "/.well-known/oauth-protected-resource/mcp"
                return JSONResponse({"error": "unauthorized"}, status_code=401, headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="' + resource_meta + '", scope="muxue:ask"',
                    "Cache-Control": "no-store",
                })
        return await call_next(request)

app = mcp.streamable_http_app()
app.routes.extend(routes())
app.add_middleware(OAuthMiddleware)
