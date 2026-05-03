from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

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
class ClaudeToken:
    """Claude OAuth token — pure value object."""

    access_token: str
    refresh_token: str
    expires_at_ms: int
    scopes: list[str]
    subscription_type: str | None = None
    rate_limit_tier: str | None = None

    @classmethod
    def from_json(cls, raw: str) -> ClaudeToken:
        """Parse from the on-disk JSON format ({"claudeAiOauth": {...}})."""
        d = json.loads(raw)['claudeAiOauth']
        return cls(
            access_token=d['accessToken'],
            refresh_token=d.get('refreshToken', ''),
            expires_at_ms=d.get('expiresAt', 0),
            scopes=d.get('scopes') or DEFAULT_SCOPES,
            subscription_type=d.get('subscriptionType'),
            rate_limit_tier=d.get('rateLimitTier'),
        )

    @classmethod
    def from_oauth_response(cls, prev: ClaudeToken, resp: dict) -> ClaudeToken:
        """Build the rotated token from an OAuth refresh response, falling
        back to the previous token's metadata for fields the server omits."""
        return cls(
            access_token=resp['access_token'],
            refresh_token=resp.get('refresh_token', prev.refresh_token),
            expires_at_ms=int(time.time() * 1000) + resp.get('expires_in', 0) * 1000,
            scopes=(resp.get('scope') or '').split() or prev.scopes,
            subscription_type=prev.subscription_type,
            rate_limit_tier=prev.rate_limit_tier,
        )

    @property
    def expires_at(self) -> float:
        return self.expires_at_ms / 1000

    @property
    def remaining(self) -> float:
        return self.expires_at - time.time()

    @property
    def is_expired(self) -> bool:
        return self.remaining <= 0

    def to_json(self) -> str:
        """Full JSON for server-side persistence (includes refresh_token)."""
        return json.dumps({'claudeAiOauth': {
            'accessToken': self.access_token,
            'refreshToken': self.refresh_token,
            'expiresAt': self.expires_at_ms,
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

        return ClaudeToken.from_oauth_response(token, resp.json())

    async def close(self) -> None:
        await self._client.aclose()


class ClaudeProvider:
    """Manages the seed token: persists to disk, refreshes on demand,
    serves access_tokens to clients (refresh_token never leaves the server)."""

    name = 'claude'

    def __init__(self, token_path: Path | None = None) -> None:
        self._path = token_path or Path(__file__).parent / 'data' / 'claude.json'
        self._lock = asyncio.Lock()
        self._factory = ClaudeFactory()
        self._token: ClaudeToken | None = None

    @property
    def token(self) -> ClaudeToken | None:
        return self._token

    def _load(self) -> ClaudeToken:
        self._token = ClaudeToken.from_json(self._path.read_text())
        return self._token

    def _save(self, token: ClaudeToken) -> None:
        self._path.write_text(token.to_json())
        self._token = token
        logger.info(f'[{self.name}] Token refreshed and saved')

    async def force_refresh(self) -> ClaudeToken:
        """Refresh once at startup to validate the seed."""
        async with self._lock:
            token = self._load()
            new_token = await self._factory.refresh(token)
            self._save(new_token)
            return new_token

    async def token_for_client(self) -> str:
        """Return JSON for client distribution (no refresh_token).

        Refreshes first if remaining validity is below MIN_CLIENT_VALIDITY,
        kicking any current holders so the new client gets useful lifetime.
        """
        async with self._lock:
            token = self._load()
            if token.remaining < MIN_CLIENT_VALIDITY:
                logger.info(
                    f'[{self.name}] {token.remaining:.0f}s remaining'
                    f' (< {MIN_CLIENT_VALIDITY}s), refreshing before handout'
                )
                token = await self._factory.refresh(token)
                self._save(token)
        return token.to_client_json()

    async def keep_fresh_loop(self) -> None:
        """Background safety net: refresh whenever the cached token expires."""
        while True:
            try:
                async with self._lock:
                    token = self._load()
                    if token.is_expired:
                        new_token = await self._factory.refresh(token)
                        self._save(new_token)
            except Exception as e:
                logger.error(f'[{self.name}] keep_fresh failed: {e}')
            await asyncio.sleep(random.uniform(300, 600))

    async def close(self) -> None:
        await self._factory.close()
