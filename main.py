#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import random
from pathlib import Path

from loguru import logger

from claude import Credentials, pull_refresh_image
from serv import run_server, RequestEvent, request_events


async def keep_claude_fresh(cred_path: Path) -> None:
    while True:
        try:
            creds = Credentials(cred_path.read_text())
            if creds.is_expired:
                new_creds = await creds.refresh()
                if new_creds is None:
                    logger.error(f'Re-login required to refresh credentials ({cred_path})')
                else:
                    cred_path.write_text(str(new_creds))
                    logger.info(f'Credentials refreshed and saved to {cred_path}')
        except Exception as e:
            logger.error(f'Failed to refresh credentials ({cred_path}): {e}')
        await asyncio.sleep(random.uniform(300, 600))  # check every 5-10 minutes


async def generate_new_credentials(src: Path, dest: Path) -> None:
    src_creds = Credentials(src.read_text())
    results = await asyncio.gather(
        src_creds.refresh(force=True),
        src_creds.refresh(force=True),
        return_exceptions=True,
    )
    valid = [r for r in results if isinstance(r, Credentials)]

    if len(valid) == 2:
        src.write_text(str(valid[0]))
        dest.write_text(str(valid[1]))
        logger.info(f'Successfully generated new credentials to {dest}')
    elif len(valid) == 1:
        src.write_text(str(valid[0]))
        logger.warning('Only one set of credentials was refreshed, retrying...')
        await generate_new_credentials(src, dest)
    else:
        raise RuntimeError(f'Failed to generate new credentials from {src} to {dest}')


async def keep_image_fresh() -> None:
    while True:
        await asyncio.sleep(random.uniform(3600, 7200))  # check every 1-2 hours
        try:
            await pull_refresh_image()
        except Exception as e:
            logger.error(f'Failed to pull refresh image: {e}')


async def main() -> None:
    await pull_refresh_image()
    await generate_new_credentials(Path('.credentials.json'), Path('public/.credentials.json'))

    regenerate_lock = asyncio.Lock()

    async def on_credentials_access(event: RequestEvent):
        if regenerate_lock.locked():
            logger.debug('Credential regeneration already in progress, skipping')
            return
        async with regenerate_lock:
            try:
                await generate_new_credentials(Path('.credentials.json'), Path('public/.credentials.json'))
            except Exception as e:
                logger.error(f'Failed to generate new credentials on access: {e}')

    request_events.on('/.credentials.json', on_credentials_access)

    await asyncio.gather(
        run_server('0.0.0.0', 46510),
        keep_claude_fresh(Path('.credentials.json')),
        keep_claude_fresh(Path('public/.credentials.json')),
        keep_image_fresh(),
    )


if __name__ == '__main__':
    asyncio.run(main())
