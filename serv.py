#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import fnmatch
import inspect
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Awaitable, Callable, Union
from urllib.parse import urlparse

from loguru import logger

PUBLIC_DIR = Path('public')


@dataclass
class RequestEvent:
    path: str
    client_ip: str
    status: HTTPStatus
    file_path: Path | None


RequestCallback = Union[
    Callable[[RequestEvent], None],
    Callable[[RequestEvent], Awaitable[None]],
]


class RequestEventEmitter:
    def __init__(self):
        self._listeners: list[tuple[str | None, RequestCallback]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the event loop for async callbacks."""
        self._loop = loop

    def on(self, pattern: str | None, callback: RequestCallback) -> None:
        """Register a callback. pattern=None means all requests."""
        self._listeners.append((pattern, callback))

    def off(self, callback: RequestCallback) -> None:
        """Unregister a callback."""
        self._listeners = [(p, cb) for p, cb in self._listeners if cb != callback]

    def emit(self, event: RequestEvent) -> None:
        for pattern, callback in self._listeners:
            if pattern is None or fnmatch.fnmatch(event.path, pattern):
                try:
                    if inspect.iscoroutinefunction(callback):
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(callback(event), self._loop)
                    else:
                        callback(event)
                except Exception as e:
                    logger.error(f'Event callback error: {e}')


request_events = RequestEventEmitter()


class StaticFileHandler(BaseHTTPRequestHandler):
    """Simple static file server handler."""

    def log_message(self, format: str, *args) -> None:
        """Suppress default logging."""
        pass

    def _get_client_ip(self) -> str:
        """Get real client IP, checking proxy headers."""
        forwarded = self.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        real_ip = self.headers.get('X-Real-IP')
        if real_ip:
            return real_ip.strip()
        return self.client_address[0]

    def _log_request(self, status: HTTPStatus, file_path: Path | None = None) -> None:
        """Log request with status and optional file path."""
        client = self._get_client_ip()
        size = file_path.stat().st_size if file_path else '-'
        logger.debug(f'{client} "{self.command} {self.path}" {status.value} {size}')

        event = RequestEvent(
            path=urlparse(self.path).path,
            client_ip=client,
            status=status,
            file_path=file_path,
        )
        request_events.emit(event)

    def _resolve_path(self) -> Path | None:
        """Resolve request path to a safe file path within PUBLIC_DIR."""
        parsed = urlparse(self.path)
        request_path = parsed.path.lstrip('/')
        if not request_path:
            request_path = 'index.html'

        file_path = (PUBLIC_DIR / request_path).resolve()

        # Prevent path traversal attacks
        if not file_path.is_relative_to(PUBLIC_DIR.resolve()):
            return None

        return file_path if file_path.is_file() else None

    def _send_file(self, file_path: Path) -> None:
        """Send a file with appropriate content type."""
        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = 'application/octet-stream'

        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(file_path.stat().st_size))
        self.end_headers()

        with open(file_path, 'rb') as f:
            self.wfile.write(f.read())

        self._log_request(HTTPStatus.OK, file_path)

    def _send_error(self, status: HTTPStatus) -> None:
        """Send an error response."""
        self.send_response(status)
        self.end_headers()
        self._log_request(status)

    def do_GET(self) -> None:
        file_path = self._resolve_path()
        if file_path:
            self._send_file(file_path)
        else:
            self._send_error(HTTPStatus.NOT_FOUND)


async def run_server(host: str, port: int) -> None:
    """Run the HTTP server asynchronously."""
    request_events.set_loop(asyncio.get_running_loop())
    server = HTTPServer((host, port), StaticFileHandler)
    logger.info(f'Server running at http://{host}:{port}')
    await asyncio.to_thread(server.serve_forever)
