"""main.py — starts pyrogram, the webhook server and the expiry loop."""
import asyncio
import logging

from pyrogram import Client, idle
from pyrogram.enums import ParseMode

import cmds
import config
import database
import inline
import verify

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")


async def main():
    await database.init()

    # created inside the running loop on purpose (pyrogram grabs the loop at construction)
    app = Client(
        "merchant_bot",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
        parse_mode=ParseMode.HTML,
        workers=16,  # owner conversations hold a worker while waiting for a reply
    )
    cmds.register(app)
    inline.register(app)

    await app.start()
    runner = await verify.start_web(app)
    expiry = asyncio.create_task(verify.expiry_loop(app))
    logging.info("Merchant bot is up.")

    await idle()

    expiry.cancel()
    await runner.cleanup()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
