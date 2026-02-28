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
            with open(cred_path, 'r') as f:
                creds = Credentials(f.read())
            if creds.is_expired:
                new_creds = await creds.refresh()
                if new_creds is None:
                    logger.error(f'Re-login required to refresh credentials ({cred_path})')
                else:
                    with open(cred_path, 'w') as f:
                        f.write(str(new_creds))
                    logger.info(f'Credentials refreshed and saved to {cred_path}')
        except Exception as e:
            logger.error(f'Failed to refresh credentials ({cred_path}): {e}')
        await asyncio.sleep(random.uniform(300, 600))  # check every 5-10 minutes


async def generate_new_credentials(src: Path, dest: Path) -> None:
    with open(src, 'r') as f_src:
        src_creds = Credentials(f_src.read())
    creds1, creds2 = await asyncio.gather(
        src_creds.refresh(force=True),
        src_creds.refresh(force=True),
        return_exceptions=True,
    )

    # 2 OK
    if isinstance(creds1, Credentials) and isinstance(creds2, Credentials):
        with open(src, 'w') as f_src:
            f_src.write(str(creds1))
        with open(dest, 'w') as f_dest:
            f_dest.write(str(creds2))
        logger.info(f'Successfully generated new credentials to {dest}')
    
    # 1 OK
    elif isinstance(creds1, Credentials) or isinstance(creds2, Credentials):
        valid_creds = creds1 if isinstance(creds1, Credentials) else creds2
        with open(src, 'w') as f_src:
            f_src.write(str(valid_creds))
        logger.warning('Only one set of credentials was refreshed, retrying...')
        await generate_new_credentials(src, dest)
    
    # 0 OK
    else:
        logger.error('Failed to refresh credentials concurrently')
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
