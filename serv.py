#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import mimetypes
import time
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from loguru import logger

if TYPE_CHECKING:
    from credentials.base import CredentialProvider

PUBLIC_DIR = Path('public')


class RateLimiter:
    """IP-based rate limiter using a sliding window."""

    def __init__(self, max_requests: int = 10, window_seconds: float = 60) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        timestamps = self._requests[ip]
        # Remove expired entries
        self._requests[ip] = [t for t in timestamps if now - t < self._window]
        if len(self._requests[ip]) >= self._max_requests:
            return False
        self._requests[ip].append(now)
        return True


# Global state set by run_server
_providers: dict[str, CredentialProvider] = {}
_rate_limiter = RateLimiter()
_loop: asyncio.AbstractEventLoop | None = None


def register_provider(provider: CredentialProvider) -> None:
    _providers[provider.name] = provider


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for static files and API endpoints."""

    def log_message(self, format: str, *args) -> None:
        pass

    def _get_client_ip(self) -> str:
        forwarded = self.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        real_ip = self.headers.get('X-Real-IP')
        if real_ip:
            return real_ip.strip()
        return self.client_address[0]

    def _send_json(self, status: HTTPStatus, data: dict | str) -> None:
        body = data if isinstance(data, str) else json.dumps(data)
        encoded = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error(self, status: HTTPStatus, message: str | None = None) -> None:
        if message:
            self._send_json(status, {'error': message})
        else:
            self.send_response(status)
            self.end_headers()

    def _handle_api(self, path: str) -> None:
        """Route API requests."""
        client_ip = self._get_client_ip()

        # /api/credentials/<provider>
        parts = path.strip('/').split('/')
        if len(parts) == 3 and parts[0] == 'api' and parts[1] == 'credentials':
            provider_name = parts[2]
            self._handle_credentials(provider_name, client_ip)
            return

        self._send_error(HTTPStatus.NOT_FOUND, 'Unknown API endpoint')

    def _handle_credentials(self, provider_name: str, client_ip: str) -> None:
        if not _rate_limiter.is_allowed(client_ip):
            logger.warning(f'Rate limit exceeded for {client_ip} on /api/credentials/{provider_name}')
            self._send_error(HTTPStatus.TOO_MANY_REQUESTS, 'Rate limit exceeded')
            return

        provider = _providers.get(provider_name)
        if provider is None:
            self._send_error(HTTPStatus.NOT_FOUND, f'Unknown provider: {provider_name}')
            return

        if provider.credentials is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, 'Credentials not yet available')
            return

        # Generate independent client credentials (dual-refresh)
        if _loop and _loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(provider.generate_for_client(), _loop)
                client_creds = future.result(timeout=60)
                logger.debug(f'{client_ip} GET /api/credentials/{provider_name}')
                self._send_json(HTTPStatus.OK, client_creds.serialize())
                return
            except Exception as e:
                logger.error(f'[{provider_name}] Failed to generate client credentials: {e}')
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'Credential generation failed')
                return

        self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, 'Server not ready')

    def _handle_static(self) -> None:
        """Serve static files from PUBLIC_DIR."""
        parsed = urlparse(self.path)
        request_path = parsed.path.lstrip('/')
        if not request_path:
            request_path = 'index.html'

        file_path = (PUBLIC_DIR / request_path).resolve()

        if not file_path.is_relative_to(PUBLIC_DIR.resolve()) or not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = 'application/octet-stream'

        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(file_path.stat().st_size))
        self.end_headers()

        with open(file_path, 'rb') as f:
            self.wfile.write(f.read())

        client_ip = self._get_client_ip()
        logger.debug(f'{client_ip} "{self.command} {self.path}" {HTTPStatus.OK.value} {file_path.stat().st_size}')

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self._handle_api(parsed.path)
        else:
            self._handle_static()


async def run_server(host: str, port: int) -> None:
    """Run the HTTP server asynchronously."""
    global _loop
    _loop = asyncio.get_running_loop()
    server = HTTPServer((host, port), RequestHandler)
    logger.info(f'Server running at http://{host}:{port}')
    await asyncio.to_thread(server.serve_forever)
