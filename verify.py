"""verify.py — Deku SMS webhook + regex payment verification + auto delivery + expiry loop.

Point Deku's forwarding URL to:
    https://<your-domain>/api/payment/sms-webhook?secret=seishiro
"""
import asyncio
import hashlib
import hmac
import html
import json
import logging
import re

from aiohttp import web

import cmds
import config
import database as db

log = logging.getLogger("verify")

# ── regex ───────────────────────────────────────────────────────────────────
AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
CREDIT_RE = re.compile(r"\b(?:credited|received|deposited|paid you|sent you)\b", re.I)
DEBIT_RE = re.compile(r"\b(?:debited|withdrawn|spent|purchase|sent to|paid to|transferred to)\b", re.I)
REF_RE = re.compile(r"(?:upi\s*ref(?:\s*no)?|ref(?:erence)?\s*(?:no|id)?|utr|txn\s*(?:id|no)?)[\s:.#-]*([A-Za-z0-9]{8,})", re.I)
# "Avl Bal Rs 1,234.00" must never be mistaken for the payment amount
BAL_RE = re.compile(r"(?:avl\.?\s*|available\s*|total\s*)?bal(?:ance)?\.?[^0-9₹]{0,15}(?:₹|rs\.?|inr)?\s*[0-9][0-9,]*(?:\.[0-9]+)?", re.I)

TEXT_KEYS = ("text", "message", "body", "sms", "content", "msg", "data")
SENDER_KEYS = ("address", "sender", "from", "number", "phone")


def parse_sms(text: str):
    """Return {'amount_paise', 'ref'} for a *credit* SMS, otherwise None."""
    clean = BAL_RE.sub(" ", text)
    if not CREDIT_RE.search(clean) or DEBIT_RE.search(clean):
        return None
    m = AMOUNT_RE.search(clean)
    if not m:
        return None
    paise = int(round(float(m.group(1).replace(",", "")) * 100))
    ref = REF_RE.search(clean)
    return {"amount_paise": paise, "ref": ref.group(1) if ref else None}


def extract_text(payload) -> str:
    """Deku/forwarders use different JSON shapes — dig the SMS body out of any of them."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for k in TEXT_KEYS:
            if k in payload:
                t = extract_text(payload[k])
                if t.strip():
                    return t
        return " ".join(extract_text(v) for v in payload.values())
    if isinstance(payload, list):
        return " ".join(extract_text(v) for v in payload)
    return ""


def extract_sender(payload) -> str:
    if isinstance(payload, dict):
        for k in SENDER_KEYS:
            if isinstance(payload.get(k), str):
                return payload[k]
    return ""


async def _read_payload(request: web.Request):
    if request.method == "GET":
        return {k: v for k, v in request.query.items() if k != "secret"}
    try:
        if request.content_type == "application/json":
            return await request.json()
        if request.content_type in ("application/x-www-form-urlencoded", "multipart/form-data"):
            return dict(await request.post())
        raw = await request.text()
    except Exception:
        return ""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


# ── telegram helpers ────────────────────────────────────────────────────────
async def edit_msg(bot, chat_id, msg_id, text):
    """Swap the payment message to a random image + new caption (also removes the stale QR & buttons)."""
    await cmds.edit_ui(bot, chat_id, msg_id, text)


async def notify_owners(bot, text):
    for oid in config.OWNER_IDS:
        try:
            await cmds.send_ui(bot, oid, text)
        except Exception:
            pass


async def deliver(bot, s):
    """Payment verified -> hand the item to the buyer."""
    uid = s["user_id"]
    amount = s["amount_paise"] / 100
    await edit_msg(bot, s.get("qr_chat_id"), s.get("qr_msg_id"), "✅ <b>payment received!</b>")

    unit = await db.sell_unit(s["_id"], uid)
    if not unit:  # should never happen (unit is reserved) but never leave a paying user hanging
        await cmds.send_ui(
            bot, uid, f"⚠️ payment received but i couldn't fetch your item. contact {config.SUPPORT} — you will be sorted."
        )
        await notify_owners(bot, f"🚨 paid but no reserved unit!\nuser: <code>{uid}</code>\nitem: {html.escape(s['product_name'])}\namount: {config.CURRENCY}{amount:.2f}")
        return

    text = (
        "✅ <b>payment verified!</b>\n\n"
        f"📦 <b>{html.escape(s['product_name'])}</b>\n"
        f"🛡 warranty: <b>{html.escape(s['warranty'])}</b>\n"
        f"💰 paid: <b>{config.CURRENCY}{amount:.2f}</b>\n\n"
        f"🔐 <b>your details:</b>\n<code>{html.escape(unit['data'])}</code>\n\n"
        "thanks for shopping! 🛍"
    )
    # details inside <code> stay exactly as stored (never small-capsed)
    await cmds.send_ui(bot, uid, text, cmds.Markup([[cmds.Btn("🔍 Discover More", "discover:0")]]))
    left = await db.stock_of(s["product_id"])
    await notify_owners(
        bot,
        f"💰 <b>new sale!</b>\n📦 {html.escape(s['product_name'])} — {config.CURRENCY}{amount:.2f}\n"
        f"👤 <a href='tg://user?id={uid}'>{uid}</a>\n📊 left in stock: {left}",
    )


# ── webhook ─────────────────────────────────────────────────────────────────
async def sms_webhook(request: web.Request):
    if not hmac.compare_digest(request.query.get("secret", ""), config.WEBHOOK_SECRET):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    payload = await _read_payload(request)
    text = extract_text(payload).strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty"}, status=400)

    if config.ALLOWED_SENDERS and not re.search(config.ALLOWED_SENDERS, extract_sender(payload) + " " + text, re.I):
        return web.json_response({"ok": True, "matched": False, "reason": "sender"})

    info = parse_sms(text)
    if not info:
        return web.json_response({"ok": True, "matched": False, "reason": "not_a_credit"})

    key = info["ref"] or hashlib.sha256(text.encode()).hexdigest()
    if not await db.log_payment(key, {"amount_paise": info["amount_paise"], "sms": text}):
        return web.json_response({"ok": True, "matched": False, "reason": "duplicate"})

    bot = request.app["bot"]
    session = await db.claim_payment(info["amount_paise"])
    if not session:
        await notify_owners(
            bot,
            f"⚠️ credit of <b>{config.CURRENCY}{info['amount_paise'] / 100:.2f}</b> received but no open session matches "
            "(late / wrong amount / not a shop payment).",
        )
        return web.json_response({"ok": True, "matched": False, "reason": "no_session"})

    asyncio.create_task(deliver(bot, session))
    return web.json_response({"ok": True, "matched": True})


async def start_web(bot):
    app = web.Application()
    app["bot"] = bot
    app.router.add_route("*", config.WEBHOOK_PATH, sms_webhook)
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT).start()
    log.info("Webhook listening on %s:%s%s", config.WEB_HOST, config.WEB_PORT, config.WEBHOOK_PATH)
    return runner


# ── 15-minute session expiry ────────────────────────────────────────────────
async def expiry_loop(bot):
    while True:
        try:
            for s in await db.expire_due():
                await edit_msg(
                    bot, s.get("qr_chat_id"), s.get("qr_msg_id"),
                    "⌛ <b>session expired</b>\nthis payment window has closed.",
                )
                try:
                    await cmds.send_ui(
                        bot, s["user_id"],
                        "⌛ your payment session expired (15 min).\ntap /start to try again. "
                        f"if you already paid, contact {config.SUPPORT}.",
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("expiry loop error")
        await asyncio.sleep(15)
