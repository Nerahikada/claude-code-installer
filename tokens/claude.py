from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from loguru import logger

from tokens.base import TokenRefreshError, OAuthToken, TokenProvider, TokenRefresher

TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
DEFAULT_SCOPES = ['user:profile', 'user:inference', 'user:sessions:claude_code', 'user:mcp_servers', 'user:file_upload']

# Cloudflare aggressively rate-limits the token endpoint for unrecognized
# User-Agents. The official CLI's UA bypasses this. Without it we see
# 429 within a few requests followed by a multi-minute lockout.
USER_AGENT = 'claude-cli/2.0.0'


class ClaudeToken(OAuthToken):
    """Claude AI OAuth token."""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self._data = self._parse(raw)
        self._oauth = self._data['claudeAiOauth']

    @staticmethod
    def _parse(raw: str) -> dict:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError('Invalid JSON format') from e
        if 'claudeAiOauth' not in data:
            raise ValueError('Missing required key: claudeAiOauth')
        return data

    @property
    def expires_at(self) -> float:
        return self._oauth.get('expiresAt', 0) / 1000

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def access_token(self) -> str:
        return self._oauth.get('accessToken', '')

    @property
    def refresh_token(self) -> str:
        return self._oauth.get('refreshToken', '')

    @property
    def scopes(self) -> list[str]:
        return self._oauth.get('scopes', DEFAULT_SCOPES)

    def serialize(self) -> str:
        return self._raw

    def serialize_for_client(self) -> str:
        """Serialize without refresh_token. Clients only need the access_token."""
        client_view = {
            'claudeAiOauth': {
                'accessToken': self.access_token,
                'expiresAt': int(self.expires_at * 1000),
                'scopes': self.scopes,
                'subscriptionType': self._oauth.get('subscriptionType'),
                'rateLimitTier': self._oauth.get('rateLimitTier'),
            }
        }
        return json.dumps(client_view)


class ClaudeRefresher(TokenRefresher):
    """Handles OAuth token refresh against the Claude API."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def refresh(self, token: OAuthToken, *, force: bool = False) -> ClaudeToken | None:
        if not isinstance(token, ClaudeToken):
            raise TypeError(f'Expected ClaudeToken, got {type(token).__name__}')

        if not token.refresh_token:
            logger.debug('No refresh token available')
            return None

        if not force and not token.is_expired:
            logger.debug('Token is still valid and force=False, skipping refresh')
            return token

        logger.debug(f'Refreshing token (expired={token.is_expired}, force={force})')

        body = {
            'grant_type': 'refresh_token',
            'refresh_token': token.refresh_token,
            'client_id': CLIENT_ID,
            'scope': ' '.join(token.scopes),
        }

        try:
            resp = await self._client.post(
                TOKEN_URL,
                json=body,
                headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise TokenRefreshError(f'HTTP request failed: {e}') from e

        if resp.status_code == 401:
            logger.debug('Refresh token rejected (401), re-login required')
            return None

        if resp.status_code != 200:
            raise TokenRefreshError(
                f'Token refresh failed ({resp.status_code}): {resp.text}'
            )

        data = resp.json()
        new_access_token = data.get('access_token')
        new_refresh_token = data.get('refresh_token', token.refresh_token)
        expires_in = data.get('expires_in', 0)
        new_expires_at = int(time.time() * 1000) + expires_in * 1000
        new_scopes = (data.get('scope') or '').split() or token.scopes

        new_data = {
            'claudeAiOauth': {
                'accessToken': new_access_token,
                'refreshToken': new_refresh_token,
                'expiresAt': new_expires_at,
                'scopes': new_scopes,
                'subscriptionType': token._oauth.get('subscriptionType'),
                'rateLimitTier': token._oauth.get('rateLimitTier'),
            }
        }

        new_raw = json.dumps(new_data)
        new_token = ClaudeToken(new_raw)

        if token.access_token == new_token.access_token and token.refresh_token == new_token.refresh_token:
            if force:
                logger.debug('Tokens were not refreshed despite force flag, please re-login')
                return None
            logger.debug('Tokens unchanged, no refresh needed')
        else:
            logger.debug(f'Tokens refreshed successfully (new expiration: {new_token.expires_at})')

        return new_token

    async def close(self) -> None:
        await self._client.aclose()


class ClaudeProvider(TokenProvider):
    """Token provider for Claude AI."""

    def __init__(self, token_path: Path | None = None) -> None:
        super().__init__('claude', ClaudeRefresher(), token_path)

    def _load(self, raw: str) -> ClaudeToken:
        return ClaudeToken(raw)
