from __future__ import annotations

import asyncio
import json
import random
import time
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
# this many seconds of validity remaining. Each refresh invalidates any
# previously-distributed access_token, so this is a tradeoff between
# guaranteeing minimum useful lifetime to new clients and disrupting
# existing ones.
MIN_CLIENT_VALIDITY = 3600


class TokenRefreshError(Exception):
    """Raised when an OAuth refresh call fails unexpectedly."""


class ClaudeProvider:
    """Manages the Claude OAuth token: persists the seed, refreshes on
    demand, and serves access_tokens to clients (refresh_token never
    leaves the server)."""

    name = 'claude'

    def __init__(self, token_path: Path | None = None) -> None:
        self._path = token_path or Path(__file__).parent / 'data' / 'claude.json'
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=30)
        self._token: dict | None = None  # parsed claudeAiOauth dict

    @property
    def token(self) -> dict | None:
        return self._token

    def _load(self) -> dict:
        data = json.loads(self._path.read_text())
        self._token = data['claudeAiOauth']
        return self._token

    def _save(self, token: dict) -> None:
        self._path.write_text(json.dumps({'claudeAiOauth': token}))
        self._token = token
        logger.info(f'[{self.name}] Token refreshed and saved')

    async def _refresh(self, token: dict, *, force: bool = False) -> dict | None:
        """Call the OAuth refresh endpoint. Returns new token, or None if
        re-login is required. Caller is responsible for locking and saving."""
        if not token.get('refreshToken'):
            logger.debug('No refresh_token available')
            return None
        expires_at = token.get('expiresAt', 0) / 1000
        if not force and time.time() < expires_at:
            logger.debug('Token still valid and force=False, skipping refresh')
            return token

        logger.debug(f'Refreshing token (force={force})')
        try:
            resp = await self._client.post(
                TOKEN_URL,
                json={
                    'grant_type': 'refresh_token',
                    'refresh_token': token['refreshToken'],
                    'client_id': CLIENT_ID,
                    'scope': ' '.join(token.get('scopes') or DEFAULT_SCOPES),
                },
                headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise TokenRefreshError(f'HTTP request failed: {e}') from e

        if resp.status_code == 401:
            logger.debug('Refresh rejected (401), re-login required')
            return None
        if resp.status_code != 200:
            raise TokenRefreshError(f'Refresh failed ({resp.status_code}): {resp.text}')

        data = resp.json()
        return {
            'accessToken': data['access_token'],
            'refreshToken': data.get('refresh_token', token['refreshToken']),
            'expiresAt': int(time.time() * 1000) + data.get('expires_in', 0) * 1000,
            'scopes': (data.get('scope') or '').split() or token.get('scopes') or DEFAULT_SCOPES,
            'subscriptionType': token.get('subscriptionType'),
            'rateLimitTier': token.get('rateLimitTier'),
        }

    async def force_refresh(self) -> dict:
        """Refresh once regardless of expiry; used at startup to validate the seed."""
        async with self._lock:
            token = self._load()
            new = await self._refresh(token, force=True)
            if new is None:
                raise TokenRefreshError(f'[{self.name}] Re-login required')
            self._save(new)
            return new

    async def token_for_client(self) -> str:
        """Return JSON for client distribution (no refresh_token).

        Refreshes first if remaining validity is below MIN_CLIENT_VALIDITY,
        kicking any current holders so the new client gets useful lifetime.
        """
        async with self._lock:
            token = self._load()
            remaining = token.get('expiresAt', 0) / 1000 - time.time()
            if remaining < MIN_CLIENT_VALIDITY:
                logger.info(
                    f'[{self.name}] {remaining:.0f}s remaining'
                    f' (< {MIN_CLIENT_VALIDITY}s), refreshing before handout'
                )
                new = await self._refresh(token, force=True)
                if new is None:
                    raise TokenRefreshError(f'[{self.name}] Re-login required')
                self._save(new)
                token = new
        return json.dumps({
            'claudeAiOauth': {
                'accessToken': token['accessToken'],
                'expiresAt': token['expiresAt'],
                'scopes': token.get('scopes'),
                'subscriptionType': token.get('subscriptionType'),
                'rateLimitTier': token.get('rateLimitTier'),
            }
        })

    async def keep_fresh_loop(self) -> None:
        """Background task: refresh whenever the cached token is expired."""
        while True:
            try:
                async with self._lock:
                    token = self._load()
                    if time.time() >= token.get('expiresAt', 0) / 1000:
                        new = await self._refresh(token)
                        if new:
                            self._save(new)
            except Exception as e:
                logger.error(f'[{self.name}] keep_fresh failed: {e}')
            await asyncio.sleep(random.uniform(300, 600))

    async def close(self) -> None:
        await self._client.aclose()
