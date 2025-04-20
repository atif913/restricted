import os
import asyncio
import logging
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    logging.info("⚡ Using uvloop")
except ImportError:
    pass

from telethon import TelegramClient
from config import (
    API_ID, API_HASH, BOT_TOKEN,
    DOWNLOAD_DIR, SESSIONS_DIR,
    WORKER_COUNT, SUB_CLEANUP_INTERVAL
)
from auth import cleanup_authorized
from download import download_worker
from uploader import upload_worker
from handlers import register_handlers

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)

    bot = TelegramClient("bot_session", API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    logger.info("✅ Bot connected successfully")

    # start cleanup loop
    asyncio.create_task(cleanup_authorized())
    logger.info(f"🛡️  Started auth cleanup loop (every {SUB_CLEANUP_INTERVAL}s)")

    # initialize queues
    import download
    download.task_queue = asyncio.Queue()
    download.send_queue = asyncio.Queue()
    logger.info("⚙️  Queues initialized")

    # register handlers
    register_handlers(bot, download.task_queue, download.send_queue)
    logger.info("🔗 Handlers registered")

    # launch many download workers
    dl_workers = WORKER_COUNT * 16
    for _ in range(dl_workers):
        asyncio.create_task(download_worker())
    logger.info(f"🚀 Launched {dl_workers} download workers")

    # launch upload workers
    ul_workers = WORKER_COUNT * 8
    for _ in range(ul_workers):
        asyncio.create_task(upload_worker(bot, download.send_queue))
    logger.info(f"🚀 Launched {ul_workers} upload workers")

    try:
        await bot.run_until_disconnected()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Shutdown cleanly")

if __name__ == "__main__":
    asyncio.run(main())
