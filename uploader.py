# uploader.py

import asyncio, logging
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.types import DocumentAttributeVideo
from config import UPLOAD_DELAY
from state import user_states

logger = logging.getLogger(__name__)
user_locks = {}
user_progress_msgs = {}

async def upload_worker(bot, send_queue):
    while True:
        info    = await send_queue.get()
        uid     = info["uid"]
        fileobj = info["fileobj"]
        is_video= info["is_video"]
        is_photo= info["is_photo"]
        dur     = info["duration"]
        w       = info["width"]
        h       = info["height"]
        cap     = info["caption"]

        if fileobj is None:
            logger.error(f"[UPLOAD ERROR] Missing buffer for {uid}")
            send_queue.task_done()
            continue

        # ensure correct extension
        name = getattr(fileobj, "name", "")
        if is_video and not name.lower().endswith(".mp4"):
            fileobj.name = name.rsplit(".",1)[0] + ".mp4"
        if is_photo and not any(name.lower().endswith(ext) for ext in (".jpg",".png")):
            fileobj.name = name.rsplit(".",1)[0] + ".jpg"

        lock = user_locks.setdefault(uid, asyncio.Lock())
        async with lock:
            st     = user_states.setdefault(uid,{})
            total  = st.get("batch_total",0)
            waiting= st.get("waiting_batch",0)
            sent   = total - waiting + 1 if total and waiting is not None else 1

            # progress
            prev = user_progress_msgs.get(uid)
            txt  = f"📤 {sent}/{total or 1}"
            if prev:
                try: await bot.edit_message(uid, prev, txt)
                except: pass
            else:
                msg = await bot.send_message(uid, txt)
                user_progress_msgs[uid] = msg.id

            try:
                if is_video:
                    attr = DocumentAttributeVideo(
                        duration=int(dur or 0),
                        w=int(w or 0),
                        h=int(h or 0),
                        supports_streaming=True
                    )
                    kwargs = {
                        "file": fileobj,
                        "caption": cap,
                        "attributes": [attr],
                        "force_document": False
                    }
                elif is_photo:
                    kwargs = {
                        "file": fileobj,
                        "caption": cap,
                        "force_document": False
                    }
                else:
                    kwargs = {
                        "file": fileobj,
                        "caption": cap,
                        "force_document": True
                    }

                # send with flood‑wait handling
                while True:
                    try:
                        await bot.send_file(**kwargs, entity=uid)
                        break
                    except FloodWaitError as e:
                        logger.warning(f"⚠️ FloodWait {e.seconds}s")
                        await asyncio.sleep(e.seconds+1)

                # configured delay
                if UPLOAD_DELAY:
                    await asyncio.sleep(UPLOAD_DELAY)

            except Exception as e:
                logger.error(f"[UPLOAD ERROR] {e}", exc_info=True)
            finally:
                try: fileobj.close()
                except: pass

                # batch wrap‑up
                if waiting is not None:
                    st["waiting_batch"] = waiting - 1
                    if st["waiting_batch"] <= 0:
                        last = user_progress_msgs.pop(uid,None)
                        if last:
                            try: await bot.delete_messages(uid,last)
                            except: pass
                        try:
                            await bot.send_message(uid, f"✅ All {total}/{total} files uploaded!")
                        except: pass

                send_queue.task_done()
