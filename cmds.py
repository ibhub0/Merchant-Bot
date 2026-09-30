"""cmds.py — /start, /stock, /broadcast, the owner 'listener' (ask) and the shared UI helpers
(small-caps text + random image on every message)."""
import asyncio
import html
import logging
import random
import re

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, InputUserDeactivated, MessageNotModified, PeerIdInvalid, UserIsBlocked
from pyrogram.handlers import MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup as Markup, InputMediaPhoto

import config
import database as db

log = logging.getLogger("cmds")
OWNER = filters.user(config.OWNER_IDS)
CAPTION_MAX = 1000  # Telegram caption limit is 1024


# ════════════════════════════════════════════════════════════════════════════
#  SMALL CAPS  (applied to every text, caption, button and popup)
# ════════════════════════════════════════════════════════════════════════════
_SC = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ" * 2,
)
# never converted: html tags, <code>/<pre> content (credentials, UPI id), entities, /commands, @usernames
_KEEP = re.compile(
    r"(<code>.*?</code>|<pre>.*?</pre>|<[^>]+>|&#?\w+;|(?<!\w)/[A-Za-z_]+|@[A-Za-z0-9_]+)", re.S
)


def sc(text) -> str:
    parts = _KEEP.split(str(text))
    return "".join(p if i % 2 else p.translate(_SC) for i, p in enumerate(parts))


def Btn(text, data):
    return InlineKeyboardButton(sc(text), callback_data=data)


# ════════════════════════════════════════════════════════════════════════════
#  RANDOM IMAGE + TEXT  (every message is a photo with a small-caps caption)
# ════════════════════════════════════════════════════════════════════════════
_bad: set = set()  # image URLs Telegram refused — skipped from now on


def rand_img():
    pool = [u for u in config.IMAGES if u not in _bad]
    if not pool:
        _bad.clear()
        pool = config.IMAGES
    return random.choice(pool)


def _img_error(e) -> bool:
    s = str(e).upper()
    return any(k in s for k in ("WEBPAGE", "MEDIA", "PHOTO", "IMAGE"))


async def send_ui(client, chat_id, text, kb=None, photo=True):
    """Send a random image with `text` as caption. Falls back to plain text if images fail."""
    text = sc(text)
    if photo and len(text) <= CAPTION_MAX:
        for _ in range(3):
            url = rand_img()
            try:
                return await client.send_photo(chat_id, url, caption=text, reply_markup=kb)
            except Exception as e:
                if not _img_error(e):
                    break
                _bad.add(url)
                log.warning("image rejected, skipping: %s", url[:80])
    return await client.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)


async def edit_ui(client, chat_id, msg_id, text, kb=None, media=True, is_photo=True):
    """Edit a message: new random image + new caption (media=False keeps the current image)."""
    if not msg_id:
        return False
    text = sc(text)
    try:
        if is_photo and len(text) <= CAPTION_MAX:
            if media:
                for _ in range(3):
                    url = rand_img()
                    try:
                        await client.edit_message_media(
                            chat_id, msg_id,
                            InputMediaPhoto(url, caption=text, parse_mode=ParseMode.HTML),
                            reply_markup=kb,
                        )
                        return True
                    except MessageNotModified:
                        return True
                    except Exception as e:
                        if not _img_error(e):
                            raise
                        _bad.add(url)
            await client.edit_message_caption(chat_id, msg_id, text, reply_markup=kb)
        else:
            await client.edit_message_text(chat_id, msg_id, text, reply_markup=kb, disable_web_page_preview=True)
        return True
    except MessageNotModified:
        return True
    except Exception:
        log.debug("edit_ui failed", exc_info=True)
        return False


async def edit_cq(client, cq, text, kb=None, media=True):
    m = cq.message
    return await edit_ui(client, m.chat.id, m.id, text, kb, media=media, is_photo=bool(m.photo))


async def alert(cq, text=None, show=False):
    try:
        await cq.answer(sc(text) if text else None, show_alert=show)
    except Exception:
        pass


# ── tiny background-task helper ─────────────────────────────────────────────
_tasks = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)

    def _done(task):
        _tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("background task failed", exc_info=task.exception())

    t.add_done_callback(_done)


# ════════════════════════════════════════════════════════════════════════════
#  LISTENER — wait for the owner's next message
# ════════════════════════════════════════════════════════════════════════════
CANCEL = object()
_waiters: dict = {}


async def ask(client, chat_id, text, timeout=180):
    """Send a prompt (with a Cancel button) and wait for the user's next message. None = cancelled/timeout."""
    fut = asyncio.get_running_loop().create_future()
    _waiters[chat_id] = fut
    prompt = await send_ui(client, chat_id, text, Markup([[Btn("❌ cancel", "ask_cancel")]]))
    try:
        res = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        res = CANCEL
        await edit_ui(client, chat_id, prompt.id, "⌛ timed out.", is_photo=bool(prompt.photo))
    finally:
        if _waiters.get(chat_id) is fut:
            _waiters.pop(chat_id, None)
    try:
        await prompt.edit_reply_markup(None)
    except Exception:
        pass
    return None if res is CANCEL else res


async def ask_value(client, chat_id, prompt, cast, tries=3):
    """ask() + validation. `cast` takes the text and returns a value or raises ValueError(message)."""
    for _ in range(tries):
        msg = await ask(client, chat_id, prompt)
        if msg is None:
            return None
        try:
            return cast((msg.text or "").strip())
        except ValueError as e:
            await send_ui(client, chat_id, f"⚠️ {e or 'invalid input'} — try again.")
    await send_ui(client, chat_id, "❌ too many invalid attempts. cancelled.")
    return None


def cancel_ask(chat_id) -> bool:
    fut = _waiters.get(chat_id)
    if fut and not fut.done():
        fut.set_result(CANCEL)
        return True
    return False


async def _listener(client, message):
    fut = _waiters.get(message.chat.id)
    if fut and not fut.done():
        if (message.text or "").strip().lower().startswith("/cancel"):
            fut.set_result(CANCEL)
        else:
            fut.set_result(message)
        message.stop_propagation()


# ════════════════════════════════════════════════════════════════════════════
#  /start
# ════════════════════════════════════════════════════════════════════════════
def start_text(name):
    return (
        f"👋 hey <b>{html.escape(name or 'there')}</b>!\n\n"
        "🛒 <b>here's your ultimate merchant bot — you can buy anything!</b>\n\n"
        "tap <b>discover</b> below to see everything in stock."
    )


def start_kb():
    return Markup([[Btn("🔍 Discover", "discover:0")]])


async def start_cmd(client, message):
    await db.add_user(message.from_user)
    await send_ui(client, message.chat.id, start_text(message.from_user.first_name), start_kb())


# ════════════════════════════════════════════════════════════════════════════
#  /stock
# ════════════════════════════════════════════════════════════════════════════
async def stock_panel():
    s = await db.stats()
    text = (
        "📦 <b>stock manager</b>\n\n"
        f"🧾 items: <b>{s['products']}</b>\n"
        f"📊 units in stock: <b>{s['available']}</b>\n"
        f"✅ units sold: <b>{s['sold']}</b>\n"
        f"💰 revenue: <b>{config.CURRENCY}{s['revenue']:,.2f}</b>\n"
        f"👥 users: <b>{s['users']}</b>"
    )
    kb = Markup([
        [Btn("➕ Add Item", "st_add"), Btn("📋 Manage Items", "st_list:0")],
        [Btn("⚙️ Payment Settings", "st_pay")],
    ])
    return text, kb


async def stock_cmd(client, message):
    text, kb = await stock_panel()
    await send_ui(client, message.chat.id, text, kb)


# ════════════════════════════════════════════════════════════════════════════
#  /broadcast
# ════════════════════════════════════════════════════════════════════════════
_pending_bc: dict = {}  # owner_id -> (chat_id, message_id)


async def broadcast_cmd(client, message):
    chat_id = message.chat.id
    src = message.reply_to_message
    if not src:
        src = await ask(
            client, chat_id,
            "📢 <b>broadcast</b>\n\nsend the message to broadcast — text, photo, video, anything.\n/cancel to abort.",
        )
        if not src:
            return
    total = len(await db.user_ids())
    _pending_bc[message.from_user.id] = (src.chat.id, src.id)
    await client.copy_message(chat_id, src.chat.id, src.id)  # exact preview (your content is never altered)
    await send_ui(
        client, chat_id,
        f"👆 <b>preview.</b> send this to <b>{total}</b> users?",
        Markup([[Btn("✅ Send", "bc_yes"), Btn("❌ Cancel", "bc_no")]]),
    )


async def run_broadcast(client, owner_id, status_msg):
    cid, mid_status, is_photo = status_msg.chat.id, status_msg.id, bool(status_msg.photo)
    job = _pending_bc.pop(owner_id, None)
    if not job:
        await edit_ui(client, cid, mid_status, "⚠️ nothing to broadcast.", is_photo=is_photo)
        return
    from_chat, mid = job
    ids = await db.user_ids()
    sent = failed = 0
    for i, uid in enumerate(ids, 1):
        try:
            await client.copy_message(uid, from_chat, mid)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await client.copy_message(uid, from_chat, mid)
                sent += 1
            except Exception:
                failed += 1
        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            failed += 1
            await db.mark_blocked(uid)
        except Exception:
            failed += 1
        if i % 25 == 0:
            await edit_ui(client, cid, mid_status, f"📢 broadcasting… {i}/{len(ids)}", media=False, is_photo=is_photo)
        await asyncio.sleep(0.05)
    await edit_ui(
        client, cid, mid_status,
        f"📢 <b>broadcast finished</b>\n\n✅ delivered: <b>{sent}</b>\n🚫 failed/blocked: <b>{failed}</b>\n👥 total: <b>{len(ids)}</b>",
        is_photo=is_photo,
    )


def register(app):
    app.add_handler(MessageHandler(_listener, filters.private & OWNER), group=-1)
    app.add_handler(MessageHandler(start_cmd, filters.command("start") & filters.private))
    app.add_handler(MessageHandler(stock_cmd, filters.command("stock") & filters.private & OWNER))
    app.add_handler(MessageHandler(broadcast_cmd, filters.command("broadcast") & filters.private & OWNER))
