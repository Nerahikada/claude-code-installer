#!/usr/bin/env python3
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

PUBLIC_DIR = Path('public')
RATE_LIMIT = 10            # requests per IP
RATE_WINDOW = 60.0         # ...within this many seconds

_request_log: dict[str, list[float]] = defaultdict(list)


def _client_ip(req: Request) -> str:
    for h in ('x-forwarded-for', 'x-real-ip'):
        v = req.headers.get(h)
        if v:
            return v.split(',')[0].strip()
    return req.client.host if req.client else 'unknown'


def _rate_limit_ok(ip: str) -> bool:
    now = time.monotonic()
    log = [t for t in _request_log[ip] if now - t < RATE_WINDOW]
    _request_log[ip] = log
    if len(log) >= RATE_LIMIT:
        return False
    log.append(now)
    return True


def build_app(provider) -> Starlette:
    async def get_token(req: Request) -> Response:
        ip = _client_ip(req)
        if not _rate_limit_ok(ip):
            logger.warning(f'Rate limit exceeded for {ip}')
            return JSONResponse({'error': 'Rate limit exceeded'}, status_code=429)

        if req.path_params['provider'] != provider.name:
            return JSONResponse({'error': 'Unknown provider'}, status_code=404)

        logger.debug(f'{ip} GET /api/tokens/{provider.name}')
        try:
            client_json = await provider.token_for_client()
        except Exception as e:
            logger.error(f'[{provider.name}] Failed to provide token: {e}')
            return JSONResponse({'error': 'Token unavailable'}, status_code=500)

        return PlainTextResponse(client_json, media_type='application/json')

    return Starlette(routes=[
        Route('/api/tokens/{provider}', get_token),
        Mount('/', app=StaticFiles(directory=str(PUBLIC_DIR), html=True)),
    ])
