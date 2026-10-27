import asyncio
import os
import sys
import qrcode
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

API_ID = 36752148
API_HASH = "0f91ef066beb9c6ba68a0bbdee283f4b"
QR_PATH = "/Users/mac/.gemini/antigravity/brain/e92b4958-b0c1-453a-a7bd-862b84739abb/telegram_qr.png"

async def login_via_qr():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("INITIATING_QR_LOOP")
        qr_login = await client.qr_login()
        
        logged_in = False
        attempts = 0
        while not logged_in and attempts < 10:
            attempts += 1
            # Save QR code image
            img = qrcode.make(qr_login.url)
            img.save(QR_PATH)
            print(f"\n[Attempt {attempts}] QR updated: {QR_PATH}")
            print(f"QR_URL: {qr_login.url}")
            
            # Print ascii QR
            qr = qrcode.QRCode()
            qr.add_data(qr_login.url)
            qr.print_ascii(invert=True)
            
            print(f"Waiting for scan (Attempt {attempts}/10)...")
            try:
                # Wait until token expiration or max 120s
                await qr_login.wait(timeout=120)
                logged_in = True
            except errors.SessionPasswordNeededError:
                print("2FA_PASSWORD_NEEDED")
                # If 2FA password is required
                pwd = input("Enter 2FA password: ").strip()
                await client.sign_in(password=pwd)
                logged_in = True
            except asyncio.TimeoutError:
                print("Token expired. Refreshing QR code...")
                await qr_login.recreate()
            except Exception as e:
                print(f"Error during scan wait: {e}")
                await qr_login.recreate()
                
    if await client.is_user_authorized():
        print("LOGIN_SUCCESS")
        session_str = client.session.save()
        print("\nSESSION_STRING_START")
        print(session_str)
        print("SESSION_STRING_END\n")
        
        with open("/Users/mac/Desktop/car/.session_string", "w") as f:
            f.write(session_str)
            
        print("Session string successfully saved to /Users/mac/Desktop/car/.session_string")
    else:
        print("LOGIN_FAILED")
        
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(login_via_qr())
