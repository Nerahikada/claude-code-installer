#!/usr/bin/env python3
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from credentials.base import CredentialProvider

from credentials.base import CredentialSplitError

PUBLIC_DIR = Path('public')

_providers: dict[str, CredentialProvider] = {}


def register_provider(provider: CredentialProvider) -> None:
    _providers[provider.name] = provider


class RateLimiter:
    """IP-based sliding-window rate limiter."""

    def __init__(self, max_requests: int = 10, window_seconds: float = 60) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        timestamps = [t for t in self._requests[ip] if now - t < self._window]
        if len(timestamps) >= self._max_requests:
            self._requests[ip] = timestamps
            return False
        timestamps.append(now)
        self._requests[ip] = timestamps
        return True


_rate_limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = request.headers.get('x-real-ip')
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else 'unknown'


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply rate limiting to /api/* routes."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith('/api/'):
            ip = _client_ip(request)
            if not _rate_limiter.is_allowed(ip):
                logger.warning(f'Rate limit exceeded for {ip} on {request.url.path}')
                return JSONResponse({'error': 'Rate limit exceeded'}, status_code=429)
        return await call_next(request)


async def get_credentials(request: Request) -> Response:
    provider_name = request.path_params['provider']
    provider = _providers.get(provider_name)
    if provider is None:
        return JSONResponse({'error': f'Unknown provider: {provider_name}'}, status_code=404)

    if provider.credentials is None:
        return JSONResponse({'error': 'Credentials not yet available'}, status_code=503)

    logger.debug(f'{_client_ip(request)} GET /api/credentials/{provider_name}')

    try:
        client_creds = await provider.generate_for_client()
    except CredentialSplitError as e:
        logger.warning(f'[{provider_name}] {e}')
        return JSONResponse(
            {'error': 'Credential generation temporarily unavailable'},
            status_code=503,
            headers={'Retry-After': '1'},
        )
    except Exception as e:
        logger.error(f'[{provider_name}] Failed to generate client credentials: {e}')
        return JSONResponse({'error': 'Credential generation failed'}, status_code=500)

    return PlainTextResponse(client_creds.serialize(), media_type='application/json')


routes = [
    Route('/api/credentials/{provider}', get_credentials),
    Mount('/', app=StaticFiles(directory=str(PUBLIC_DIR), html=True)),
]

app = Starlette(
    routes=routes,
    middleware=[Middleware(RateLimitMiddleware)],
)


async def run_server(host: str, port: int) -> None:
    """Run the HTTP server asynchronously."""
    config = uvicorn.Config(app, host=host, port=port, log_level='warning', access_log=False)
    server = uvicorn.Server(config)
    logger.info(f'Server running at http://{host}:{port}')
    await server.serve()
