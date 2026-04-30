#!/usr/bin/env python3
from __future__ import annotations

import asyncio

import uvicorn
from loguru import logger

from serv import build_app
from tokens.claude import ClaudeProvider

HOST = '0.0.0.0'
PORT = 46510


async def main() -> None:
    provider = ClaudeProvider()
    await provider.force_refresh()
    logger.info(f'[{provider.name}] Provider ready')

    config = uvicorn.Config(build_app(provider), host=HOST, port=PORT,
                            log_level='warning', access_log=False)
    server = uvicorn.Server(config)
    logger.info(f'Server running at http://{HOST}:{PORT}')

    await asyncio.gather(server.serve(), provider.keep_fresh_loop())


if __name__ == '__main__':
    asyncio.run(main())
