"""
Local Entra-ID -> Bearer injection reverse proxy for GitHub Copilot CLI.

Listens on 127.0.0.1:8787 and forwards requests to the configured Azure AI
Foundry endpoint, replacing the (dummy) Authorization header from the CLI
with a freshly acquired Microsoft Entra ID bearer token.

Environment overrides (optional):
  PROXY_HOST           default 127.0.0.1
  PROXY_PORT           default 8787
  FOUNDRY_BASE_URL     default https://aif-ext-ketana-pe.cognitiveservices.azure.com
  ENTRA_SCOPE          default https://ai.azure.com/.default
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import aiohttp
from aiohttp import web
from azure.core.credentials import AccessToken
from azure.identity import DefaultAzureCredential

LOG = logging.getLogger("copilot-proxy")

PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8787"))
FOUNDRY_BASE_URL = os.environ.get(
    "FOUNDRY_BASE_URL",
    "https://aif-ext-ketana-pe.cognitiveservices.azure.com",
).rstrip("/")
ENTRA_SCOPE = os.environ.get("ENTRA_SCOPE", "https://ai.azure.com/.default")

# Hop-by-hop headers per RFC 7230 that must not be forwarded.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


class TokenCache:
    """Caches an Entra access token until 5 minutes before expiry."""

    def __init__(self, scope: str) -> None:
        self._scope = scope
        self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        self._token: Optional[AccessToken] = None
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        now = int(time.time())
        if self._token and self._token.expires_on - now > 300:
            return self._token.token
        async with self._lock:
            now = int(time.time())
            if self._token and self._token.expires_on - now > 300:
                return self._token.token
            LOG.info("Acquiring new Entra token for scope %s", self._scope)
            # azure-identity is sync; offload to thread to avoid blocking the loop.
            self._token = await asyncio.to_thread(self._credential.get_token, self._scope)
            ttl = self._token.expires_on - now
            LOG.info("Token acquired, valid for %d seconds", ttl)
            return self._token.token


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


async def handle(request: web.Request) -> web.StreamResponse:
    cache: TokenCache = request.app["token_cache"]
    session: aiohttp.ClientSession = request.app["session"]

    # Build upstream URL: preserve path and query string verbatim.
    upstream_url = f"{FOUNDRY_BASE_URL}{request.rel_url}"

    # Drop CLI auth header and inject Entra bearer.
    token = await cache.get()
    headers = _filter_headers(dict(request.headers))
    headers.pop("Authorization", None)
    headers.pop("api-key", None)
    headers["Authorization"] = f"Bearer {token}"

    body = await request.read() if request.can_read_body else None

    LOG.info("%s %s -> %s", request.method, request.rel_url, upstream_url)

    try:
        upstream = await session.request(
            request.method,
            upstream_url,
            headers=headers,
            data=body,
            allow_redirects=False,
        )
    except aiohttp.ClientError as exc:
        LOG.error("Upstream request failed: %s", exc)
        return web.json_response({"error": {"message": str(exc)}}, status=502)

    # Stream response back (supports SSE).
    response_headers = _filter_headers(dict(upstream.headers))
    response = web.StreamResponse(status=upstream.status, headers=response_headers)
    await response.prepare(request)
    try:
        async for chunk in upstream.content.iter_any():
            await response.write(chunk)
    finally:
        upstream.release()
    await response.write_eof()
    return response


async def on_startup(app: web.Application) -> None:
    app["session"] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=30),
        auto_decompress=False,
    )
    app["token_cache"] = TokenCache(ENTRA_SCOPE)
    # Warm up the token so the first request is fast and auth issues surface early.
    try:
        await app["token_cache"].get()
    except Exception as exc:  # noqa: BLE001
        LOG.error("Initial token acquisition failed: %s", exc)
        raise


async def on_cleanup(app: web.Application) -> None:
    await app["session"].close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{tail:.*}", handle)
    LOG.info("Listening on http://%s:%d -> %s", PROXY_HOST, PROXY_PORT, FOUNDRY_BASE_URL)
    LOG.info("Entra scope: %s", ENTRA_SCOPE)
    web.run_app(app, host=PROXY_HOST, port=PROXY_PORT, print=None)


if __name__ == "__main__":
    main()
