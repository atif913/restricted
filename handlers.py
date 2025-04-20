import logging, asyncio, re
from telethon import events, Button
from config import ADMIN_ID, ADMIN_USERNAME
from auth import (
    is_authorized, get_tokens, use_token,
    handle_referral, REFERRAL_BONUS
)
from tele_utils import (
    get_user_client, extract_message_info,
    load_all_dialogs, disconnect_user_client, user_dialogs_cache
)
from state import user_states

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def build_keyboard(uid):
    st      = user_states.get(uid, {})
    logged  = st.get("client_authorized", False)
    premium = is_authorized(uid)
    tokens  = get_tokens(uid)

    if not logged:
        return [
            [Button.text("🏠 Home"), Button.text("❓ Help")],
            [Button.text("🔑 Login"), Button.text("🔄 Refresh")],
            [Button.text("🤝 Invite"), Button.text(f"🎟️ {tokens} tokens")],
        ]
    if not premium:
        return [
            [Button.text("🏠 Home"),   Button.text("❓ Help")],
            [Button.text("🔐 Logout"), Button.text("📥 Send Link")],
            [Button.text("🔢 Batch"),  Button.text("🔄 Refresh")],
            [Button.text("🤝 Invite"), Button.text(f"🎟️ {tokens} tokens")],
        ]
    return [
        [Button.text("🏠 Home"),   Button.text("❓ Help")],
        [Button.text("🔐 Logout"), Button.text("📥 Send Link")],
        [Button.text("🔢 Batch"),  Button.text("🔄 Refresh")],
    ]

def register_handlers(bot, task_queue, send_queue):

    @bot.on(events.NewMessage(pattern=r"^/start(?:\s+\d+)?$"))
    async def start_cmd(event):
        uid, parts = event.sender_id, event.raw_text.split()
        if len(parts) == 2 and handle_referral(uid, parts[1]):
            inviter = int(parts[1])
            await event.reply(
                f"🎉 You and `{parts[1]}` each got {REFERRAL_BONUS} tokens!",
                parse_mode="md"
            )
            await event.client.send_message(
                inviter,
                f"🎉 User `{uid}` joined via your link and you earned {REFERRAL_BONUS} tokens!\n"
                f"Balance: {get_tokens(inviter)} tokens."
            )

        client = await get_user_client(uid)
        logged = await client.is_user_authorized()
        user_states[uid] = {"client_authorized": logged, "step": None}

        if not logged:
            await event.reply(
                "👋 **Welcome to MediaSaver Pro!**\n\n"
                "🔒 Secure & private.\n"
                "1️⃣ Tap **Login**\n"
                "2️⃣ Enter your phone number\n\n"
                "🚀 Ready to download restricted videos?",
                buttons=build_keyboard(uid), parse_mode="md"
            )
        else:
            await event.reply(
                f"🎉 **Welcome back!** You have **{get_tokens(uid)} tokens** left.\n\n"
                "📥 Tap **Send Link** to download instantly.\n"
                "🔢 Tap **Batch** for multi‑download.\n\n"
                "💎 **Why Premium?** Unlimited downloads, batch up to 100, priority speed!",
                buttons=build_keyboard(uid), parse_mode="md"
            )

    @bot.on(events.NewMessage(pattern=r"^📥 Send Link$"))
    async def single_cmd(event):
        uid    = event.sender_id
        st     = user_states.setdefault(uid, {})
        client = await get_user_client(uid)
        if not await client.is_user_authorized():
            return await event.reply("🔐 Please log in first.", buttons=build_keyboard(uid))

        if not is_authorized(uid) and get_tokens(uid) <= 0:
            return await event.reply(
                "😞 **Out of tokens!** Share or upgrade:",
                buttons=[[Button.inline("🤝 Invite Friends", b"invite"),
                          Button.inline("💎 Buy Premium",    b"buy")]]
            )

        st["step"] = "await_single"
        await event.reply(
            "📨 Paste your Telegram link below to download.",
            buttons=[[Button.text("🏠 Home"), Button.text("Retry")]]
        )

    @bot.on(events.NewMessage())
    async def flow(event):
        text, uid = event.raw_text.strip(), event.sender_id
        st        = user_states.setdefault(uid, {})
        if st.get("step") != "await_single":
            return

        cid, mid, priv = extract_message_info(text)
        if cid is None:
            return await event.reply("⚠️ Invalid link. Retry.", buttons=[[Button.text("Retry")]])

        if not is_authorized(uid) and not use_token(uid):
            st.clear()
            return await event.reply(
                "😞 **Out of tokens!** Share or upgrade to continue.",
                buttons=[[Button.inline("🤝 Invite Friends", b"invite"),
                          Button.inline("💎 Buy Premium",    b"buy")]]
            )

        st.clear()

        # Instant server-side copy
        try:
            await event.client.copy_message(
                entity=uid,
                from_peer=cid,
                message_ids=mid
            )
            return await event.reply(
                "✅ Sent instantly via Telegram!",
                buttons=build_keyboard(uid)
            )
        except Exception as e:
            logger.warning(f"copy_message failed ({e}); falling back to download")
            await task_queue.put((uid, cid, mid, priv))
            return await event.reply(
                "🔄 Download queued (copy failed)…",
                buttons=build_keyboard(uid)
            )

    # ... Register other handlers (batch, invite cb, admin cb, etc.) ...
