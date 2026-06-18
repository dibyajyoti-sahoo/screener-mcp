"""
HTTP client for Screener.in.

Credential model
----------------
Credentials can come from two places depending on the transport:

* ``stdio``  — read once from the ``SCREENER_USERNAME`` / ``SCREENER_PASSWORD``
  environment variables.
* ``sse``    — supplied *per connection* by the client when it opens ``/sse``
  (via request headers or query params). The SSE handler stores them in the
  ``current_credentials`` context variable for the lifetime of that session.

Each distinct ``(username, password)`` pair gets its own :class:`ScreenerClient`
(its own cookie jar + login state), kept in a small in-process pool so repeated
tool calls on the same connection reuse one authenticated session. Anonymous
("public mode") access uses a shared, credential-less client.

Authentication flow per client
-------------------------------
1. Fetch the login page and extract the CSRF token.
2. POST credentials; a successful login redirects away from ``/login/``.
3. The session cookie is reused for subsequent requests.
4. If a session silently expires mid-use, one re-login is attempted
   automatically before surfacing a ``PermissionError``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.screener.in"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 2               # network-level retries for transient errors
RETRY_BACKOFF = 0.5           # seconds, multiplied by attempt number
MAX_POOLED_CLIENTS = 64       # safety cap on distinct credential sessions

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}
JSON_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass(frozen=True)
class Credentials:
    """Screener.in login credentials. Empty fields mean anonymous access."""

    username: str = ""
    password: str = ""

    @property
    def is_anonymous(self) -> bool:
        return not (self.username and self.password)

    @property
    def pool_key(self) -> str:
        """Key used to look up the pooled client for these credentials."""
        if self.is_anonymous:
            return "__anonymous__"
        return f"{self.username}\x00{self.password}"

    def __repr__(self) -> str:  # never leak the password in logs
        return f"Credentials(username={self.username!r})" if not self.is_anonymous else "Credentials(anonymous)"


# Set by the SSE handler per connection. ``None`` means "not in an SSE request",
# in which case we fall back to environment variables (stdio transport).
current_credentials: ContextVar[Optional[Credentials]] = ContextVar(
    "screener_credentials", default=None
)


def env_credentials() -> Credentials:
    """Read credentials from environment variables (used by stdio transport)."""
    return Credentials(
        os.getenv("SCREENER_USERNAME", "").strip(),
        os.getenv("SCREENER_PASSWORD", "").strip(),
    )


def resolve_credentials() -> Credentials:
    """Resolve the credentials in effect for the current call."""
    creds = current_credentials.get()
    return creds if creds is not None else env_credentials()


class ScreenerClient:
    """An authenticated (or anonymous) HTTP session against Screener.in."""

    def __init__(self, credentials: Credentials):
        self._credentials = credentials
        self._client: Optional[httpx.AsyncClient] = None
        self._logged_in = False
        self._login_attempted = False
        self._lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
                timeout=DEFAULT_TIMEOUT,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── auth ─────────────────────────────────────────────────────────────────

    async def _get_csrf_token(self) -> str:
        client = self._ensure_client()
        resp = await client.get(f"{BASE_URL}/login/")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if token_input and token_input.get("value"):
            return token_input["value"]
        return client.cookies.get("csrftoken", "")

    async def ensure_login(self) -> bool:
        """Idempotently log in if credentials are present. Returns login state."""
        if self._credentials.is_anonymous:
            return False
        if self._login_attempted:
            return self._logged_in

        async with self._lock:
            if self._login_attempted:
                return self._logged_in
            self._login_attempted = True
            self._logged_in = await self._do_login()
            return self._logged_in

    async def _do_login(self) -> bool:
        client = self._ensure_client()
        try:
            csrf = await self._get_csrf_token()
            resp = await client.post(
                f"{BASE_URL}/login/",
                data={
                    "username": self._credentials.username,
                    "password": self._credentials.password,
                    "csrfmiddlewaretoken": csrf,
                    "next": "/",
                },
                headers={"Referer": f"{BASE_URL}/login/"},
            )
            if "/login/" not in str(resp.url):
                logger.info("Logged in to Screener.in as %s", self._credentials.username)
                return True
            logger.warning("Screener login failed for %s — check credentials", self._credentials.username)
            return False
        except httpx.HTTPError as exc:
            logger.error("Login error for %s: %s", self._credentials.username, exc)
            return False

    async def _relogin(self) -> bool:
        """Force a fresh login (e.g. after a session cookie expires)."""
        async with self._lock:
            self._login_attempted = True
            self._logged_in = await self._do_login()
            return self._logged_in

    # ── requests ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_auth_redirect(url: str) -> bool:
        return "/login/" in url or "/register/" in url

    def _auth_error(self) -> PermissionError:
        return PermissionError(
            "Screener.in requires login for this feature. Provide SCREENER_USERNAME "
            "and SCREENER_PASSWORD (via env vars for stdio, or per-connection headers/"
            "query params for SSE)."
        )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Issue a request with transient-error retries."""
        client = self._ensure_client()
        url = urljoin(BASE_URL, path)
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = await client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt <= MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF * attempt)
                    continue
                raise
        assert last_exc is not None  # pragma: no cover
        raise last_exc

    async def get_html(self, path: str, params: Optional[dict] = None) -> str:
        """Fetch an HTML page, retrying once after re-login if it bounces to /login."""
        resp = await self._request("GET", path, params=params or {})
        if self._is_auth_redirect(str(resp.url)):
            if not self._credentials.is_anonymous and await self._relogin():
                resp = await self._request("GET", path, params=params or {})
            if self._is_auth_redirect(str(resp.url)):
                raise self._auth_error()
        return resp.text

    async def get_json(self, path: str, params: Optional[dict] = None):
        """Fetch a JSON endpoint."""
        resp = await self._request(
            "GET", path, params=params or {}, headers=JSON_HEADERS
        )
        if self._is_auth_redirect(str(resp.url)):
            if not self._credentials.is_anonymous and await self._relogin():
                resp = await self._request("GET", path, params=params or {}, headers=JSON_HEADERS)
            if self._is_auth_redirect(str(resp.url)):
                raise self._auth_error()
        return resp.json()


# ── client pool ────────────────────────────────────────────────────────────

_pool: dict[str, ScreenerClient] = {}
_pool_lock = asyncio.Lock()


async def get_client() -> ScreenerClient:
    """Return a logged-in client for the credentials in effect for this call."""
    creds = resolve_credentials()
    key = creds.pool_key

    client = _pool.get(key)
    if client is None:
        async with _pool_lock:
            client = _pool.get(key)
            if client is None:
                if len(_pool) >= MAX_POOLED_CLIENTS:
                    await _evict_one()
                client = ScreenerClient(creds)
                _pool[key] = client

    await client.ensure_login()
    return client


async def _evict_one() -> None:
    """Drop and close one pooled client to stay under the cap (called under lock)."""
    for key, client in list(_pool.items()):
        if key == "__anonymous__":
            continue  # keep the shared anonymous client around
        _pool.pop(key, None)
        await client.close()
        return
    # only the anonymous client is present; nothing to evict


async def close_all() -> None:
    """Close every pooled client (call on server shutdown)."""
    async with _pool_lock:
        clients = list(_pool.values())
        _pool.clear()
    for client in clients:
        try:
            await client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            logger.debug("Error closing client", exc_info=True)
