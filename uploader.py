import asyncio, logging, os
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.types import DocumentAttributeVideo
from config import UPLOAD_DELAY
from state import user_states

logger = logging.getLogger(__name__)
user_locks = {}
user_progress_msgs = {}

async def upload_worker(bot, send_queue):
    while True:
        info     = await send_queue.get()
        uid      = info["uid"]
        filepath = info.get("filepath")
        is_video = info.get("is_video")
        is_photo = info.get("is_photo")
        dur      = info.get("duration")
        w        = info.get("width")
        h        = info.get("height")
        cap      = info.get("caption")

        lock = user_locks.setdefault(uid, asyncio.Lock())
        async with lock:
            st      = user_states.setdefault(uid, {})
            total   = st.get("batch_total", 0)
            waiting = st.get("waiting_batch", 0)
            sent    = total - waiting + 1 if total and waiting is not None else 1

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
                    # ── generate thumbnail ─────────────────────────
                    thumb = filepath + ".thumb.jpg"
                    # grab a frame at 1 second
                    os.system(f'ffmpeg -y -i "{filepath}" -ss 00:00:01 -vframes 1 "{thumb}"')

                    attr = DocumentAttributeVideo(
                        duration=int(dur or 0), w=int(w or 0), h=int(h or 0), supports_streaming=True
                    )
                    kwargs = {
                        "file": filepath,
                        "caption": cap,
                        "attributes": [attr],
                        "thumbnail": thumb,
                        "force_document": False
                    }
                elif is_photo:
                    kwargs = {
                        "file": filepath,
                        "caption": cap,
                        "force_document": False
                    }
                else:
                    kwargs = {
                        "file": filepath,
                        "caption": cap,
                        "force_document": True
                    }

                # send with flood-wait handling
                while True:
                    try:
                        await bot.send_file(entity=uid, **kwargs)
                        break
                    except FloodWaitError as e:
                        logger.warning(f"⚠️ FloodWait {e.seconds}s")
                        await asyncio.sleep(e.seconds + 1)

                if UPLOAD_DELAY:
                    await asyncio.sleep(UPLOAD_DELAY)

            except Exception as e:
                logger.error(f"[UPLOAD ERROR] {e}", exc_info=True)
            finally:
                # cleanup files
                if filepath and os.path.exists(filepath):
                    try: os.remove(filepath)
                    except: pass
                thumb = filepath + ".thumb.jpg"
                if os.path.exists(thumb):
                    try: os.remove(thumb)
                    except: pass

                if waiting is not None:
                    st["waiting_batch"] = waiting - 1
                    if st["waiting_batch"] <= 0:
                        last = user_progress_msgs.pop(uid, None)
                        if last:
                            try: await bot.delete_messages(uid, last)
                            except: pass
                        try:
                            await bot.send_message(uid, f"✅ All {total}/{total} files uploaded!")
                        except: pass

                send_queue.task_done()
