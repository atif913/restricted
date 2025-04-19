# download.py

import logging, asyncio, os
from io import BytesIO
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.types import DocumentAttributeVideo
from tele_utils import get_user_client, load_all_dialogs, user_dialogs_cache
from config import DOWNLOAD_DIR

logger = logging.getLogger(__name__)
task_queue = None
send_queue = None

async def download_worker():
    # high concurrency
    sem = asyncio.Semaphore(os.cpu_count() * 2 or 10)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    while True:
        uid, cid, mid, priv = await task_queue.get()
        async with sem:
            logger.info(f"🛠 [Download] uid={uid} cid={cid} mid={mid} priv={priv}")
            client = await get_user_client(uid)
            if priv and uid not in user_dialogs_cache:
                await load_all_dialogs(client, uid)
            entity = user_dialogs_cache.get(uid, {}).get(cid) if priv else cid
            if not entity:
                logger.warning("⚠️ Chat not found")
                task_queue.task_done()
                continue

            try:
                msg = await client.get_messages(entity, ids=mid)
            except FloodWaitError as e:
                logger.warning(f"⚠️ FloodWait {e.seconds}s")
                await asyncio.sleep(e.seconds+1)
                task_queue.task_done()
                continue

            if not msg or not msg.media:
                logger.warning("⚠️ No media")
                task_queue.task_done()
                continue

            # extract video metadata from API
            duration = getattr(msg.video, "duration", None)
            width    = getattr(msg.video, "w", getattr(msg.video, "width", None))
            height   = getattr(msg.video, "h", getattr(msg.video, "height", None))

            # build in‑memory buffer with correct extension
            buf = BytesIO()
            if msg.video:
                buf.name = f"{mid}.mp4"
            elif msg.photo:
                buf.name = f"{mid}.jpg"
            else:
                buf.name = getattr(msg.file, "name", str(mid))

            try:
                await client.download_media(msg, buf)
                buf.seek(0)
            except Exception as e:
                logger.error(f"❌ Stream download failed: {e}")
                buf.close()
                task_queue.task_done()
                continue

            # enqueue for upload
            await send_queue.put({
                "uid": uid,
                "fileobj": buf,
                "is_video": bool(msg.video),
                "is_photo": bool(msg.photo),
                "duration": duration,
                "width": width,
                "height": height,
                "caption": msg.text or ""
            })
            logger.info("🚀 Enqueued upload")

        task_queue.task_done()
