import asyncio
from dotenv import load_dotenv
load_dotenv()

import os
from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ.get("API_ID", "4"))
API_HASH = os.environ.get("API_HASH", "014b35b6184100b085b0d0572f9b5103")

bot = Client(
    "test_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

@bot.on_message(filters.private)
async def catch_all(client, message):
    print(f"GOT MESSAGE: {message.text} from {message.from_user.id}")
    await message.reply("I'm alive!")

async def main():
    print("Starting test bot...")
    await bot.start()
    me = await bot.get_me()
    print(f"Bot: {me.first_name} (@{me.username})")
    print("Waiting for messages...")
    await idle()
    await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
