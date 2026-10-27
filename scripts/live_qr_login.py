import asyncio
import os
import subprocess
import sys
import yaml
import qrcode
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

API_ID = 36752148
API_HASH = "0f91ef066beb9c6ba68a0bbdee283f4b"
QR_PATH = "/Users/mac/.gemini/antigravity/brain/e92b4958-b0c1-453a-a7bd-862b84739abb/telegram_qr.png"
SESSION_PATH = "scripts/tg_qr_session"
PWD_FILE = "/Users/mac/Desktop/car/.2fa_pwd"

async def main():
    pwd = sys.argv[1] if len(sys.argv) > 1 else None
    if not pwd and os.path.exists(PWD_FILE):
        with open(PWD_FILE) as f:
            pwd = f.read().strip()
            
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("Starting QR login loop...")
        qr = await client.qr_login()
        
        while not await client.is_user_authorized():
            # Save QR to PNG
            img = qrcode.make(qr.url)
            img.save(QR_PATH)
            
            # Open directly in macOS Preview
            try:
                subprocess.run(["open", QR_PATH], check=False)
            except Exception:
                pass
                
            print(f"\n[QR CODE UPDATED] Valid for ~30s. File: {QR_PATH}")
            print(f"URL: {qr.url}")
            
            try:
                # wait until scan
                await qr.wait()
                break
            except errors.SessionPasswordNeededError:
                print("2FA_PASSWORD_NEEDED")
                if pwd:
                    print("Submitting 2FA password...")
                    await client.sign_in(password=pwd)
                    break
                else:
                    print("Please provide 2FA password to complete login!")
                    break
            except asyncio.TimeoutError:
                print("QR code expired (30s limit). Recreating fresh QR code...")
                await qr.recreate()
            except Exception as e:
                print(f"Wait error: {e}. Recreating...")
                await qr.recreate()
                
    if await client.is_user_authorized():
        print("LOGIN_SUCCESS!")
        session_str = StringSession.save(client.session)
        print("\n=== SESSION STRING GENERATED ===")
        print(session_str)
        print("================================\n")
        
        with open("/Users/mac/Desktop/car/.session_string", "w") as f:
            f.write(session_str)
            
        config_path = "/Users/mac/Desktop/car/config/config.yaml"
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if "parsers" in cfg and "telegram" in cfg["parsers"]:
                cfg["parsers"]["telegram"]["session_string"] = session_str
                cfg["parsers"]["telegram"]["api_id"] = API_ID
                cfg["parsers"]["telegram"]["api_hash"] = API_HASH
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
                print(f"Updated {config_path} with new session_string, api_id, and api_hash!")
                
        if os.path.exists(PWD_FILE):
            os.remove(PWD_FILE)
    else:
        print("LOGIN_INCOMPLETE_WAITING_2FA")
        
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
