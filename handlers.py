# handlers.py

import logging
import asyncio
import re
from telethon import events, Button
from telethon.tl.types import InputMessagesFilterPhotos, InputMessagesFilterVideo
from telethon.errors import SessionPasswordNeededError

from auth import (
    is_authorized, grant_access, get_batch_limit,
    get_tokens, use_token,
    handle_referral, REFERRAL_BONUS
)
from tele_utils import (
    get_user_client, extract_message_info,
    load_all_dialogs, disconnect_user_client, user_dialogs_cache
)
from state import user_states
from config import ADMIN_ID, ADMIN_USERNAME

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Cache for bot username
BOT_USERNAME = None

async def get_bot_username(client):
    global BOT_USERNAME
    if BOT_USERNAME is None:
        me = await client.get_me()
        BOT_USERNAME = me.username
    return BOT_USERNAME

# Premium pitch for non‑premium batch attempts
PREMIUM_PITCH = (
    "🔒 **Batch downloads** are Premium‑only.\n"
    "💎 **Upgrade now:** ₹299 for 10 days of UNLIMITED downloads, batch mode & more!\n"
    f"Contact @{ADMIN_USERNAME} to purchase."
)

# Inline‑only admin panel
ADMIN_PANEL = [
    [Button.inline("📊 Stats",        b"admin:stats"),
     Button.inline("📝 Broadcast",    b"admin:broadcast")],
    [Button.inline("👑 Premium List", b"admin:premiumlist"),
     Button.inline("❌ Revoke User",   b"admin:revokepanel")],
    [Button.inline("⚙️ Set Limit",     b"admin:setlimit"),
     Button.inline("🗓️ Set Days",      b"admin:setdays")],
    [Button.inline("📋 View Queue",    b"admin:viewqueue"),
     Button.inline("🚫 Cancel All",    b"admin:cancelall")],
    [Button.inline("🔄 Refresh Dialogs", b"admin:refreshdialogs"),
     Button.inline("🗑️ Clear Cache",      b"admin:cacheclear")],
    [Button.inline("⚠️ Shutdown Bot",     b"admin:shutdown")]
]

def build_keyboard(uid):
    """Return only Button.text rows (no inline) for the main menu."""
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

# Messages/buttons to skip in the flow handler
SKIP = {
    "🏠 Home","❓ Help","🔑 Login","🔐 Logout",
    "📥 Send Link","🔢 Batch","🔄 Refresh",
    "🤝 Invite","💎 Buy Premium","/start","/admin","❌ Stop","Retry"
}
SKIP.update(str(n) for n in range(10, 101, 10))


def register_handlers(bot, task_queue, send_queue):

    # ─── Admin commands ────────────────────────────────────────────────

    @bot.on(events.NewMessage(pattern=r"^/grant\s+"))
    async def grant_cmd(event):
        """/grant <user_id> <days> [batch_limit]"""
        if event.sender_id != ADMIN_ID:
            return
        parts = event.raw_text.strip().split()
        if len(parts) not in (3, 4):
            return await event.reply(
                "⚠️ Usage: `/grant <user_id> <days> [batch_limit]`",
                parse_mode="md"
            )
        try:
            target = int(parts[1])
            days   = int(parts[2])
            limit  = int(parts[3]) if len(parts) == 4 else 10
        except ValueError:
            return await event.reply("❌ `<user_id>` and `<days>` must be numbers.", parse_mode="md")

        grant_access(target, days, limit)
        await event.reply(
            f"✅ Granted user `{target}` Premium for **{days}** days (batch limit **{limit}**).",
            parse_mode="md"
        )
        # Notify the target user if possible
        try:
            await bot.send_message(
                target,
                f"🎉 **You’ve been granted Premium!**\n"
                f"Duration: **{days}** days\n"
                f"Batch limit: **{limit}** items\n"
                "Enjoy unlimited, lightning‑fast downloads! 🚀"
            )
        except:
            pass

    @bot.on(events.NewMessage(pattern=r"^/admin$"))
    async def admin_panel(event):
        """Display the inline-only admin panel."""
        if event.sender_id != ADMIN_ID:
            return
        await bot.send_message(
            event.chat_id,
            "**Admin Panel:**",
            buttons=ADMIN_PANEL,
            parse_mode="md"
        )

    @bot.on(events.CallbackQuery(data=re.compile(rb"admin:(.+)")))
    async def admin_cb(event):
        """Handle admin-panel button presses."""
        if event.sender_id != ADMIN_ID:
            return await event.answer("❌ Unauthorized", alert=True)
        action = event.data.decode().split(":",1)[1]

        # Stats
        if action == "stats":
            from auth import authorized
            from datetime import datetime
            active = [u for u,i in authorized.items() if i["expiry"] > datetime.utcnow() and u != ADMIN_ID]
            await event.edit(f"📊 Active Premium Users: {len(active)}", buttons=ADMIN_PANEL)
            return

        # Broadcast prompt
        if action == "broadcast":
            return await event.answer("✍️ To broadcast, use:\n`/broadcast <message>`", alert=True)

        # Premium list
        if action == "premiumlist":
            from auth import authorized
            from datetime import datetime
            lines, kb = [], []
            for u,i in authorized.items():
                if i["expiry"] <= datetime.utcnow() or u == ADMIN_ID:
                    continue
                exp = i["expiry"].strftime("%Y-%m-%d")
                lines.append(f"👤 `{u}` • Expires: {exp} • Limit: {i['batch_limit']}")
                kb.append([Button.inline(f"❌ Revoke {u}", data=f"revoke:{u}".encode())])
            kb.append([Button.inline("🔙 Back", b"admin:stats")])
            text = "**Active Premium Users:**\n\n" + ("\n".join(lines) or "None")
            return await event.edit(text, buttons=kb, parse_mode="md")

        # Revoke user
        if action.startswith("revoke:"):
            tgt = int(action.split(":",1)[1])
            from auth import authorized, _save_authorized
            if tgt in authorized:
                del authorized[tgt]; _save_authorized()
                await event.answer("✅ User revoked.", alert=True)
                await event.edit(f"❌ Revoked `{tgt}`’s access.", buttons=[[Button.inline("🔙 Back", b"admin:premiumlist")]], parse_mode="md")
            else:
                await event.answer("❌ Not found.", alert=True)
            return

        # Set limit / days
        if action in ("setlimit","setdays"):
            return await event.answer("⚙️ Use `/grant <user_id> <days> [batch_limit]`", alert=True)

        # View/cancel queues
        if action == "viewqueue":
            import download
            dq, uq = download.task_queue.qsize(), download.send_queue.qsize()
            return await event.edit(f"📋 Download queue: {dq}\nUpload queue: {uq}", buttons=ADMIN_PANEL)
        if action == "cancelall":
            import download
            while not download.task_queue.empty():
                await download.task_queue.get(); download.task_queue.task_done()
            while not download.send_queue.empty():
                await download.send_queue.get(); download.send_queue.task_done()
            return await event.answer("✅ All tasks cancelled.", alert=True)

        # Refresh dialogs / clear cache
        if action == "refreshdialogs":
            user_dialogs_cache.clear()
            return await event.answer("🔄 Dialog cache cleared.", alert=True)
        if action == "cacheclear":
            user_dialogs_cache.clear()
            return await event.answer("🗑️ User cache cleared.", alert=True)

        # Shutdown
        if action == "shutdown":
            await event.answer("⚠️ Shutting down…", alert=True)
            await bot.disconnect()
            return

        await event.answer(f"❓ Unknown action: {action}", alert=True)


    # ─── Invite & Premium inline callbacks ────────────────────────────

    @bot.on(events.CallbackQuery(data=b"invite"))
    async def on_invite_cb(event):
        await event.answer()
        bot_user = await get_bot_username(event.client)
        uid = event.sender_id
        link = f"https://t.me/{bot_user}?start={uid}"
        await event.reply(
            f"🤝 **Here is your invite link:**\n{link}\n\n"
            "Share it to earn 3 tokens per friend!",
            parse_mode="md"
        )

    @bot.on(events.CallbackQuery(data=b"buy"))
    async def on_buy_cb(event):
        await event.answer()
        await event.reply(
            "💎 **Premium Plan Details**\n\n"
            "• ₹299 for 10 days of UNLIMITED downloads\n"
            "• Batch up to 100 items in one click\n"
            "• Priority speed & zero wait\n\n"
            f"Contact @{ADMIN_USERNAME} to upgrade now!",
            parse_mode="md"
        )


    # ─── Text buttons handlers ────────────────────────────────────────

    @bot.on(events.NewMessage(pattern=r"^🤝 Invite$"))
    async def invite_text_cmd(event):
        uid = event.sender_id
        bot_user = await get_bot_username(event.client)
        link = f"https://t.me/{bot_user}?start={uid}"
        await event.reply(
            f"🤝 **Here is your invite link:**\n{link}\n\n"
            "Share it to earn 3 tokens per friend!",
            parse_mode="md"
        )

    @bot.on(events.NewMessage(pattern=r"^🔄 Refresh$"))
    async def refresh_cmd(event):
        await start_cmd(event)

    @bot.on(events.NewMessage(pattern=r"^❓ Help$"))
    async def help_cmd(event):
        uid = event.sender_id
        await event.reply(
            "📚 **How to Use MediaSaver Pro:**\n\n"
            "• 🏠 **Home** – main menu\n"
            "• ❓ **Help** – this guide\n"
            "• 🔑 **Login** / 🔐 **Logout**\n"
            "• 📥 **Send Link** – single download\n"
            "• 🔢 **Batch** – multi download\n"
            "• 🤝 **Invite** – earn tokens\n",
            buttons=build_keyboard(uid), parse_mode="md"
        )

    @bot.on(events.NewMessage(pattern=r"^🎟️ \d+ tokens$"))
    async def token_cmd(event):
        uid = event.sender_id
        tokens = get_tokens(uid)
        await event.reply(
            f"🎟️ You have **{tokens} tokens**.\n"
            "Tap **Invite** to share & earn more, or **Buy Premium** for unlimited.",
            buttons=build_keyboard(uid), parse_mode="md"
        )

    @bot.on(events.NewMessage(pattern=r"^Retry$"))
    async def retry_cmd(event):
        uid  = event.sender_id
        step = user_states.get(uid, {}).get("step")
        if step == "await_single":
            return await event.reply(
                "📨 Paste your link to retry.",
                buttons=[[Button.text("🏠 Home"), Button.text("Retry")]]
            )
        if step == "await_batch_link":
            total = user_states[uid].get("batch_total", 0)
            return await event.reply(
                f"📨 Paste first link to queue {total}.",
                buttons=[[Button.text("🏠 Home"), Button.text("Retry")]]
            )
        await start_cmd(event)

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
        elif not is_authorized(uid):
            await event.reply(
                f"🎉 **Welcome back!** You have **{get_tokens(uid)} tokens** left.\n\n"
                "📥 Tap **Send Link** to download.\n"
                "🔢 Tap **Batch** for multi‑download.\n"
                "🤝 Tap **Invite** to share & earn tokens.\n\n"
                "💎 **Why Premium?** Unlimited, batch up to 100, priority speed!",
                buttons=build_keyboard(uid), parse_mode="md"
            )
        else:
            await event.reply(
                "✅ **Premium Active!** Unlimited, lightning‑fast downloads + batch mode.",
                buttons=build_keyboard(uid), parse_mode="md"
            )
            asyncio.create_task(load_all_dialogs(client, uid))

    @bot.on(events.NewMessage(pattern=r"^🏠 Home$"))
    async def home_cmd(event):
        await start_cmd(event)

    @bot.on(events.NewMessage(pattern=r"^🔑 Login$"))
    async def login_cmd(event):
        uid = event.sender_id
        client = await get_user_client(uid)
        if await client.is_user_authorized():
            user_states[uid]["client_authorized"] = True
            return await start_cmd(event)
        user_states[uid] = {"step": "phone_entry", "client_authorized": False}
        await event.reply(
            "📱 **Step 1:** Enter your phone number (e.g. +911234567890)\n"
            "🔐 Safe & private – we never store your number.",
            buttons=[[Button.text("🏠 Home"), Button.text("Retry")]],
            parse_mode="md"
        )

    @bot.on(events.NewMessage(pattern=r"^🔐 Logout$"))
    async def logout_cmd(event):
        uid = event.sender_id
        await disconnect_user_client(uid)
        user_states.pop(uid, None)
        await event.reply("✅ Logged out. See you soon!", buttons=build_keyboard(uid))

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

    @bot.on(events.NewMessage(pattern=r"^🔢 Batch$"))
    async def batch_cmd(event):
        uid    = event.sender_id
        st     = user_states.setdefault(uid, {})
        client = await get_user_client(uid)
        if not await client.is_user_authorized():
            return await event.reply("🔐 Please log in first.", buttons=build_keyboard(uid))
        if not is_authorized(uid):
            return await event.reply(
                PREMIUM_PITCH,
                buttons=[[Button.inline("💎 Buy Premium", b"buy")]],
                parse_mode="md"
            )

        lim     = get_batch_limit(uid)
        choices = [n for n in range(10, 101, 10) if n <= lim]
        kb      = [[Button.text(str(n)) for n in choices[i:i+2]] for i in range(0, len(choices), 2)]
        kb.append([Button.text("Retry")])
        st["step"] = "await_batch_size"
        await event.reply(
            "🔢 **Batch Mode:** Choose how many items to download (10–100):",
            buttons=kb, parse_mode="md"
        )

    @bot.on(events.NewMessage(pattern=r"^[0-9]+$"))
    async def batch_size_cmd(event):
        uid = event.sender_id
        st  = user_states.setdefault(uid, {})
        if st.get("step") != "await_batch_size":
            return
        n, lim = int(event.raw_text), get_batch_limit(uid)
        if n not in range(10, lim+1, 10):
            return await event.reply(
                "❌ Pick a valid batch size:",
                buttons=[[Button.text(str(x)) for x in range(10, min(lim,100)+1, 10)],
                         [Button.text("Retry")]]
            )
        st.update(batch_total=n, waiting_batch=n, step="await_batch_link")
        await event.reply(
            f"📨 Paste first link to queue **{n}** items.",
            buttons=[[Button.text("🏠 Home"), Button.text("Retry")]]
        )

    @bot.on(events.NewMessage(pattern=r"^❌ Stop$"))
    async def stop_batch(event):
        uid = event.sender_id
        st  = user_states.get(uid, {})
        if st.get("step") not in ("await_batch_link", "batch_sending"):
            return await event.reply("ℹ️ No batch in progress.", buttons=build_keyboard(uid))
        st.clear()
        await event.reply("🛑 Batch cancelled.", buttons=build_keyboard(uid))

    @bot.on(events.NewMessage())
    async def flow(event):
        text, uid = event.raw_text.strip(), event.sender_id
        st        = user_states.setdefault(uid, {})
        if not st.get("step") or text in SKIP:
            return
        client = await get_user_client(uid)

        try:
            # — Phone entry —
            if st["step"] == "phone_entry":
                try:
                    await client.send_code_request(text)
                except:
                    return await event.reply("❌ Could not send code. Retry.", buttons=[[Button.text("Retry")]])
                st.update(step="otp_entry", phone=text)
                return await event.reply(
                    "📩 Enter the 5‑digit code **with spaces**, e.g. `1 2 3 4 5`.",
                    buttons=[[Button.text("Retry")]], parse_mode="md"
                )

            # — OTP entry —
            if st["step"] == "otp_entry":
                parts = text.split()
                if len(parts) != 5 or not all(p.isdigit() for p in parts):
                    return await event.reply(
                        "⚠️ Code must be **5 digits with spaces**.",
                        buttons=[[Button.text("Retry")]], parse_mode="md"
                    )
                code = "".join(parts)
                try:
                    await client.sign_in(st["phone"], code)
                except SessionPasswordNeededError:
                    st["step"] = "twofa"
                    return await event.reply("🔒 Enter your 2FA password.", buttons=[[Button.text("Retry")]])
                except:
                    return await event.reply("❌ Invalid code. Retry.", buttons=[[Button.text("Retry")]])
                user_states[uid]["client_authorized"] = True
                return await event.reply(
                    "✅ Login successful! Tap **Yes, it’s me** in Telegram to confirm.",
                    buttons=[[Button.text("🏠 Home")]], parse_mode="md"
                )

            # — 2FA entry —
            if st["step"] == "twofa":
                try:
                    await client.sign_in(password=text)
                except:
                    return await event.reply("❌ Wrong 2FA. Retry.", buttons=[[Button.text("Retry")]])
                user_states[uid]["client_authorized"] = True
                return await event.reply(
                    "✅ Login successful! Tap **Yes, it’s me** in Telegram to confirm.",
                    buttons=[[Button.text("🏠 Home")]], parse_mode="md"
                )

            # — Single link —
            if st["step"] == "await_single":
                cid, mid, priv = extract_message_info(text)
                if cid is None:
                    return await event.reply("⚠️ Invalid link. Retry.", buttons=[[Button.text("Retry")]])
                if not is_authorized(uid) and not use_token(uid):
                    st.clear()
                    return await event.reply(
                        "😞 **Out of tokens!** Share or upgrade to continue.",
                        buttons=[[Button.inline("🤝 Invite Friends", b"invite"),
                                  Button.inline("💎 Buy Premium",    b"buy")]]
                    )
                st.clear()
                await task_queue.put((uid, cid, mid, priv))
                return await event.reply("🔄 Download queued!", buttons=build_keyboard(uid))

            # — Batch links —
            if st["step"] in ("await_batch_link", "batch_sending"):
                cid, mid, priv = extract_message_info(text)
                if cid is None:
                    return await event.reply("⚠️ Invalid link. Retry.", buttons=[[Button.text("Retry")]])
                if priv and uid not in user_dialogs_cache:
                    await load_all_dialogs(client, uid)
                ent = user_dialogs_cache.get(uid, {}).get(cid) if priv else cid
                if not ent:
                    return await event.reply("⚠️ Chat not found. Retry.", buttons=[[Button.text("Retry")]])
                total, fetched = st["batch_total"], 0
                orig = await client.get_messages(ent, ids=[mid])
                if orig and getattr(orig[0], "media", None):
                    await task_queue.put((uid, cid, mid, priv))
                    fetched = 1
                if fetched < total:
                    needed = total - fetched
                    photos = await client.get_messages(
                        ent, limit=needed,
                        filter=InputMessagesFilterPhotos(),
                        offset_id=mid, reverse=True
                    )
                    videos = await client.get_messages(
                        ent, limit=needed,
                        filter=InputMessagesFilterVideo(),
                        offset_id=mid, reverse=True
                    )
                    for m in sorted(photos + videos, key=lambda m: m.id):
                        if fetched >= total: break
                        await task_queue.put((uid, cid, m.id, priv))
                        fetched += 1
                st["step"] = "batch_sending"
                return await event.reply(
                    f"🚀 Queued {fetched}/{total}! ❌ Stop to cancel.",
                    buttons=[[Button.text("🏠 Home"), Button.text("Retry")]]
                )

        except Exception as e:
            logger.error(f"[FLOW ERROR] {e}", exc_info=True)
