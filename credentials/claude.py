from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from loguru import logger

from credentials.base import CredentialRefreshError, Credentials, CredentialProvider

TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
DEFAULT_SCOPES = ['user:profile', 'user:inference', 'user:sessions:claude_code', 'user:mcp_servers', 'user:file_upload']


class ClaudeCredentials(Credentials):
    """Claude AI OAuth credentials."""

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

    async def refresh(self, force: bool = False) -> ClaudeCredentials | None:
        if not self.refresh_token:
            logger.debug('No refresh token available')
            return None

        if not force and not self.is_expired:
            logger.debug('Token is still valid and force=False, skipping refresh')
            return self

        logger.debug(f'Refreshing credentials (expired={self.is_expired}, force={force})')

        body = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': CLIENT_ID,
            'scope': ' '.join(self.scopes),
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    TOKEN_URL,
                    json=body,
                    headers={'Content-Type': 'application/json'},
                    timeout=30,
                )
        except httpx.HTTPError as e:
            raise CredentialRefreshError(f'HTTP request failed: {e}') from e

        if resp.status_code == 401:
            logger.debug('Refresh token rejected (401), re-login required')
            return None

        if resp.status_code != 200:
            raise CredentialRefreshError(
                f'Token refresh failed ({resp.status_code}): {resp.text}'
            )

        data = resp.json()
        new_access_token = data.get('access_token')
        new_refresh_token = data.get('refresh_token', self.refresh_token)
        expires_in = data.get('expires_in', 0)
        new_expires_at = int(time.time() * 1000) + expires_in * 1000
        new_scopes = (data.get('scope') or '').split() or self.scopes

        new_data = {
            'claudeAiOauth': {
                'accessToken': new_access_token,
                'refreshToken': new_refresh_token,
                'expiresAt': new_expires_at,
                'scopes': new_scopes,
                'subscriptionType': self._oauth.get('subscriptionType'),
                'rateLimitTier': self._oauth.get('rateLimitTier'),
            }
        }

        new_raw = json.dumps(new_data)
        new_creds = ClaudeCredentials(new_raw)

        if self.access_token == new_creds.access_token and self.refresh_token == new_creds.refresh_token:
            if force:
                logger.debug('Tokens were not refreshed despite force flag, please re-login')
                return None
            logger.debug('Tokens unchanged, no refresh needed')
        else:
            logger.debug(f'Tokens refreshed successfully (new expiration: {new_creds.expires_at})')

        return new_creds


class ClaudeProvider(CredentialProvider):
    """Credential provider for Claude AI."""

    def __init__(self, cred_path: Path | None = None) -> None:
        super().__init__('claude', cred_path)

    def _load(self, raw: str) -> ClaudeCredentials:
        return ClaudeCredentials(raw)
