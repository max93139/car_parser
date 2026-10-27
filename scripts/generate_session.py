import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 36752148
API_HASH = "0f91ef066beb9c6ba68a0bbdee283f4b"
PHONE = "+380635135971"

async def generate_session():
    print(f"Connecting to Telegram for {PHONE}...")
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print(f"Sending login code to {PHONE}...")
        sent_code = await client.send_code_request(PHONE)
        print("CODE_SENT_SUCCESS")
        
        # Read code from stdin
        code = input("Enter confirmation code from Telegram: ").strip()
        
        try:
            await client.sign_in(PHONE, code, phone_code_hash=sent_code.phone_code_hash)
        except Exception as e:
            if "password" in str(e).lower() or "two-step" in str(e).lower():
                password = input("Enter your 2FA password: ").strip()
                await client.sign_in(password=password)
            else:
                raise e
                
    session_str = client.session.save()
    print("\nSESSION_STRING_START")
    print(session_str)
    print("SESSION_STRING_END\n")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(generate_session())
