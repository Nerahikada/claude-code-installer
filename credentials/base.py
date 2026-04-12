from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger


class CredentialRefreshError(Exception):
    """Raised when credential refresh fails unexpectedly."""


class CredentialSplitError(Exception):
    """Raised when dual refresh produced only one token (server token is healthy)."""


class Credentials(ABC):
    """Abstract base for OAuth credentials."""

    @property
    @abstractmethod
    def is_expired(self) -> bool: ...

    @abstractmethod
    async def refresh(self, force: bool = False) -> Credentials | None:
        """Refresh credentials. Returns new instance, or None if re-login required."""
        ...

    @abstractmethod
    def serialize(self) -> str:
        """Serialize credentials to a string suitable for client consumption."""
        ...

    def __str__(self) -> str:
        return self.serialize()


DATA_DIR = Path(__file__).parent / 'data'


class CredentialProvider(ABC):
    """Manages credentials for a single provider (Claude, Codex, etc.)."""

    def __init__(self, name: str, cred_path: Path | None = None) -> None:
        self.name = name
        self._cred_path = cred_path or DATA_DIR / f'{name}.json'
        self._lock = asyncio.Lock()
        self._credentials: Credentials | None = None

    @abstractmethod
    def _load(self, raw: str) -> Credentials:
        """Parse raw credential string into a Credentials instance."""
        ...

    def load(self) -> Credentials:
        """Load credentials from disk."""
        self._credentials = self._load(self._cred_path.read_text())
        return self._credentials

    @property
    def credentials(self) -> Credentials | None:
        return self._credentials

    async def ensure_fresh(self) -> Credentials | None:
        """Refresh credentials if expired. Returns current credentials."""
        async with self._lock:
            try:
                creds = self.load()
                if creds.is_expired:
                    new_creds = await creds.refresh()
                    if new_creds is None:
                        logger.error(f'[{self.name}] Re-login required to refresh credentials')
                        return creds
                    self._save(new_creds)
                    return new_creds
                return creds
            except Exception as e:
                logger.error(f'[{self.name}] Failed to refresh credentials: {e}')
                return self._credentials

    async def force_refresh(self) -> Credentials:
        """Force a credential refresh regardless of expiration."""
        async with self._lock:
            creds = self.load()
            new_creds = await creds.refresh(force=True)
            if new_creds is None:
                raise CredentialRefreshError(f'[{self.name}] Force refresh failed, re-login required')
            self._save(new_creds)
            return new_creds

    async def generate_for_client(self) -> Credentials:
        """Generate an independent credential set for client distribution.

        Exploits the OAuth server's grace period before refresh-token
        invalidation: two parallel refreshes from the same token can each
        yield an independent token pair.

        - 2 succeed → save one (server), return the other (client).
        - 1 succeeds → server token preserved, raise CredentialSplitError
          (client should retry).
        - 0 succeed → should not happen under normal operation (seed is
          validated at startup); indicates an external problem.
        """
        async with self._lock:
            creds = self.load()
            results = await asyncio.gather(
                creds.refresh(force=True),
                creds.refresh(force=True),
                return_exceptions=True,
            )
            valid = [r for r in results if isinstance(r, Credentials)]

            if len(valid) == 2:
                self._save(valid[0])
                logger.info(f'[{self.name}] Generated independent client credentials')
                return valid[1]

            if len(valid) == 1:
                self._save(valid[0])
                raise CredentialSplitError(f'[{self.name}] Token split failed, server token preserved')

            errors = [r for r in results if isinstance(r, Exception)]
            raise CredentialRefreshError(f'[{self.name}] Both refreshes failed: {errors}')

    def _save(self, creds: Credentials) -> None:
        self._cred_path.write_text(creds.serialize())
        self._credentials = creds
        logger.info(f'[{self.name}] Credentials refreshed and saved')

    async def keep_fresh_loop(self) -> None:
        """Continuously keep credentials fresh."""
        while True:
            await self.ensure_fresh()
            await asyncio.sleep(random.uniform(300, 600))
