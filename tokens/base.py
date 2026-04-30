from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger


class TokenRefreshError(Exception):
    """Raised when token refresh fails unexpectedly."""


class OAuthToken(ABC):
    """Abstract base for an OAuth token set (access + refresh). Pure data, no I/O."""

    @property
    @abstractmethod
    def expires_at(self) -> float:
        """Unix timestamp (seconds) when the access_token expires."""
        ...

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @abstractmethod
    def serialize(self) -> str:
        """Serialize for server-side persistence (includes refresh_token)."""
        ...

    def serialize_for_client(self) -> str:
        """Serialize for client distribution. Override to strip secrets."""
        return self.serialize()

    def __str__(self) -> str:
        return self.serialize()


class TokenRefresher(ABC):
    """Handles the HTTP refresh call for a specific OAuth provider."""

    @abstractmethod
    async def refresh(self, token: OAuthToken, *, force: bool = False) -> OAuthToken | None:
        """Refresh the given token. Returns a new instance, or None if re-login required."""
        ...

    async def close(self) -> None:
        """Release any held resources (HTTP clients, etc.)."""


DATA_DIR = Path(__file__).parent / 'data'


MIN_CLIENT_VALIDITY_SECONDS = 3600  # Refresh before handing out if remaining < 1h.


class TokenProvider(ABC):
    """Manages OAuth tokens for a single provider (Claude, Codex, etc.)."""

    def __init__(self, name: str, refresher: TokenRefresher, token_path: Path | None = None) -> None:
        self.name = name
        self._refresher = refresher
        self._token_path = token_path or DATA_DIR / f'{name}.json'
        self._lock = asyncio.Lock()
        self._token: OAuthToken | None = None

    @abstractmethod
    def _load(self, raw: str) -> OAuthToken:
        """Parse raw string into an OAuthToken instance."""
        ...

    def load(self) -> OAuthToken:
        """Load token from disk."""
        self._token = self._load(self._token_path.read_text())
        return self._token

    @property
    def token(self) -> OAuthToken | None:
        return self._token

    async def ensure_fresh(self) -> OAuthToken | None:
        """Refresh token if expired. Returns current token."""
        async with self._lock:
            try:
                token = self.load()
                if token.is_expired:
                    new_token = await self._refresher.refresh(token)
                    if new_token is None:
                        logger.error(f'[{self.name}] Re-login required to refresh token')
                        return token
                    self._save(new_token)
                    return new_token
                return token
            except Exception as e:
                logger.error(f'[{self.name}] Failed to refresh token: {e}')
                return self._token

    async def force_refresh(self) -> OAuthToken:
        """Force a token refresh regardless of expiration."""
        async with self._lock:
            token = self.load()
            new_token = await self._refresher.refresh(token, force=True)
            if new_token is None:
                raise TokenRefreshError(f'[{self.name}] Force refresh failed, re-login required')
            self._save(new_token)
            return new_token

    async def token_for_client(self) -> OAuthToken:
        """Return the current access_token for client distribution.

        Anthropic's OAuth server enforces strict single-use refresh_token
        rotation with no grace period, so we cannot mint independent tokens
        per client. All clients share the server's current access_token.

        If the cached token has less than MIN_CLIENT_VALIDITY_SECONDS
        remaining, refresh first so the new client gets a useful lifetime.
        Refreshing invalidates any previously-distributed tokens, so we
        only do it when necessary.
        """
        async with self._lock:
            token = self.load()
            remaining = token.expires_at - time.time()
            if remaining >= MIN_CLIENT_VALIDITY_SECONDS:
                return token
            logger.info(
                f'[{self.name}] Token has {remaining:.0f}s remaining'
                f' (< {MIN_CLIENT_VALIDITY_SECONDS}s), refreshing before handout'
            )
            new_token = await self._refresher.refresh(token, force=True)
            if new_token is None:
                raise TokenRefreshError(f'[{self.name}] Refresh failed, re-login required')
            self._save(new_token)
            return new_token

    def _save(self, token: OAuthToken) -> None:
        self._token_path.write_text(token.serialize())
        self._token = token
        logger.info(f'[{self.name}] Token refreshed and saved')

    async def keep_fresh_loop(self) -> None:
        """Continuously keep token fresh."""
        while True:
            await self.ensure_fresh()
            await asyncio.sleep(random.uniform(300, 600))
