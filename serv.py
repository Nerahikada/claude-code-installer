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
POLICY_DIR = Path('policy')
RATE_LIMIT = 10            # requests per IP
RATE_WINDOW = 60.0         # ...within this many seconds

# The text content types are deliberate: Windows PowerShell 5.1 hands back a
# Byte[] for anything it does not recognise as text.
POLICY_FILES = {
    'apply.py': 'text/plain; charset=utf-8',
    'apply.ps1': 'text/plain; charset=utf-8',
    'policy.json': 'application/json',
}

# This prefix is rewritten to the caller's own origin on the way out. The
# apply scripts carry it so that they also work when fetched from GitHub.
GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/Nerahikada/claude-code-installer/master'

_request_log: dict[str, list[float]] = defaultdict(list)


def _client_ip(req: Request) -> str:
    for h in ('x-forwarded-for', 'x-real-ip'):
        v = req.headers.get(h)
        if v:
            return v.split(',')[0].strip()
    return req.client.host if req.client else 'unknown'


def _origin(req: Request) -> str:
    """The origin the client reached us on. Cloudflare terminates TLS, so the
    scheme survives only in X-Forwarded-Proto."""
    scheme = req.headers.get('x-forwarded-proto', req.url.scheme)
    host = req.headers.get('x-forwarded-host') or req.headers.get('host') or req.url.netloc
    return f'{scheme.split(",")[0].strip()}://{host.split(",")[0].strip()}'


def _rate_limit_ok(ip: str) -> bool:
    now = time.monotonic()
    log = [t for t in _request_log[ip] if now - t < RATE_WINDOW]
    _request_log[ip] = log
    if len(log) >= RATE_LIMIT:
        return False
    log.append(now)
    return True


async def get_policy(req: Request) -> Response:
    """Serve an apply script or the policy itself, rewritten to point at this host.

    This is the one path excluded from ZTNA: the machine being set up runs
    `curl | python3`, with no browser to complete an interactive login.
    """
    name = req.path_params['name']
    media_type = POLICY_FILES.get(name)
    if media_type is None:
        return PlainTextResponse('Not found', status_code=404)

    text = (POLICY_DIR / name).read_text(encoding='utf-8')
    return Response(text.replace(GITHUB_RAW_BASE, _origin(req)), media_type=media_type)


def build_app(providers: list) -> Starlette:
    _providers = {p.name: p for p in providers}

    async def get_token(req: Request) -> Response:
        ip = _client_ip(req)
        if not _rate_limit_ok(ip):
            logger.warning(f'Rate limit exceeded for {ip}')
            return JSONResponse({'error': 'Rate limit exceeded'}, status_code=429)

        provider = _providers.get(req.path_params['provider'])
        if provider is None:
            return JSONResponse({'error': 'Unknown provider'}, status_code=404)

        try:
            client_json = await provider.token_for_client()
        except Exception as e:
            logger.error(f'[{provider.name}] Failed to provide token: {e}')
            return JSONResponse({'error': 'Token unavailable'}, status_code=500)

        return Response(client_json, media_type='application/json')

    return Starlette(routes=[
        Route('/api/tokens/{provider}', get_token),
        Route('/policy/{name}', get_policy),
        Mount('/', app=StaticFiles(directory=str(PUBLIC_DIR), html=True)),
    ])
