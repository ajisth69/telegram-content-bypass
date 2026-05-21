import asyncio
from pyrogram import Client

async def main():
    print("\n" + "=" * 40)
    print("   Telegram Userbot Session Generator")
    print("   dev: @letmesolo_her")
    print("=" * 40 + "\n")

    print("Note: You can get your own API ID & API Hash from https://my.telegram.org")
    
    api_id_input = input("Enter API_ID [Press Enter for official Android default]: ").strip()
    if api_id_input:
        api_id = int(api_id_input)
    else:
        api_id = 4

    api_hash_input = input("Enter API_HASH [Press Enter for official Android default]: ").strip()
    if api_hash_input:
        api_hash = api_hash_input
    else:
        api_hash = "014b35b6184100b085b0d0572f9b5103"

    print(f"\nUsing API_ID: {api_id}")
    print(f"Using API_HASH: {api_hash}\n")

    # init client in memory so it doesn't create local .session files
    async with Client(
        name="session_gen",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    ) as app:
        session_str = await app.export_session_string()

        print("\n" + "-" * 60)
        print(" SUCCESS! Copy the session string below:")
        print("-" * 60)
        print(session_str)
        print("-" * 60)
        print("\nKeep it safe. Set this as SESSION_STRING on Render.\n")

if __name__ == "__main__":
    asyncio.run(main())
