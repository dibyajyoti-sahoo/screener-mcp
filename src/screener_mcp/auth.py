"""
Client-authentication gate backed by an external Auth Server (SuperTokens).

When ``AUTH_SERVER_URL`` is configured, every SSE connection must present valid
credentials, which are validated by calling ``POST {AUTH_SERVER_URL}/auth/signin``
with HTTP Basic auth (``base64("email:password")``). The auth server returns:

    {"status": "OK", "session": {...}, "accessToken": {...}, ...}   # success
    {"status": "WRONG_CREDENTIALS_ERROR"}                            # failure

This only controls *who may use the MCP server*. The Screener.in account used to
fetch data is configured separately via ``SCREENER_USERNAME`` / ``SCREENER_PASSWORD``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_AUTH_TIMEOUT = 10.0
DEFAULT_CACHE_TTL = 300.0      # seconds a successful sign-in stays cached
MAX_CACHE_ENTRIES = 1024


def auth_server_url() -> str:
    """Configured auth-server base URL (no trailing slash), or '' if unset."""
    return os.getenv("AUTH_SERVER_URL", "").strip().rstrip("/")


def auth_enabled() -> bool:
    """Auth is on when AUTH_ENABLED is truthy, or (by default) when a URL is set."""
    flag = os.getenv("AUTH_ENABLED", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(auth_server_url())


@dataclass
class AuthResult:
    ok: bool
    status: str = ""
    user_id: Optional[str] = None
    error: Optional[str] = None
    expiry_ms: Optional[int] = None   # access-token expiry (epoch ms), if provided


def cache_ttl() -> float:
    try:
        return float(os.getenv("AUTH_CACHE_TTL", DEFAULT_CACHE_TTL))
    except ValueError:
        return DEFAULT_CACHE_TTL


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        timeout = float(os.getenv("AUTH_TIMEOUT", DEFAULT_AUTH_TIMEOUT))
        _client = httpx.AsyncClient(timeout=timeout)
    return _client


async def signin(username: str, password: str) -> AuthResult:
    """Validate credentials against the auth server's /auth/signin endpoint."""
    base = auth_server_url()
    if not base:
        return AuthResult(ok=False, error="AUTH_SERVER_URL not configured")

    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    try:
        resp = await _get_client().post(
            f"{base}/auth/signin",
            headers={"Authorization": f"Basic {token}"},
        )
    except httpx.HTTPError as exc:
        logger.warning("Auth server unreachable: %s", exc)
        return AuthResult(ok=False, error=f"auth server unreachable: {exc}")

    if resp.status_code >= 500:
        return AuthResult(ok=False, error=f"auth server error {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return AuthResult(ok=False, error="invalid auth server response")

    status = str(data.get("status", ""))
    if status == "OK":
        session = data.get("session") or {}
        access = data.get("accessToken") or {}
        expiry = access.get("expiry") if isinstance(access, dict) else None
        return AuthResult(
            ok=True, status=status, user_id=session.get("userId"), expiry_ms=expiry
        )
    return AuthResult(ok=False, status=status or "WRONG_CREDENTIALS_ERROR")


async def verify(access_token: str) -> AuthResult:
    """Validate a SuperTokens access token via /auth/verify (Bearer)."""
    base = auth_server_url()
    if not base:
        return AuthResult(ok=False, error="AUTH_SERVER_URL not configured")

    try:
        resp = await _get_client().post(
            f"{base}/auth/verify",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as exc:
        logger.warning("Auth server unreachable: %s", exc)
        return AuthResult(ok=False, error=f"auth server unreachable: {exc}")

    if resp.status_code >= 500:
        return AuthResult(ok=False, error=f"auth server error {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return AuthResult(ok=False, error="invalid auth server response")

    if resp.status_code == 200 and bool(data.get("valid")):
        return AuthResult(ok=True, status="OK")
    return AuthResult(ok=False, status="TOKEN_INVALID")


# ── credential cache ────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    expires_at: float       # epoch seconds
    user_id: Optional[str]


_cache: dict[str, _CacheEntry] = {}
_cache_lock = asyncio.Lock()


def _cache_key(username: str, password: str) -> str:
    # Hash so plaintext passwords are never held in memory.
    return hashlib.sha256(f"{username}\x00{password}".encode("utf-8")).hexdigest()


def _prune(now: float) -> None:
    """Drop expired entries; if still full, drop the soonest-to-expire."""
    expired = [k for k, e in _cache.items() if e.expires_at <= now]
    for k in expired:
        _cache.pop(k, None)
    if len(_cache) >= MAX_CACHE_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k].expires_at)
        _cache.pop(oldest, None)


async def authenticate(username: str, password: str) -> AuthResult:
    """Validate credentials, using the cache and falling back to /auth/signin.

    Cache hit (not expired) → trusted without a network call. Miss/expired →
    sign in; cache the result on success, evict any stale entry on failure.
    """
    key = _cache_key(username, password)
    now = time.time()

    entry = _cache.get(key)
    if entry is not None and entry.expires_at > now:
        return AuthResult(ok=True, status="OK_CACHED", user_id=entry.user_id)

    result = await signin(username, password)
    async with _cache_lock:
        if result.ok:
            expires_at = now + cache_ttl()
            if result.expiry_ms:  # never trust past the token's own lifetime
                expires_at = min(expires_at, result.expiry_ms / 1000.0)
            _prune(now)
            _cache[key] = _CacheEntry(expires_at=expires_at, user_id=result.user_id)
        else:
            _cache.pop(key, None)
    return result


def clear_cache() -> None:
    _cache.clear()


async def close() -> None:
    """Close the shared auth client and clear the cache (call on shutdown)."""
    global _client
    clear_cache()
    if _client is not None:
        await _client.aclose()
        _client = None
