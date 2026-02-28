#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import copy
import json
import shutil
import tempfile
import time

from loguru import logger
from python_on_whales import DockerClient, DockerException

_detected_client: list[str] | None = None


async def _check_docker_permission(cmd: list[str]) -> bool:
    """Check if docker command works with given prefix."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, 'info',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        return False


async def detect_container_client() -> list[str]:
    """
    Auto-detect available container client.

    Priority: podman > docker > sudo docker

    Returns:
        Command list for DockerClient(client_call=...).

    Raises:
        RuntimeError: If no container client is available.
    """
    global _detected_client
    if _detected_client is not None:
        logger.debug(f'Using cached container client: {_detected_client}')
        return _detected_client

    logger.debug('Detecting container client...')

    if shutil.which('podman'):
        logger.debug('Found podman')
        _detected_client = ['podman']
        return _detected_client

    if shutil.which('docker'):
        logger.debug('Found docker, checking if it works without sudo...')
        if await _check_docker_permission(['docker']):
            logger.debug('Docker works without sudo')
            _detected_client = ['docker']
            return _detected_client

        logger.debug('Docker requires elevated privileges, trying sudo...')
        if shutil.which('sudo'):
            if await _check_docker_permission(['sudo', '-n', 'docker']):
                logger.debug('Docker works with sudo')
                _detected_client = ['sudo', 'docker']
                return _detected_client
            logger.debug('sudo docker failed (may require password)')
        else:
            logger.debug('sudo not found')
    else:
        logger.debug('Neither podman nor docker found in PATH')

    raise RuntimeError('No container client found. Install podman or docker.')


class CredentialRefreshError(Exception):
    """Raised when credential refresh fails unexpectedly."""


class Credentials:
    """Manages Claude AI OAuth credentials with refresh capability."""

    def __init__(self, credentials: str) -> None:
        self._data = self._parse(credentials)
        self._raw = credentials

    @staticmethod
    def _parse(credentials: str) -> dict:
        try:
            data = json.loads(credentials)
        except json.JSONDecodeError as e:
            raise ValueError('Invalid JSON format') from e

        if 'claudeAiOauth' not in data:
            raise ValueError('Missing required key: claudeAiOauth')
        return data

    @property
    def expires_at(self) -> float:
        """Returns expiration time as Unix timestamp in seconds."""
        return self._data.get('claudeAiOauth', {}).get('expiresAt', 0) / 1000

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def access_token(self) -> str:
        """Returns the current access token."""
        return self._data.get('claudeAiOauth', {}).get('accessToken', '')

    @property
    def refresh_token(self) -> str:
        """Returns the current refresh token."""
        return self._data.get('claudeAiOauth', {}).get('refreshToken', '')

    def has_same_tokens(self, other: Credentials) -> bool:
        """Check if access and refresh tokens are identical."""
        return self.access_token == other.access_token and self.refresh_token == other.refresh_token

    def __str__(self) -> str:
        return self._raw

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Credentials):
            return NotImplemented
        return self._data == other._data

    async def refresh(self, force: bool = False) -> Credentials | None:
        """
        Refresh credentials using container-based refresh mechanism.

        Args:
            force: If True, force refresh even if credentials haven't expired.

        Returns:
            New Credentials instance if refresh succeeded, None if re-login is required.

        Raises:
            CredentialRefreshError: If refresh fails unexpectedly.
        """
        logger.debug(f'Refreshing credentials (expired={self.is_expired}, force={force})')

        data = copy.deepcopy(self._data)
        if force:
            data['claudeAiOauth']['expiresAt'] = int((time.time() - 1) * 1000)

        with tempfile.NamedTemporaryFile() as fp:
            fp.write(json.dumps(data).encode())
            fp.flush()

            client = await detect_container_client()
            docker = DockerClient(client_call=client)

            try:
                output = await asyncio.to_thread(
                    docker.run,
                    'ghcr.io/nerahikada/hello-claude',
                    volumes=[(fp.name, '/root/.claude/.credentials.json')],
                    pull='always',
                    remove=True,
                )
            except DockerException as e:
                if 'Please run /login' in str(e.stdout):
                    logger.debug(e.stdout.strip() if e.stdout else e.stdout)
                    return None
                raise

            if 'hello' not in output.lower():
                raise CredentialRefreshError(f'Unexpected output: {output}')

            fp.seek(0)
            new_creds = Credentials(fp.read().decode())

            if new_creds.has_same_tokens(self):
                if force:
                    logger.debug('Tokens were not refreshed despite force flag, please re-login')
                    return None
                if self.is_expired:
                    logger.debug('Tokens have expired and could not be refreshed, please re-login')
                    return None
                logger.debug('Tokens are still valid, no refresh needed')
            else:
                logger.debug(f'Tokens refreshed successfully (new expiration: {new_creds.expires_at})')

            return new_creds
