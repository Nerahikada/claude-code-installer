from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger


class TokenRefreshError(Exception):
    """Raised when token refresh fails unexpectedly."""


class TokenSplitError(Exception):
    """Raised when dual refresh produced only one token (server token is healthy)."""


class OAuthToken(ABC):
    """Abstract base for an OAuth token set (access + refresh)."""

    @property
    @abstractmethod
    def is_expired(self) -> bool: ...

    @abstractmethod
    async def refresh(self, force: bool = False) -> OAuthToken | None:
        """Refresh this token. Returns new instance, or None if re-login required."""
        ...

    @abstractmethod
    def serialize(self) -> str:
        """Serialize token data to a string suitable for client consumption."""
        ...

    def __str__(self) -> str:
        return self.serialize()


DATA_DIR = Path(__file__).parent / 'data'


class TokenProvider(ABC):
    """Manages OAuth tokens for a single provider (Claude, Codex, etc.)."""

    def __init__(self, name: str, token_path: Path | None = None) -> None:
        self.name = name
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
                    new_token = await token.refresh()
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
            new_token = await token.refresh(force=True)
            if new_token is None:
                raise TokenRefreshError(f'[{self.name}] Force refresh failed, re-login required')
            self._save(new_token)
            return new_token

    async def generate_for_client(self) -> OAuthToken:
        """Generate an independent token set for client distribution.

        Exploits the OAuth server's grace period before refresh-token
        invalidation: two parallel refreshes from the same token can each
        yield an independent token pair.

        - 2 succeed → save one (server), return the other (client).
        - 1 succeeds → server token preserved, raise TokenSplitError
          (client should retry).
        - 0 succeed → should not happen under normal operation (seed is
          validated at startup); indicates an external problem.
        """
        async with self._lock:
            token = self.load()
            results = await asyncio.gather(
                token.refresh(force=True),
                token.refresh(force=True),
                return_exceptions=True,
            )
            valid = [r for r in results if isinstance(r, OAuthToken)]

            if len(valid) == 2:
                self._save(valid[0])
                logger.info(f'[{self.name}] Generated independent client token')
                return valid[1]

            if len(valid) == 1:
                self._save(valid[0])
                raise TokenSplitError(f'[{self.name}] Token split failed, server token preserved')

            errors = [r for r in results if isinstance(r, Exception)]
            raise TokenRefreshError(f'[{self.name}] Both refreshes failed: {errors}')

    def _save(self, token: OAuthToken) -> None:
        self._token_path.write_text(token.serialize())
        self._token = token
        logger.info(f'[{self.name}] Token refreshed and saved')

    async def keep_fresh_loop(self) -> None:
        """Continuously keep token fresh."""
        while True:
            await self.ensure_fresh()
            await asyncio.sleep(random.uniform(300, 600))
