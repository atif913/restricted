import logging, asyncio, os
from telethon.errors.rpcerrorlist import FloodWaitError
from tele_utils import get_user_client, load_all_dialogs, user_dialogs_cache
from config import DOWNLOAD_DIR

logger = logging.getLogger(__name__)
task_queue = None
send_queue = None

async def download_worker():
    # allow 8× concurrent tasks per CPU
    sem = asyncio.Semaphore(os.cpu_count() * 8 or 32)

    while True:
        # throttle if uploads are lagging
        if send_queue.qsize() > 50:
            await asyncio.sleep(0.5)

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
                await asyncio.sleep(e.seconds + 1)
                task_queue.task_done()
                continue

            if not msg or not msg.media:
                logger.warning("⚠️ No media")
                task_queue.task_done()
                continue

            duration = getattr(msg.video, "duration", None)
            width    = getattr(msg.video, "w", getattr(msg.video, "width", None))
            height   = getattr(msg.video, "h", getattr(msg.video, "height", None))

            # ── download directly to disk ─────────────────────
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            ext = ".mp4" if msg.video else ".jpg" if msg.photo else ""
            path = os.path.join(DOWNLOAD_DIR, f"{mid}{ext}")
            try:
                await client.download_media(msg, path)
            except Exception as e:
                logger.error(f"❌ File download failed: {e}")
                task_queue.task_done()
                continue

            # enqueue for upload by filepath
            await send_queue.put({
                "uid": uid,
                "filepath": path,
                "is_video": bool(msg.video),
                "is_photo": bool(msg.photo),
                "duration": duration,
                "width": width,
                "height": height,
                "caption": msg.text or ""
            })

        task_queue.task_done()
