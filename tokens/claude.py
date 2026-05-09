from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import httpx
from loguru import logger


TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
DEFAULT_SCOPES = [
    'user:profile', 'user:inference', 'user:sessions:claude_code',
    'user:mcp_servers', 'user:file_upload',
]

# Cloudflare aggressively rate-limits the token endpoint for unrecognized
# User-Agents (~10 reqs then multi-minute lockout). The official CLI's UA
# bypasses this.
USER_AGENT = 'claude-cli/2.0.0'

# Refresh before client handout if the cached access_token has fewer than
# this many seconds of validity remaining.
MIN_CLIENT_VALIDITY = 3600


class TokenRefreshError(Exception):
    """Raised when an OAuth refresh call fails unexpectedly."""


@dataclass(frozen=True)
class OAuthToken:
    """A standard OAuth 2.0 token set (RFC 6749). Provider-agnostic.

    ``expires_at`` is a Unix timestamp in seconds — matches ``time.time()``
    and OAuth's ``expires_in`` semantics. Encoding to other formats
    (e.g. milliseconds in JSON payloads) is a serialization concern."""

    access_token: str
    refresh_token: str
    expires_at: float
    scopes: list[str]

    @property
    def remaining(self) -> float:
        return self.expires_at - time.time()

    @property
    def is_expired(self) -> bool:
        return self.remaining <= 0

    def rotated(self, resp: dict) -> Self:
        """Return a new token reflecting an OAuth refresh response.

        Provider-specific fields on subclasses are carried over via
        ``dataclasses.replace``."""
        return dataclasses.replace(
            self,
            access_token=resp['access_token'],
            refresh_token=resp.get('refresh_token', self.refresh_token),
            expires_at=time.time() + resp.get('expires_in', 0),
            scopes=(resp.get('scope') or '').split() or self.scopes,
        )


@dataclass(frozen=True)
class ClaudeToken(OAuthToken):
    """Adds Anthropic subscription metadata. Stores expires in milliseconds
    additionally (the format Anthropic's JSON uses) so the disk roundtrip
    is lossless — converting through OAuthToken's float seconds would lose
    ±1 ms of precision."""

    subscription_type: str | None = None
    rate_limit_tier: str | None = None
    expires_at_ms: int = 0    # MUST stay consistent with self.expires_at; both set in constructors

    @classmethod
    def from_json(cls, raw: str) -> ClaudeToken:
        """Parse from the on-disk JSON format (``{"claudeAiOauth": {...}}``).
        Anthropic stores ``expiresAt`` in milliseconds since epoch."""
        d = json.loads(raw)['claudeAiOauth']
        ms = d.get('expiresAt', 0)
        return cls(
            access_token=d['accessToken'],
            refresh_token=d.get('refreshToken', ''),
            expires_at=ms / 1000,                 # OAuthToken.expires_at (lossy view)
            scopes=d.get('scopes') or DEFAULT_SCOPES,
            subscription_type=d.get('subscriptionType'),
            rate_limit_tier=d.get('rateLimitTier'),
            expires_at_ms=ms,                     # exact ms (authoritative for disk roundtrip)
        )

    def to_json(self) -> str:
        """Full JSON for server-side persistence (includes refresh_token)."""
        return json.dumps({'claudeAiOauth': {
            'accessToken': self.access_token,
            'refreshToken': self.refresh_token,
            'expiresAt': self.expires_at_ms,     # use exact ms — no float conversion
            'scopes': self.scopes,
            'subscriptionType': self.subscription_type,
            'rateLimitTier': self.rate_limit_tier,
        }})

    def to_client_json(self) -> str:
        """JSON for client distribution — refresh_token stripped."""
        return json.dumps({'claudeAiOauth': {
            'accessToken': self.access_token,
            'expiresAt': self.expires_at_ms,
            'scopes': self.scopes,
            'subscriptionType': self.subscription_type,
            'rateLimitTier': self.rate_limit_tier,
        }})

    def rotated(self, resp: dict) -> ClaudeToken:
        """Override: derive ms from expires_in (integer arithmetic) and set
        both expires_at and expires_at_ms consistently."""
        ms = int(time.time() * 1000) + resp.get('expires_in', 0) * 1000
        return dataclasses.replace(
            self,
            access_token=resp['access_token'],
            refresh_token=resp.get('refresh_token', self.refresh_token),
            expires_at=ms / 1000,
            scopes=(resp.get('scope') or '').split() or self.scopes,
            expires_at_ms=ms,
        )


class ClaudeFactory:
    """HTTP client that mints new ClaudeTokens via the OAuth refresh endpoint."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def refresh(self, token: ClaudeToken) -> ClaudeToken:
        if not token.refresh_token:
            raise TokenRefreshError('No refresh_token available')

        try:
            resp = await self._client.post(
                TOKEN_URL,
                json={
                    'grant_type': 'refresh_token',
                    'refresh_token': token.refresh_token,
                    'client_id': CLIENT_ID,
                    'scope': ' '.join(token.scopes),
                },
                headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise TokenRefreshError(f'HTTP request failed: {e}') from e

        if resp.status_code != 200:
            raise TokenRefreshError(f'Refresh failed ({resp.status_code}): {resp.text}')

        return token.rotated(resp.json())

    async def close(self) -> None:
        await self._client.aclose()


DEFAULT_PATH = Path(__file__).parent / 'data' / 'claude.json'


class TokenStore:
    """Persistence layer for ClaudeToken — owns the disk file and an
    in-memory cache. Hides the memory/disk duality from the Provider.

    Not thread-safe; callers (Provider) serialize access via a lock."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache: ClaudeToken | None = None

    @property
    def cached(self) -> ClaudeToken | None:
        """Current cache value without I/O. ``None`` until first load."""
        return self._cache

    def current(self) -> ClaudeToken:
        """Return the cached token, lazy-loading from disk on first call."""
        if self._cache is None:
            self._cache = ClaudeToken.from_json(self._path.read_text())
        return self._cache

    def reload(self) -> ClaudeToken:
        """Re-read disk and refresh the cache."""
        self._cache = ClaudeToken.from_json(self._path.read_text())
        return self._cache

    def replace(self, token: ClaudeToken) -> None:
        """Persist ``token`` to disk, then update the in-memory cache.

        Disk-first ordering: if the write throws, the cache stays on the
        previous value, keeping memory and disk consistent."""
        self._path.write_text(token.to_json())
        self._cache = token
        logger.info(f'[claude] Token refreshed and saved ({self._path})')


class ClaudeProvider:
    """Orchestrates token lifecycle: lock + factory + store + policy.
    Persistence lives in TokenStore; HTTP refresh lives in ClaudeFactory."""

    name = 'claude'

    def __init__(self, token_path: Path | None = None) -> None:
        self._lock = asyncio.Lock()
        self._factory = ClaudeFactory()
        self._store = TokenStore(token_path or DEFAULT_PATH)

    @property
    def token(self) -> ClaudeToken | None:
        return self._store.cached

    async def force_refresh(self) -> ClaudeToken:
        """Refresh once at startup to validate the seed."""
        async with self._lock:
            token = self._store.reload()
            new_token = await self._factory.refresh(token)
            self._store.replace(new_token)
            return new_token

    async def token_for_client(self) -> str:
        """Return JSON for client distribution (no refresh_token).

        Refreshes first if remaining validity is below MIN_CLIENT_VALIDITY,
        kicking any current holders so the new client gets useful lifetime.
        """
        async with self._lock:
            token = self._store.current()
            if token.remaining < MIN_CLIENT_VALIDITY:
                logger.info(
                    f'[{self.name}] {token.remaining:.0f}s remaining'
                    f' (< {MIN_CLIENT_VALIDITY}s), refreshing before handout'
                )
                token = await self._factory.refresh(token)
                self._store.replace(token)
        return token.to_client_json()

    async def keep_fresh_loop(self) -> None:
        """Background safety net: refresh whenever the cached token expires."""
        while True:
            try:
                async with self._lock:
                    token = self._store.current()
                    if token.is_expired:
                        new_token = await self._factory.refresh(token)
                        self._store.replace(new_token)
            except Exception as e:
                logger.error(f'[{self.name}] keep_fresh failed: {e}')
            await asyncio.sleep(random.uniform(300, 600))

    async def close(self) -> None:
        await self._factory.close()
