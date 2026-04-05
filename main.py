#!/usr/bin/env python3
from __future__ import annotations
import asyncio

from loguru import logger

from credentials.claude import ClaudeProvider
from serv import register_provider, run_server


async def main() -> None:
    providers = [
        ClaudeProvider(),
    ]

    for provider in providers:
        provider.load()
        register_provider(provider)
        logger.info(f'[{provider.name}] Provider registered')

    await asyncio.gather(
        run_server('0.0.0.0', 46510),
        *(provider.keep_fresh_loop() for provider in providers),
    )


if __name__ == '__main__':
    asyncio.run(main())
