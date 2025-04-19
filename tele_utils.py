# tele_utils.py — Patched to handle FloodWait and add throttling

import os
import re
import asyncio
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from config import API_ID, API_HASH, SESSIONS_DIR
try:
    from telethon.errors import FloodWait
except ImportError:
    from telethon.errors.rpcerrorlist import FloodWaitError as FloodWait

# Caches
user_clients = {}       # uid -> TelegramClient instance
user_dialogs_cache = {} # uid -> {chat_id: entity}

async def get_user_client(uid: int) -> TelegramClient:
    """Return or create a Telethon client for user `uid`."""
    if uid in user_clients:
        client = user_clients[uid]
        if not client.is_connected():
            await client.connect()
        return client

    session_file = os.path.join(SESSIONS_DIR, f"user_{uid}")
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    user_clients[uid] = client
    return client

async def disconnect_user_client(uid: int):
    """
    Disconnect and remove the user client and clear its dialog cache.
    """
    client = user_clients.pop(uid, None)
    if client:
        try:
            await client.disconnect()
        except:
            pass
    user_dialogs_cache.pop(uid, None)

async def load_all_dialogs(client: TelegramClient, uid: int):
    """Load and cache all of `uid`'s dialogs, with flood-wait handling and throttling."""
    try:
        result = await client(GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=100,
            hash=0
        ))
    except FloodWait as e:
        # If flood wait occurs here, sleep and retry
        await asyncio.sleep(e.seconds)
        result = await client(GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=100,
            hash=0
        ))

    mapping = {}
    for dlg in result.dialogs:
        try:
            try:
                ent = await client.get_entity(dlg.peer)
            except FloodWait as e:
                # Sleep for the required wait and retry
                await asyncio.sleep(e.seconds)
                ent = await client.get_entity(dlg.peer)
            except Exception:
                continue

            cid = getattr(ent, "id", None)
            if cid:
                mapping[cid] = ent
                mapping[-100 * cid] = ent

            # Brief throttle between API calls to avoid flood
            await asyncio.sleep(0.2)
        except Exception:
            continue

    user_dialogs_cache[uid] = mapping

def extract_message_info(link: str):
    """
    Parse a t.me link into (chat, message_id, is_private).
    Returns (None, None, None) if invalid.
    """
    link = link.strip()
    m = re.match(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", link)
    if m:
        return -100 * int(m.group(1)), int(m.group(2)), True
    m = re.match(r"(?:https?://)?t\.me/([^/]+)/(\d+)", link)
    if m:
        return m.group(1), int(m.group(2)), False
    return None, None, None
