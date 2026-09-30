"""inline.py — every inline-button callback (shop flow for users, stock + payment-settings UI for the owner)."""
import html
import io
import logging
import re
from urllib.parse import quote, urlencode

from pyrogram.handlers import CallbackQueryHandler

import cmds
import config
import database as db
import verify
from cmds import Btn, Markup, alert, edit_cq, sc, send_ui

log = logging.getLogger("inline")
PER = config.ITEMS_PER_PAGE
OWNER_ACTIONS = {
    "ask_cancel", "st_home", "st_add", "st_list", "st_item", "st_units", "st_price", "st_del", "st_delok",
    "st_pay", "st_upi", "st_qr", "st_qrclear", "st_mode", "st_prev", "bc_yes", "bc_no",
}


def fmt(v):
    return f"{config.CURRENCY}{v:,.2f}"


def esc(s):
    return html.escape(str(s))


def pager(prefix, page, pages, back):
    nav = []
    if page > 0:
        nav.append(Btn("◀️ Prev", f"{prefix}:{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(Btn("Next ▶️", f"{prefix}:{page + 1}"))
    return [nav, [Btn(*back)]]


# ════════════════════════════════════════════════════════════════════════════
#  USER SIDE
# ════════════════════════════════════════════════════════════════════════════
async def h_noop(client, cq, arg):
    await alert(cq)


async def h_home(client, cq, arg):
    await edit_cq(client, cq, cmds.start_text(cq.from_user.first_name), cmds.start_kb())
    await alert(cq)


async def h_discover(client, cq, arg):
    total = await db.count_products()
    pages = max(1, -(-total // PER))
    page = max(0, min(int(arg or 0), pages - 1))
    prods = await db.list_products(page * PER, PER)
    if not prods:
        await edit_cq(client, cq, "😕 nothing is listed yet — check back soon!", Markup([[Btn("🏠 Home", "home")]]))
        return await alert(cq)
    counts = await db.stock_counts([p["_id"] for p in prods])
    rows = []
    for p in prods:
        n = counts.get(p["_id"], 0)
        tail = f"{n} left" if n else "❌ sold out"
        rows.append([Btn(f"{p['name'][:28]} • {fmt(p['price'])} • {tail}", f"item:{p['_id']}")])
    rows += pager("discover", page, pages, ("🏠 Home", "home"))
    await edit_cq(client, cq, "🛍 <b>discover</b>\npick anything you like:", Markup(rows))
    await alert(cq)


async def h_item(client, cq, arg):
    pid = db.oid(arg)
    p = await db.get_product(pid) if pid else None
    if not p:
        return await alert(cq, "this item is no longer available.", True)
    n = await db.stock_of(pid)
    if n <= 0:
        return await alert(cq, "❌ sold out — check back soon!", True)
    sid = await db.create_session(cq.from_user.id, p)  # the secret backend session starts here
    text = (
        f"📦 <b>{esc(p['name'])}</b>\n\n"
        f"🛡 warranty: <b>{esc(p['warranty'])}</b>\n"
        f"📊 in stock: <b>{n}</b>\n\n"
        "tap the amount below to pay 👇"
    )
    kb = Markup([[Btn(f"💳 Pay {fmt(p['price'])}", f"pay:{sid}")], [Btn("⬅️ Back", "discover:0")]])
    await edit_cq(client, cq, text, kb)
    await alert(cq)


def make_qr(amount, conf):
    """Dynamic UPI QR with the exact amount baked in. Returns BytesIO or None."""
    if not conf["upi_id"]:
        return None
    try:
        import qrcode
    except ImportError:
        return None
    q = urlencode(
        {"pa": conf["upi_id"], "pn": config.PAYEE_NAME, "am": f"{amount:.2f}", "cu": "INR", "tn": "Order"},
        quote_via=quote,
    )
    bio = io.BytesIO()
    bio.name = "pay.png"
    qrcode.make(f"upi://pay?{q}").save(bio, "PNG")
    bio.seek(0)
    return bio


def build_qr(conf, amount):
    """Static mode -> owner's uploaded image. Dynamic mode -> generated QR (falls back to static image)."""
    if conf["mode"] == "static" and conf["static_qr"]:
        return conf["static_qr"]
    return make_qr(amount, conf) or conf["static_qr"] or None


async def h_pay(client, cq, arg):
    uid = cq.from_user.id
    sid = db.oid(arg)
    s = await db.get_session(sid) if sid else None
    if not s or s["user_id"] != uid:
        return await alert(cq, "session expired — please open the item again.", True)
    if s["status"] != "created":
        return await alert(cq, "payment already started — check the message below ⬇️", True)

    await alert(cq, "generating your payment…")

    # one live payment per user: replace older ones
    for old in await db.user_pending(uid):
        await db.cancel_session(old["_id"], uid)
        await verify.edit_msg(client, old.get("qr_chat_id"), old.get("qr_msg_id"), "❌ replaced by a newer payment session.")

    unit = await db.reserve_unit(s["product_id"], sid)
    if not unit:
        await edit_cq(client, cq, "❌ sorry, this item just sold out.", Markup([[Btn("🔍 Discover", "discover:0")]]))
        return
    s2 = await db.activate_session(sid, int(round(s["price"] * 100)), unit["_id"])
    if not s2:
        await db.release_units(sid)
        await edit_cq(client, cq, "⚠️ too many people are paying right now. try again in a minute.", Markup([[Btn("🔍 Discover", "discover:0")]]))
        return

    conf = await db.payment_conf()  # live settings — owner can change them from /stock
    amount = s2["amount_paise"] / 100
    caption = (
        f"💳 <b>pay {fmt(amount)}</b> for <b>{esc(s['product_name'])}</b>\n\n"
        f"• send <b>exactly {fmt(amount)}</b> (the paise matter — that's how your payment is detected)\n"
        + (f"• upi id: <code>{esc(conf['upi_id'])}</code>\n" if conf["upi_id"] else "")
        + f"• ⏳ valid for <b>{config.SESSION_TIMEOUT // 60} minutes</b>\n\n"
        "your item is delivered automatically once the payment is verified."
    )
    kb = Markup([[Btn("❌ Cancel", f"cancel:{sid}")]])
    sent = None
    try:
        qr = build_qr(conf, amount)
        if qr:
            sent = await client.send_photo(uid, qr, caption=sc(caption), reply_markup=kb)
    except Exception:
        log.exception("qr send failed, falling back to random image")
    if not sent:
        try:
            sent = await send_ui(client, uid, caption, kb)
        except Exception:
            log.exception("failed to send payment message")
            await db.cancel_session(sid, uid)
            await edit_cq(client, cq, f"⚠️ couldn't create the payment. contact {config.SUPPORT}.")
            return
    await db.set_qr_msg(sid, sent.chat.id, sent.id)
    await edit_cq(client, cq, "⏳ <b>payment session started.</b>\ncomplete the payment in the message below 👇")


async def h_cancel(client, cq, arg):
    sid = db.oid(arg)
    s = await db.cancel_session(sid, cq.from_user.id) if sid else None
    if not s:
        return await alert(cq, "nothing to cancel (already paid or expired).", True)
    await edit_cq(client, cq, "❌ <b>payment cancelled.</b>\ntap /start to shop again.")
    await alert(cq, "cancelled")


# ════════════════════════════════════════════════════════════════════════════
#  OWNER SIDE — validators
# ════════════════════════════════════════════════════════════════════════════
def _text(s):
    if not s:
        raise ValueError("please send some text")
    return s


def _int(s):
    if not s.isdigit() or int(s) <= 0:
        raise ValueError("send a whole number greater than 0")
    return int(s)


def _price(s):
    try:
        v = round(float(s.replace(config.CURRENCY, "").replace(",", "").strip()), 2)
    except ValueError:
        raise ValueError("send a valid price, e.g. 99 or 149.50")
    if v <= 0:
        raise ValueError("price must be greater than 0")
    return v


UPI_RE = re.compile(r"^[\w.\-]{2,}@[\w.\-]{2,}$")


def _upi(s):
    if not UPI_RE.match(s):
        raise ValueError("send a valid upi id like name@bank")
    return s


async def _cancelled(client, chat_id):
    await send_ui(client, chat_id, "❌ cancelled.")


# ════════════════════════════════════════════════════════════════════════════
#  OWNER SIDE — stock UI
# ════════════════════════════════════════════════════════════════════════════
async def _ask_units(client, chat_id, qty, label):
    def cast(s):
        lines = [x.strip() for x in s.splitlines() if x.strip()]
        if len(lines) == qty:
            return lines
        if len(lines) == 1:
            return lines * qty
        raise ValueError(f"send exactly {qty} lines (one per unit) or a single line for all — you sent {len(lines)}")

    return await cmds.ask_value(
        client, chat_id,
        f"{label}\n\nsend the <b>delivery details</b> the buyer receives (account, key, link…).\n"
        f"• {qty} lines = one per unit\n• 1 line = reused for all {qty} units",
        cast,
    )


async def product_panel(pid):
    p = await db.get_product(pid)
    if not p:
        return "❌ item not found.", Markup([[Btn("⬅️ Back", "st_list:0")]])
    n, sold = await db.stock_of(pid), await db.sold_of(pid)
    text = (
        f"📦 <b>{esc(p['name'])}</b>\n\n"
        f"💰 price: <b>{fmt(p['price'])}</b>\n"
        f"🛡 warranty: <b>{esc(p['warranty'])}</b>\n"
        f"📊 in stock: <b>{n}</b>   ✅ sold: <b>{sold}</b>"
    )
    kb = Markup([
        [Btn("➕ Add Units", f"st_units:{pid}"), Btn("✏️ Edit Price", f"st_price:{pid}")],
        [Btn("🗑 Delete Item", f"st_del:{pid}")],
        [Btn("⬅️ Back", "st_list:0")],
    ])
    return text, kb


async def add_item_flow(client, chat_id):
    ask = cmds.ask_value
    name = await ask(client, chat_id, "📝 <b>step 1/5 — item name?</b>", _text)
    if name is None:
        return await _cancelled(client, chat_id)
    qty = await ask(client, chat_id, "🔢 <b>step 2/5 — how many units (no.) are you adding?</b>", _int)
    if qty is None:
        return await _cancelled(client, chat_id)
    price = await ask(client, chat_id, f"💰 <b>step 3/5 — price per unit</b> ({config.CURRENCY})?", _price)
    if price is None:
        return await _cancelled(client, chat_id)
    warranty = await ask(client, chat_id, "🛡 <b>step 4/5 — warranty?</b>\n(e.g. <code>30 days</code> or <code>No warranty</code>)", _text)
    if warranty is None:
        return await _cancelled(client, chat_id)
    datas = await _ask_units(client, chat_id, qty, "📥 <b>step 5/5 — delivery details</b>")
    if datas is None:
        return await _cancelled(client, chat_id)

    pid = await db.add_product(name, price, warranty)
    await db.add_units(pid, datas)
    text, kb = await product_panel(pid)
    await send_ui(client, chat_id, "✅ <b>item added to stock!</b>\n\n" + text, kb)


async def add_units_flow(client, chat_id, pid):
    qty = await cmds.ask_value(client, chat_id, "🔢 <b>how many units (no.) are you adding?</b>", _int)
    if qty is None:
        return await _cancelled(client, chat_id)
    datas = await _ask_units(client, chat_id, qty, "📥")
    if datas is None:
        return await _cancelled(client, chat_id)
    await db.add_units(pid, datas)
    text, kb = await product_panel(pid)
    await send_ui(client, chat_id, f"✅ added <b>{len(datas)}</b> units.\n\n" + text, kb)


async def price_flow(client, chat_id, pid):
    price = await cmds.ask_value(client, chat_id, f"💰 <b>new price</b> ({config.CURRENCY})?", _price)
    if price is None:
        return await _cancelled(client, chat_id)
    await db.set_price(pid, price)
    text, kb = await product_panel(pid)
    await send_ui(client, chat_id, "✅ price updated.\n\n" + text, kb)


async def h_ask_cancel(client, cq, arg):
    ok = cmds.cancel_ask(cq.message.chat.id)
    await alert(cq, "cancelled" if ok else "nothing to cancel")


async def h_st_home(client, cq, arg):
    text, kb = await cmds.stock_panel()
    await edit_cq(client, cq, text, kb)
    await alert(cq)


async def h_st_add(client, cq, arg):
    await alert(cq)
    cmds.spawn(add_item_flow(client, cq.message.chat.id))


async def h_st_list(client, cq, arg):
    total = await db.count_products()
    pages = max(1, -(-total // PER))
    page = max(0, min(int(arg or 0), pages - 1))
    prods = await db.list_products(page * PER, PER)
    if not prods:
        await edit_cq(client, cq, "📭 no items yet.", Markup([[Btn("➕ Add Item", "st_add")], [Btn("⬅️ Back", "st_home")]]))
        return await alert(cq)
    counts = await db.stock_counts([p["_id"] for p in prods])
    rows = [
        [Btn(f"{p['name'][:26]} • {fmt(p['price'])} • {counts.get(p['_id'], 0)} left", f"st_item:{p['_id']}")]
        for p in prods
    ]
    rows += pager("st_list", page, pages, ("⬅️ Back", "st_home"))
    await edit_cq(client, cq, "📋 <b>manage items</b>", Markup(rows))
    await alert(cq)


async def h_st_item(client, cq, arg):
    text, kb = await product_panel(db.oid(arg))
    await edit_cq(client, cq, text, kb)
    await alert(cq)


async def h_st_units(client, cq, arg):
    await alert(cq)
    cmds.spawn(add_units_flow(client, cq.message.chat.id, db.oid(arg)))


async def h_st_price(client, cq, arg):
    await alert(cq)
    cmds.spawn(price_flow(client, cq.message.chat.id, db.oid(arg)))


async def h_st_del(client, cq, arg):
    kb = Markup([[Btn("✅ Yes, Delete", f"st_delok:{arg}"), Btn("❌ No", f"st_item:{arg}")]])
    await edit_cq(client, cq, "⚠️ delete this item and all its <b>unsold</b> units?", kb)
    await alert(cq)


async def h_st_delok(client, cq, arg):
    await db.delete_product(db.oid(arg))
    await alert(cq, "deleted")
    await h_st_list(client, cq, "0")


# ════════════════════════════════════════════════════════════════════════════
#  OWNER SIDE — payment settings (UPI id / QR image / mode), all live
# ════════════════════════════════════════════════════════════════════════════
async def pay_panel():
    c = await db.payment_conf()
    text = (
        "⚙️ <b>payment settings</b>\n\n"
        f"💳 upi id: <code>{esc(c['upi_id'] or '-')}</code>\n"
        f"🖼 static qr: <b>{'✅ set' if c['static_qr'] else '❌ not set'}</b>\n"
        f"🔀 mode: <b>{c['mode']}</b>\n\n"
        "• <b>dynamic</b> — qr generated from your upi id with the exact amount inside (recommended)\n"
        "• <b>static</b> — your uploaded qr image is sent as it is (buyer types the amount)\n\n"
        "changes apply instantly to new payments."
    )
    kb = Markup([
        [Btn("✏️ Set UPI ID", "st_upi"), Btn("🖼 Set QR Image", "st_qr")],
        [Btn(f"🔀 Mode: {c['mode']}", "st_mode"), Btn("🗑 Clear QR", "st_qrclear")],
        [Btn("👁 Preview", "st_prev")],
        [Btn("⬅️ Back", "st_home")],
    ])
    return text, kb


async def upi_flow(client, chat_id):
    v = await cmds.ask_value(
        client, chat_id, "💳 <b>send your new upi id</b>\ne.g. <code>name@bank</code>", _upi
    )
    if v is None:
        return await _cancelled(client, chat_id)
    await db.set_setting("upi_id", v)
    text, kb = await pay_panel()
    await send_ui(client, chat_id, "✅ upi id updated.\n\n" + text, kb)


async def qr_flow(client, chat_id):
    val = None
    for _ in range(3):
        msg = await cmds.ask(
            client, chat_id,
            "🖼 <b>send your qr code</b>\n\nupload it as a <b>photo</b>, or send a direct image link.",
        )
        if msg is None:
            return await _cancelled(client, chat_id)
        t = (msg.text or "").strip()
        if msg.photo:
            val = msg.photo.file_id
            break
        if t.lower().startswith(("http://", "https://")):
            val = t
            break
        await send_ui(client, chat_id, "⚠️ that's not a photo or a link — try again.")
    if not val:
        return await send_ui(client, chat_id, "❌ too many invalid attempts. cancelled.")
    await db.set_setting("static_qr", val)
    await db.set_setting("qr_mode", "static")
    text, kb = await pay_panel()
    await send_ui(client, chat_id, "✅ qr saved & mode switched to <b>static</b> (toggle anytime).\n\n" + text, kb)


async def h_st_pay(client, cq, arg):
    text, kb = await pay_panel()
    await edit_cq(client, cq, text, kb)
    await alert(cq)


async def h_st_upi(client, cq, arg):
    await alert(cq)
    cmds.spawn(upi_flow(client, cq.message.chat.id))


async def h_st_qr(client, cq, arg):
    await alert(cq)
    cmds.spawn(qr_flow(client, cq.message.chat.id))


async def h_st_qrclear(client, cq, arg):
    await db.set_setting("static_qr", "")
    await db.set_setting("qr_mode", "dynamic")
    await alert(cq, "qr cleared — back to dynamic mode")
    await h_st_pay(client, cq, "")


async def h_st_mode(client, cq, arg):
    c = await db.payment_conf()
    new = "static" if c["mode"] == "dynamic" else "dynamic"
    if new == "static" and not c["static_qr"]:
        return await alert(cq, "upload a qr image first.", True)
    if new == "dynamic" and not c["upi_id"]:
        return await alert(cq, "set a upi id first.", True)
    await db.set_setting("qr_mode", new)
    await alert(cq, f"mode: {new}")
    await h_st_pay(client, cq, "")


async def h_st_prev(client, cq, arg):
    c = await db.payment_conf()
    qr = build_qr(c, 1.0)
    if not qr:
        return await alert(cq, "nothing to preview — set a upi id (needs the qrcode package) or upload a qr.", True)
    await alert(cq, "sending preview…")
    try:
        await client.send_photo(cq.message.chat.id, qr, caption=sc(f"👁 preview — this is what buyers get ({c['mode']} mode)"))
    except Exception as e:
        await send_ui(client, cq.message.chat.id, f"⚠️ preview failed: {esc(e)}")


# ── broadcast confirm ───────────────────────────────────────────────────────
async def h_bc_yes(client, cq, arg):
    await alert(cq, "broadcasting…")
    await edit_cq(client, cq, "📢 starting broadcast…", media=False)
    cmds.spawn(cmds.run_broadcast(client, cq.from_user.id, cq.message))


async def h_bc_no(client, cq, arg):
    cmds._pending_bc.pop(cq.from_user.id, None)
    await edit_cq(client, cq, "❌ broadcast cancelled.")
    await alert(cq)


HANDLERS = {
    "noop": h_noop, "home": h_home, "discover": h_discover, "item": h_item, "pay": h_pay, "cancel": h_cancel,
    "ask_cancel": h_ask_cancel, "st_home": h_st_home, "st_add": h_st_add, "st_list": h_st_list,
    "st_item": h_st_item, "st_units": h_st_units, "st_price": h_st_price, "st_del": h_st_del,
    "st_delok": h_st_delok, "st_pay": h_st_pay, "st_upi": h_st_upi, "st_qr": h_st_qr,
    "st_qrclear": h_st_qrclear, "st_mode": h_st_mode, "st_prev": h_st_prev,
    "bc_yes": h_bc_yes, "bc_no": h_bc_no,
}


async def on_callback(client, cq):
    action, _, arg = (cq.data or "").partition(":")
    if action in OWNER_ACTIONS and cq.from_user.id not in config.OWNER_IDS:
        return await alert(cq, "owner only.", True)
    handler = HANDLERS.get(action)
    if not handler:
        return await alert(cq)
    try:
        await handler(client, cq, arg)
    except Exception:
        log.exception("callback %s failed", cq.data)
        await alert(cq, "something went wrong, please try again.", True)


def register(app):
    app.add_handler(CallbackQueryHandler(on_callback))
