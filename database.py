"""database.py — everything that touches MongoDB (motor)."""
import time

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import config

_client = AsyncIOMotorClient(config.MONGO_URI)
db = _client[config.DB_NAME]

users = db.users          # every user who pressed /start (for broadcast)
products = db.products    # name, price, warranty
units = db.units          # individual sellable units: available -> reserved -> sold
sessions = db.sessions    # purchase sessions: created -> pending -> paid / expired / cancelled
payments = db.payments    # log of every credit SMS (dedupe + audit)
settings = db.settings    # live-editable settings (UPI id, QR image, QR mode)

AFTER = ReturnDocument.AFTER


def now() -> float:
    return time.time()


def oid(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


async def init():
    await units.create_index([("product_id", 1), ("status", 1)])
    await units.create_index("session")
    await sessions.create_index([("status", 1), ("expires_at", 1)])
    await sessions.create_index("user_id")
    # Two *pending* sessions can never share the same amount -> SMS can be matched to exactly one buyer
    await sessions.create_index(
        "amount_paise", unique=True, partialFilterExpression={"status": "pending"}
    )


# ── settings (changeable from the bot) ──────────────────────────────────────
async def get_setting(key, default=None):
    d = await settings.find_one({"_id": key})
    return d["value"] if d else default


async def set_setting(key, value):
    await settings.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)


async def payment_conf():
    """Current payment setup: bot-saved values win, config.py values are the defaults."""
    return {
        "upi_id": await get_setting("upi_id", config.UPI_ID),
        "static_qr": await get_setting("static_qr", config.STATIC_QR),
        "mode": await get_setting("qr_mode", "dynamic"),  # dynamic = generated from UPI id, static = uploaded image
    }


# ── users ───────────────────────────────────────────────────────────────────
async def add_user(u):
    await users.update_one(
        {"_id": u.id},
        {
            "$set": {"name": u.first_name, "username": u.username, "blocked": False},
            "$setOnInsert": {"joined": now()},
        },
        upsert=True,
    )


async def user_ids():
    return [d["_id"] async for d in users.find({"blocked": {"$ne": True}}, {"_id": 1})]


async def mark_blocked(uid):
    await users.update_one({"_id": uid}, {"$set": {"blocked": True}})


# ── products ────────────────────────────────────────────────────────────────
async def add_product(name, price, warranty):
    res = await products.insert_one(
        {"name": name, "price": price, "warranty": warranty, "created": now()}
    )
    return res.inserted_id


async def get_product(pid):
    return await products.find_one({"_id": pid})


async def list_products(skip, limit):
    return await products.find().sort("created", 1).skip(skip).limit(limit).to_list(limit)


async def count_products():
    return await products.count_documents({})


async def set_price(pid, price):
    await products.update_one({"_id": pid}, {"$set": {"price": price}})


async def delete_product(pid):
    await products.delete_one({"_id": pid})
    await units.delete_many({"product_id": pid, "status": "available"})


# ── units (the actual stock) ────────────────────────────────────────────────
async def add_units(pid, datas):
    docs = [
        {"product_id": pid, "data": d, "status": "available", "added": now()} for d in datas
    ]
    if docs:
        await units.insert_many(docs)
    return len(docs)


async def stock_of(pid):
    return await units.count_documents({"product_id": pid, "status": "available"})


async def sold_of(pid):
    return await units.count_documents({"product_id": pid, "status": "sold"})


async def stock_counts(pids):
    pipeline = [
        {"$match": {"product_id": {"$in": pids}, "status": "available"}},
        {"$group": {"_id": "$product_id", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in units.aggregate(pipeline)}


async def reserve_unit(pid, sid):
    """Atomically lock one unit for a session so two buyers can never get the same one."""
    return await units.find_one_and_update(
        {"product_id": pid, "status": "available"},
        {"$set": {"status": "reserved", "session": sid, "reserved_at": now()}},
        return_document=AFTER,
    )


async def release_units(sid):
    await units.update_many(
        {"session": sid, "status": "reserved"},
        {"$set": {"status": "available"}, "$unset": {"session": ""}},
    )


async def sell_unit(sid, uid):
    return await units.find_one_and_update(
        {"session": sid, "status": "reserved"},
        {"$set": {"status": "sold", "buyer": uid, "sold_at": now()}},
        return_document=AFTER,
    )


# ── sessions ────────────────────────────────────────────────────────────────
async def create_session(uid, product):
    res = await sessions.insert_one(
        {
            "user_id": uid,
            "chat_id": uid,
            "product_id": product["_id"],
            "product_name": product["name"],
            "warranty": product["warranty"],
            "price": product["price"],
            "status": "created",
            "created": now(),
        }
    )
    return res.inserted_id


async def get_session(sid):
    return await sessions.find_one({"_id": sid})


async def user_pending(uid):
    return await sessions.find({"user_id": uid, "status": "pending"}).to_list(10)


async def activate_session(sid, base_paise, unit_id):
    """created -> pending with a unique amount (base, base+1p, base+2p …)."""
    for off in range(config.MAX_PAISE_OFFSET + 1):
        try:
            return await sessions.find_one_and_update(
                {"_id": sid, "status": "created"},
                {
                    "$set": {
                        "status": "pending",
                        "amount_paise": base_paise + off,
                        "unit_id": unit_id,
                        "started": now(),
                        "expires_at": now() + config.SESSION_TIMEOUT,
                    }
                },
                return_document=AFTER,
            )
        except DuplicateKeyError:
            continue
    return None


async def set_qr_msg(sid, chat_id, msg_id):
    await sessions.update_one({"_id": sid}, {"$set": {"qr_chat_id": chat_id, "qr_msg_id": msg_id}})


async def claim_payment(amount_paise):
    """Atomically flip the matching pending session to paid (so a payment is used only once)."""
    return await sessions.find_one_and_update(
        {"status": "pending", "amount_paise": amount_paise, "expires_at": {"$gt": now()}},
        {"$set": {"status": "paid", "paid_at": now()}},
        return_document=AFTER,
    )


async def cancel_session(sid, uid):
    s = await sessions.find_one_and_update(
        {"_id": sid, "user_id": uid, "status": {"$in": ["created", "pending"]}},
        {"$set": {"status": "cancelled"}},
        return_document=AFTER,
    )
    if s:
        await release_units(sid)
    return s


async def expire_due():
    """Expire pending sessions past their deadline; returns them. Also sweeps stale 'created' ones."""
    out = []
    while True:
        s = await sessions.find_one_and_update(
            {"status": "pending", "expires_at": {"$lte": now()}},
            {"$set": {"status": "expired"}},
            return_document=AFTER,
        )
        if not s:
            break
        await release_units(s["_id"])
        out.append(s)
    await sessions.delete_many({"status": "created", "created": {"$lt": now() - config.SESSION_TIMEOUT}})
    return out


# ── payments log ────────────────────────────────────────────────────────────
async def log_payment(key, data):
    """False if we've already seen this SMS (webhook retry / duplicate)."""
    try:
        await payments.insert_one({"_id": key, "at": now(), **data})
        return True
    except DuplicateKeyError:
        return False


# ── stats ───────────────────────────────────────────────────────────────────
async def stats():
    agg = await sessions.aggregate(
        [{"$match": {"status": "paid"}}, {"$group": {"_id": None, "t": {"$sum": "$amount_paise"}}}]
    ).to_list(1)
    return {
        "users": await users.count_documents({}),
        "products": await products.count_documents({}),
        "available": await units.count_documents({"status": "available"}),
        "sold": await units.count_documents({"status": "sold"}),
        "revenue": (agg[0]["t"] / 100) if agg else 0,
    }
