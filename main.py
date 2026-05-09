#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from loguru import logger

from serv import build_app
from tokens.claude import ClaudeProvider

HOST = '0.0.0.0'
PORT = 46510


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


async def main() -> None:
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    provider = ClaudeProvider()
    await provider.force_refresh()
    logger.info(f'[{provider.name}] Provider ready')

    config = uvicorn.Config(build_app(provider), host=HOST, port=PORT, log_config=None)
    server = uvicorn.Server(config)
    logger.info(f'Server running at http://{HOST}:{PORT}')

    await asyncio.gather(server.serve(), provider.keep_fresh_loop())


if __name__ == '__main__':
    asyncio.run(main())
